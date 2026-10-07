#!/usr/bin/env python3
"""Headless logic-agent runner for a CEO-agent operating system.

Runs another project's Claude Code agent non-interactively (`claude -p`) from that
project's own directory, so each project keeps its own CLAUDE.md, settings, memory
and permission boundary. The CEO agent (or a scheduler) is the only caller.

Single source of truth: config/logic_agents.yaml (see templates/logic_agents.yaml).

Rules (mirrored in the registry header):
  - Permissions = the target directory's .claude/settings*.json allowlist
    + registry allowed_tools (only while permissions_status == "confirmed")
    + registry denied_tools (always).
    `--permission-mode` is never passed. A headless run cannot answer permission
    prompts, so any tool outside the allowlist is denied automatically. That
    behaviour is what protects critical zones; do not bypass it.
  - Sessions: resume the agent's last headless session id. Rotate to a fresh
    session when its transcript grows beyond rotate_mb (the preamble then tells
    the agent to read its handoff file first). Never `--continue`: it would pick
    up whatever conversation was opened last in that directory, including a live
    one the owner is using.
  - One process per agent at a time (non-blocking file lock).
  - Every call is appended to <state dir>/logic_agent_calls.jsonl.

Usage:
  python3 scripts/logic_agent.py strategy_a -p "Summarize the last run in 5 lines"
  python3 scripts/logic_agent.py ops_bot --prompt-file brief.md -m sonnet -t 1200
  echo "..." | python3 scripts/logic_agent.py ops_bot
  python3 scripts/logic_agent.py strategy_a -p "..." --dry-run    # print command + preamble only
  python3 scripts/logic_agent.py --status                          # sessions and transcript sizes
  python3 scripts/logic_agent.py ops_bot -p "..." --allowed-tools "Bash(python3 *)"  # per-call extra allow (logged)

Paths resolve relative to the project root (the parent of scripts/).
Environment overrides: LOGIC_AGENTS_REGISTRY, LOGIC_AGENTS_STATE_DIR, CLAUDE_CMD.
Dependencies: Python 3.9+, PyYAML. Check flag names against `claude --help` for your CLI version.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PROJECTS_DIR = Path(os.path.expanduser("~/.claude/projects"))
FALLBACK_DEFAULTS = {"model": "opus", "effort": None, "timeout_sec": 900, "rotate_mb": 3}
_RESUME_MISSING = re.compile(r"no conversation found|session.*not found|could not find session", re.I)


class LogicAgentError(Exception):
    pass


# ---------------------------------------------------------------- paths
def registry_path() -> Path:
    return Path(os.environ.get("LOGIC_AGENTS_REGISTRY") or PROJECT_ROOT / "config" / "logic_agents.yaml")


def state_dir() -> Path:
    return Path(os.environ.get("LOGIC_AGENTS_STATE_DIR") or PROJECT_ROOT / "state")


def default_claude_cmd() -> str:
    return os.environ.get("CLAUDE_CMD") or "claude"


# ---------------------------------------------------------------- registry
def load_registry(path: Optional[Path] = None) -> dict:
    p = Path(path or registry_path())
    with open(p) as f:
        reg = yaml.safe_load(f) or {}
    if not isinstance(reg, dict) or "agents" not in reg:
        raise LogicAgentError(f"registry has no 'agents' section: {p}")
    return reg


def _resolve_path(raw: str) -> str:
    p = Path(os.path.expanduser(str(raw or "")))
    return str(p if p.is_absolute() else (PROJECT_ROOT / p).resolve())


def resolve(name: str, reg: dict, force_mode: bool = False) -> dict:
    """Agent spec merged with defaults. Refuses non-headless modes unless force_mode."""
    agents = reg.get("agents") or {}
    if name not in agents:
        raise LogicAgentError(f"unknown agent '{name}' (registered: {', '.join(agents) or 'none'})")
    spec = dict(agents[name] or {})
    d = reg.get("defaults") or {}
    for key, fallback in FALLBACK_DEFAULTS.items():
        spec.setdefault(key, d.get(key, fallback))
    spec["name"] = name
    spec["path"] = _resolve_path(spec.get("path", ""))
    mode = spec.get("mode", "headless")
    if mode != "headless" and not force_mode:
        why = ("the owner uses a live session there; two processes on one transcript"
               if mode == "live" else "it runs inside the CEO session as a subagent")
        raise LogicAgentError(f"'{name}' has mode={mode}: headless calls refused ({why}). Override: --force-mode.")
    if not Path(spec["path"]).is_dir():
        raise LogicAgentError(f"working directory not found: {spec['path']}")
    confirmed = reg.get("permissions_status") == "confirmed"
    spec["permissions_confirmed"] = confirmed
    spec["allowed_tools"] = (list(d.get("allowed_tools") or []) + list(spec.get("allowed_tools") or [])
                             if confirmed else [])
    spec["denied_tools"] = list(d.get("denied_tools") or []) + list(spec.get("denied_tools") or [])
    spec["preamble_extra"] = list(d.get("preamble_extra") or []) + list(spec.get("preamble_extra") or [])
    return spec


# ---------------------------------------------------------------- sessions
def encode_project_path(path: str) -> str:
    """Claude Code's ~/.claude/projects folder name: every char other than [A-Za-z0-9-] becomes '-'."""
    return re.sub(r"[^A-Za-z0-9-]", "-", path)


def transcript_path(project_path: str, session_id: str) -> Path:
    return CLAUDE_PROJECTS_DIR / encode_project_path(project_path) / f"{session_id}.jsonl"


def transcript_size_mb(project_path: str, session_id: str) -> Optional[float]:
    p = transcript_path(project_path, session_id)
    return round(p.stat().st_size / 1_000_000, 3) if p.is_file() else None


def load_sessions(path: Optional[Path] = None) -> dict:
    p = Path(path or state_dir() / "logic_agent_sessions.json")
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except json.JSONDecodeError:
        return {}


def save_sessions(state: dict, path: Optional[Path] = None) -> None:
    p = Path(path or state_dir() / "logic_agent_sessions.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(p)


def decide_session(spec: dict, state: dict, fresh: bool = False,
                   resume: Optional[str] = None) -> tuple[Optional[str], str]:
    """(session_id to resume or None for a new session, reason)."""
    if resume:
        return resume, "explicit"
    if fresh:
        return None, "fresh"
    last = (state.get(spec["name"]) or {}).get("session_id")
    if not last:
        return None, "first"
    size = transcript_size_mb(spec["path"], last)
    if size is None:
        return None, "transcript_missing"
    if size > float(spec["rotate_mb"]):
        return None, f"rotate({size}MB>{spec['rotate_mb']}MB)"
    return last, f"resume({size}MB)"


# ---------------------------------------------------------------- command
def build_preamble(spec: dict, rotated: bool) -> str:
    lines = [
        f"[headless call from the CEO agent] You are the {spec['name']} agent ({spec.get('role', '')}). "
        f"Working directory: {spec['path']}.",
        "Your final answer is your report: conclusion, key figures and commit hashes in the first 5 lines, details below.",
        f"At session start or after rotation, read the top handoff block of {spec.get('handoff', 'HANDOFF.md')} first.",
        "Do not attempt tools outside your permissions; list what you needed at the end as 'Needed permission: <rule>'.",
        "Never: revert data or state with git checkout/restore/stash/reset; edit critical-zone files without "
        "owner approval quoted verbatim in this prompt; invent numbers (write 'not measurable' instead).",
    ]
    lines += [str(x) for x in spec.get("preamble_extra") or []]
    if rotated:
        lines.append("NOTE: session rotated (fresh context). Read the handoff block and `git log -5` before working.")
    return "\n".join(lines)


def build_cmd(spec: dict, session_id: Optional[str], model: str, effort: Optional[str],
              extra_allowed: Optional[list] = None, claude_cmd: Optional[str] = None,
              rotated: bool = False) -> list:
    cmd = [claude_cmd or default_claude_cmd(), "-p", "--output-format", "json", "--model", model]
    if effort:
        cmd += ["--effort", str(effort)]
    cmd += ["--append-system-prompt", build_preamble(spec, rotated)]
    if session_id:
        cmd += ["--resume", session_id]
    allowed = list(spec.get("allowed_tools") or []) + list(extra_allowed or [])
    denied = list(spec.get("denied_tools") or [])
    if allowed:
        cmd += ["--allowedTools", *allowed]
    if denied:
        cmd += ["--disallowedTools", *denied]
    return cmd


# ---------------------------------------------------------------- run
def _child_env() -> dict:
    """Drop CLAUDECODE / CLAUDE_CODE_* so a call made from inside a session is not blocked as nested."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}


def _run(cmd: list, prompt: str, cwd: str, timeout: int) -> tuple[int, str, str, int]:
    """Run in a new process group; on timeout kill the whole group (ssh/node children too) and return rc 124."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=cwd, env=_child_env(), start_new_session=True)
    try:
        out, err = p.communicate(input=prompt, timeout=timeout)
        return p.returncode, out or "", err or "", int((time.monotonic() - t0) * 1000)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return 124, out or "", f"timeout {timeout}s\n{err or ''}", int((time.monotonic() - t0) * 1000)


def _parse(out: str) -> dict:
    if not (out or "").strip():
        return {}
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return {"result": out.strip()}
    return parsed if isinstance(parsed, dict) else {"result": out.strip()}


class _AgentLock:
    """Per-agent non-blocking lock: two processes must never write to one transcript."""

    def __init__(self, name: str, lock_dir: Path):
        self.path = Path(lock_dir) / f"logic_agent_{name}.lock"
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise LogicAgentError(f"'{self.path.stem}' is already running (lock {self.path}); retry when it ends")
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_call_log(rec: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def call(name: str, prompt: str, model: Optional[str] = None, effort: Optional[str] = None,
         timeout: Optional[int] = None, fresh: bool = False, resume: Optional[str] = None,
         extra_allowed: Optional[list] = None, force_mode: bool = False, dry_run: bool = False,
         registry: Optional[Path] = None, sessions_path: Optional[Path] = None,
         calls_log_path: Optional[Path] = None, claude_cmd: Optional[str] = None) -> dict:
    """One call. If the resumed session no longer exists, retry once with a fresh session.
    Failures are reported via is_error/rc rather than exceptions (registry/mode/lock errors still raise)."""
    if not (prompt or "").strip():
        raise LogicAgentError("empty prompt")
    spec = resolve(name, load_registry(registry), force_mode=force_mode)
    model = model or spec["model"]
    effort = effort or spec.get("effort")
    timeout = int(timeout or spec["timeout_sec"])
    sessions_path = Path(sessions_path or state_dir() / "logic_agent_sessions.json")
    calls_log_path = Path(calls_log_path or state_dir() / "logic_agent_calls.jsonl")

    session_id, reason = decide_session(spec, load_sessions(sessions_path), fresh=fresh, resume=resume)
    rotated = reason.startswith("rotate")
    cmd = build_cmd(spec, session_id, model, effort, extra_allowed, claude_cmd, rotated=rotated)
    base = {"agent": name, "cwd": spec["path"], "model": model, "effort": effort, "timeout": timeout,
            "session_reason": reason, "permissions_confirmed": spec["permissions_confirmed"],
            "extra_allowed": list(extra_allowed or [])}
    if dry_run:
        return {**base, "cmd": cmd, "preamble": build_preamble(spec, rotated), "dry_run": True,
                "result": "", "session_id": session_id or "", "cost_usd": 0.0, "duration_ms": 0,
                "is_error": False, "rc": 0, "rotated": rotated, "resumed": bool(session_id)}

    with _AgentLock(name, sessions_path.parent):
        rc, out, err, ms = _run(cmd, prompt, spec["path"], timeout)
        parsed = _parse(out)
        failed = rc != 0 or bool(parsed.get("is_error"))
        if failed and session_id and _RESUME_MISSING.search(f"{err}\n{out}"):
            reason += "->fresh(resume failed)"
            session_id, rotated = None, True
            cmd = build_cmd(spec, None, model, effort, extra_allowed, claude_cmd, rotated=True)
            rc, out, err, ms2 = _run(cmd, prompt, spec["path"], timeout)
            ms += ms2
            parsed = _parse(out)
            base["session_reason"] = reason
        text = (parsed.get("result") or "").strip()
        new_sid = parsed.get("session_id") or session_id or ""
        is_error = rc != 0 or bool(parsed.get("is_error")) or not text
        resp = {**base, "result": text if text else (err.strip()[:800] if is_error else ""),
                "session_id": new_sid, "cost_usd": float(parsed.get("total_cost_usd") or 0.0),
                "duration_ms": int(parsed.get("duration_ms") or ms), "is_error": is_error, "rc": rc,
                "rotated": rotated, "resumed": bool(session_id), "stderr": (err or "")[:800]}
        if new_sid and not is_error:
            state = load_sessions(sessions_path)  # re-read inside the lock; keep other agents' records
            prev = state.get(name) or {}
            state[name] = {"session_id": new_sid,
                           "started": prev.get("started") if session_id else _now(),
                           "last_call": _now(),
                           "calls": (prev.get("calls", 0) if session_id else 0) + 1,
                           "model": model}
            save_sessions(state, sessions_path)
    _append_call_log({"ts": _now(),
                      **{k: resp[k] for k in ("agent", "model", "effort", "session_id", "session_reason",
                                              "permissions_confirmed", "extra_allowed", "rc", "is_error",
                                              "cost_usd", "duration_ms", "rotated", "resumed")},
                      "prompt_head": prompt.strip()[:160], "result_len": len(text),
                      "error": (err or "")[:300] if is_error else ""}, calls_log_path)
    return resp


def status(registry: Optional[Path] = None, sessions_path: Optional[Path] = None) -> list:
    reg = load_registry(registry)
    state = load_sessions(sessions_path)
    rows = []
    for name, a in (reg.get("agents") or {}).items():
        a = a or {}
        sid = (state.get(name) or {}).get("session_id")
        size = transcript_size_mb(_resolve_path(a.get("path", "")), sid) if sid else None
        rows.append({"agent": name, "mode": a.get("mode", "headless"), "role": a.get("role", ""),
                     "session_id": sid, "calls": (state.get(name) or {}).get("calls", 0),
                     "last_call": (state.get(name) or {}).get("last_call"), "transcript_mb": size,
                     "permissions_status": reg.get("permissions_status")})
    return rows


# ---------------------------------------------------------------- CLI
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Headless logic-agent call (registry: config/logic_agents.yaml)")
    ap.add_argument("agent", nargs="?", help="key under 'agents' in the registry")
    ap.add_argument("-p", "--prompt", help="instruction (else --prompt-file or stdin)")
    ap.add_argument("--prompt-file", help="instruction file")
    ap.add_argument("-m", "--model")
    ap.add_argument("-e", "--effort")
    ap.add_argument("-t", "--timeout", type=int, help="seconds")
    ap.add_argument("--fresh", action="store_true", help="start a new session (agent reads its handoff first)")
    ap.add_argument("--resume", help="resume a specific session id")
    ap.add_argument("--allowed-tools", action="append", default=None,
                    help="extra allow rule for this call only (repeatable; logged)")
    ap.add_argument("--force-mode", action="store_true", help="call a live/in_session agent anyway (not advised)")
    ap.add_argument("--dry-run", action="store_true", help="print the command and preamble, do not run")
    ap.add_argument("--status", action="store_true", help="list agents, sessions and transcript sizes")
    ap.add_argument("--json", action="store_true", help="print the full response as JSON")
    a = ap.parse_args(argv)

    if a.status:
        for row in status():
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if not a.agent:
        ap.error("agent required (or --status)")

    prompt = a.prompt
    if not prompt and a.prompt_file:
        prompt = Path(a.prompt_file).read_text()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    if not (prompt or "").strip():
        ap.error("no prompt (-p / --prompt-file / stdin)")

    try:
        resp = call(a.agent, prompt, model=a.model, effort=a.effort, timeout=a.timeout, fresh=a.fresh,
                    resume=a.resume, extra_allowed=a.allowed_tools, force_mode=a.force_mode,
                    dry_run=a.dry_run)
    except LogicAgentError as e:
        print(f"[logic_agent] {e}", file=sys.stderr)
        return 1

    if a.dry_run:
        print(" ".join(json.dumps(c, ensure_ascii=False) if " " in c or "\n" in c else c for c in resp["cmd"]))
        print("--- preamble ---\n" + resp["preamble"])
        print(f"--- session: {resp['session_reason']} | permissions_confirmed={resp['permissions_confirmed']}")
        return 0
    print(json.dumps(resp, ensure_ascii=False, indent=2) if a.json else resp["result"])
    meta = (f"[logic_agent] {resp['agent']} session={resp['session_id'][:8]} ({resp['session_reason']}) "
            f"model={resp['model']} {resp['duration_ms'] / 1000:.0f}s ${resp['cost_usd']:.2f} rc={resp['rc']}")
    if not resp["permissions_confirmed"]:
        meta += " | allowlist not confirmed (directory settings only)"
    print(meta, file=sys.stderr)
    if resp["is_error"]:
        print(f"[logic_agent] failed: {resp.get('stderr', '')[:400]}", file=sys.stderr)
        return 2 if resp["rc"] == 124 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
