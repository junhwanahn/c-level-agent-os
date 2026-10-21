#!/usr/bin/env python3
"""Reporting daemon-lite: two-layer periodic report and scheduled triggers (docs/05).

A single long-running process, started by a service manager (launchd / systemd), never by an
interactive session. Every poll (default 60 s) it does two things:

  1. Periodic report at each `report_times` slot (local time in `timezone`):
       top block    rendered from the owner action queue WITHOUT a model (docs/03 render rules)
       model block  `claude -p` in project_dir, tools off by default, over the `inputs` files,
                    each passed with its age (STALE / MISSING stated); structured JSON reply.
     If the model call fails (outage, billing, timeout), the top block is still sent with a
     one-line failure note.
  2. Scheduled triggers: open queue items with `trigger_at` + `trigger_prompt`:
       timezone required (naive = invalid, reported once) | on-time window | catch-up (delay
       labelled) | missed (notified once) | deferred around a report (runs even past catch-up) |
       dedupe on (id, trigger_at) in an append-only log | "fired" written BEFORE running (after a
       restart an unfinished fire becomes an "interrupted" notice, never a silent re-run) |
       one retry | tools off unless trigger_tools: true | trigger_agent via scripts/logic_agent.py.

Delivery: Telegram Bot API, split by length (never truncated). The bot token and chat id come
only from environment variables whose names are in the config. `delivery: none` only prints the
report (and keeps the full text under log_dir/reports/), for trying the daemon before a bot exists.
Queue items marked `example: true` (the template's examples) are ignored.

Usage:
  python3 scripts/report_daemon.py --check-config                 # config, queue, CLI path, env
  python3 scripts/report_daemon.py --dry-run --once --no-model    # top block now, printed, no model call
  python3 scripts/report_daemon.py --dry-run --once               # full report now, printed, nothing sent
  python3 scripts/report_daemon.py --once                         # send one report now (outside the schedule)
  python3 scripts/report_daemon.py --status                       # lock, last/next report, trigger states
  python3 scripts/report_daemon.py                                # the loop (what the service manager runs)

Config: config/report_daemon.yaml (template: templates/report_daemon.yaml); override with --config
or REPORT_DAEMON_CONFIG. project_dir resolves relative to the project root (the parent of scripts/);
every other relative path resolves relative to project_dir.
State in log_dir (default state/report_daemon/): reports.jsonl, triggers.jsonl (append-only),
state.json, daemon.log, reports/ and runs/ (full texts).
Dependencies: Python 3.9+, PyYAML. Check flag names against `claude --help` for your CLI version.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
EX_CONFIG = 78  # sysexits EX_CONFIG; the systemd unit does not restart on it
TELEGRAM_API = "https://api.telegram.org"
DEFAULTS = {
    "timezone": None,  # required
    "report_times": ["08:00", "20:00"],
    "poll_seconds": 60,
    "on_time_minutes": 5,
    "catchup_minutes": 90,
    "collision_minutes": 5,
    "retry_delay_seconds": 60,
    "project_dir": ".",
    "queue_path": "config/owner_action_queue.yaml",
    "prompt_file": "config/report_prompt.md",
    "log_dir": "state/report_daemon",
    "inputs": [],
    "stale_hours": 26,
    "input_max_chars": 20000,
    "claude_cmd": "claude",
    "claude_extra_args": [],
    "model": None,
    "timeout_s": 600,
    "report_tools": False,
    "logic_agent_script": "scripts/logic_agent.py",
    "agent_timeout_s": 3600,
    "delivery": "telegram",
    "telegram": {"token_env": "REPORT_TELEGRAM_TOKEN", "chat_id_env": "REPORT_TELEGRAM_CHAT_ID"},
    "max_chunk": 4000,
    "max_notable": 5,
    "max_executed": 8,
    "trigger_result_lines": 15,
}
_POSITIVE = ("poll_seconds", "on_time_minutes", "catchup_minutes", "timeout_s", "agent_timeout_s", "max_chunk",
             "stale_hours", "input_max_chars", "max_notable", "max_executed", "trigger_result_lines")
_NON_NEGATIVE = ("collision_minutes", "retry_delay_seconds")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")
_TERMINAL = ("done", "missed", "interrupted", "invalid")
DELIVERY = ("telegram", "none")


class ConfigError(Exception):
    def __init__(self, problems: list):
        super().__init__("; ".join(problems))
        self.problems = problems


class DaemonError(Exception):
    pass


# ---------------------------------------------------------------- config
def config_path(arg: Optional[str] = None) -> Path:
    return Path(arg or os.environ.get("REPORT_DAEMON_CONFIG") or PROJECT_ROOT / "config" / "report_daemon.yaml")


def _abs(base: Path, raw) -> Path:
    p = Path(os.path.expanduser(str(raw)))
    return p if p.is_absolute() else base / p


def load_config(path) -> dict:
    """Defaults + file, validated. Raises ConfigError with every problem found."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError([f"config not found: {p} (copy templates/report_daemon.yaml to config/)"])
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError([f"config is not valid YAML: {p}: {str(e).splitlines()[0]}"])
    if not isinstance(raw, dict):
        raise ConfigError([f"config must be a mapping: {p}"])
    cfg = {**DEFAULTS, **raw}
    cfg["telegram"] = {**DEFAULTS["telegram"], **(raw.get("telegram") or {})}
    problems = []

    tz_name = str(cfg.get("timezone") or "").strip()
    if not tz_name:
        problems.append("timezone is required (IANA name such as Asia/Seoul, Europe/Berlin, UTC)")
    else:
        try:
            cfg["tz"] = ZoneInfo(tz_name)
        except Exception:
            problems.append(f"timezone '{tz_name}' is not a valid IANA name (on Linux: install tzdata)")
    cfg["timezone"] = tz_name

    times = cfg.get("report_times")
    times = [times] if isinstance(times, str) else list(times or [])
    slots = []
    for t in times:
        if isinstance(t, int):
            problems.append(f"report_times entry {t} is a number: quote times ('20:00'); "
                            f"YAML reads unquoted 20:00 as 1200")
            continue
        m = _HHMM.match(str(t).strip())
        if not m:
            problems.append(f"report_times entry '{t}' is not HH:MM (24h)")
        else:
            slots.append((int(m.group(1)), int(m.group(2))))
    if not times:
        problems.append("report_times is empty")
    cfg["slots"] = sorted(set(slots))
    cfg["report_times"] = [f"{h:02d}:{m:02d}" for h, m in cfg["slots"]]

    for key in _POSITIVE + _NON_NEGATIVE:
        try:
            v = float(cfg[key])
        except (TypeError, ValueError):
            problems.append(f"{key} must be a number (got {cfg[key]!r})")
            continue
        if v < 0 or (v == 0 and key in _POSITIVE):
            problems.append(f"{key} must be {'positive' if key in _POSITIVE else '>= 0'} (got {v:g})")
        cfg[key] = int(v) if v == int(v) else v
    if not problems:
        if cfg["catchup_minutes"] < cfg["on_time_minutes"]:
            problems.append("catchup_minutes must be >= on_time_minutes")
        if not 200 <= cfg["max_chunk"] <= 4096:
            problems.append("max_chunk must be between 200 and 4096 (Telegram's limit is 4096 per message)")
    for secret in ("token", "bot_token", "chat_id"):
        if cfg["telegram"].get(secret):
            problems.append(f"telegram.{secret} found in the config: secrets go in environment variables only "
                            f"(name them in telegram.token_env / telegram.chat_id_env)")
    if not isinstance(cfg.get("claude_extra_args") or [], list):
        problems.append("claude_extra_args must be a list")
    cfg["delivery"] = str(cfg.get("delivery") or "telegram").strip().lower()
    if cfg["delivery"] not in DELIVERY:
        problems.append(f"delivery must be one of {', '.join(DELIVERY)} (got {cfg['delivery']!r})")

    project_dir = _abs(PROJECT_ROOT, cfg["project_dir"]).resolve()
    inputs = []
    for n, ent in enumerate(cfg.get("inputs") or []):
        ent = {"path": ent} if isinstance(ent, str) else ent
        if not isinstance(ent, dict) or not ent.get("path"):
            problems.append(f"inputs[{n}] needs a path")
            continue
        try:
            max_age = float(ent.get("max_age_hours") or cfg["stale_hours"])
        except (TypeError, ValueError):
            problems.append(f"inputs[{n}].max_age_hours must be a number")
            continue
        inputs.append({"path": _abs(project_dir, ent["path"]),
                       "label": str(ent.get("label") or Path(str(ent["path"])).name), "max_age_hours": max_age})
    if problems:
        raise ConfigError(problems)

    cfg["project_dir"] = project_dir
    for key in ("queue_path", "prompt_file", "log_dir", "logic_agent_script"):
        cfg[key] = _abs(project_dir, cfg[key])
    cfg["inputs"] = inputs
    cfg["claude_extra_args"] = [str(x) for x in cfg.get("claude_extra_args") or []]
    cfg["config_path"] = p
    return cfg


# ---------------------------------------------------------------- time helpers
def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso(s) -> Optional[datetime]:
    dt, err = parse_trigger_at(s)
    return None if err else dt


def parse_trigger_at(raw) -> tuple:
    """(aware datetime, None) or (None, reason). A naive timestamp is invalid: reported, never guessed."""
    if isinstance(raw, datetime):
        return (raw, None) if raw.utcoffset() is not None else (None, "no timezone (add Z or +hh:mm)")
    s = str(raw if raw is not None else "").strip()
    if not s:
        return None, "empty trigger_at"
    s2 = s[:-1] + "+00:00" if s[-1] in "Zz" else s
    if "T" in s2 or " " in s2:
        s2 = _OFFSET_NO_COLON.sub(r"\1:\2", s2)
    try:
        dt = datetime.fromisoformat(s2)
    except ValueError:
        return None, f"not an ISO 8601 time: '{s}'"
    if dt.utcoffset() is None:
        return None, "no timezone (add Z or +hh:mm)"
    return dt, None


def fmt_local(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "<1m"
    if s < 3600:
        return f"{s // 60}m"
    if s < 2 * 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _minutes(td: timedelta) -> int:
    """Whole minutes, rounded down (a delay of 69.5 min is "69 min")."""
    return int(td.total_seconds() // 60)


def _d(raw) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _first_line(s) -> str:
    return next((ln.strip() for ln in str(s or "").splitlines() if ln.strip()), "")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "item"


# ---------------------------------------------------------------- queue and top block
class _QueueLoader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as strings, so an unquoted trigger_at is checked, not converted."""


_QueueLoader.yaml_implicit_resolvers = {
    ch: [r for r in rs if r[0] != "tag:yaml.org,2002:timestamp"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}


def load_queue(path, examples: bool = False) -> tuple:
    """(items, warning or None). A missing or broken file gives an empty list and a warning, never an exception.
    Items marked `example: true` (the template's examples) are dropped unless examples=True."""
    p = Path(path)
    if not p.is_file():
        return [], f"queue file not found: {p}"
    try:
        data = yaml.load(p.read_text(), Loader=_QueueLoader)
    except yaml.YAMLError as e:
        return [], f"queue file cannot be parsed: {p}: {_first_line(e)}"
    if data is None:
        return [], None
    if not isinstance(data, list):
        return [], f"queue file must be a YAML list of items: {p}"
    items = [i for i in data if isinstance(i, dict)]
    skipped = len(data) - len(items)
    if not examples:
        items = [i for i in items if i.get("example") is not True]
    return items, (f"{skipped} queue entries are not mappings and were ignored: {p}" if skipped else None)


def _dn(due: date, today: date) -> str:
    n = (due - today).days
    return f"D-{n}" if n >= 0 else f"D+{-n}"


def _title(i: dict) -> str:
    return str(i.get("title") or i.get("id") or "(untitled)")


def render_top_block(items: list, today: date, now: datetime, cfg: dict) -> str:
    """docs/03 render rules, no model: owner actions, executed in 24h, due soon or overdue, scheduled triggers."""
    out = []
    owner = [i for i in items if i.get("owner") == "human" and i.get("status") == "open" and i.get("ready") is True]
    owner.sort(key=lambda i: _d(i.get("due")) or date.max)
    actions = [i for i in owner if i.get("action") != "facts"]
    facts = [i for i in owner if i.get("action") == "facts"]

    def owner_line(i):
        d = _d(i.get("due"))
        due = f"{i.get('due')} ({_dn(d, today)})" if d else f"{i.get('due') or 'no due'} (bad date)"
        return f"- {_title(i)} - {due} - {i.get('action') or 'no action type'}"

    out.append(f"> Owner actions {len(owner)} (actions {len(actions)}, fact checks {len(facts)})")
    out += [owner_line(i) for i in actions]
    if facts:
        out.append(f"> Pending fact checks {len(facts)}")
        out += [owner_line(i) for i in facts]

    yday = today - timedelta(days=1)
    done = [i for i in items if i.get("status") == "done" and _d(i.get("done_at")) in (today, yday)]
    done.sort(key=lambda i: _d(i.get("done_at")), reverse=True)
    cap = int(cfg.get("max_executed", 8))
    out += ["", f"> Decided and executed by CEO (24h) {len(done)}"]
    for i in done[:cap]:
        tag = " [owner done]" if i.get("owner") == "human" else ""
        out.append(f"- {_title(i)}{tag} ({_d(i.get('done_at')).strftime('%m/%d')})")
    if len(done) > cap:
        out.append(f"- and {len(done) - cap} more")

    in_queue = {id(i) for i in owner}
    others = [i for i in items if i.get("status") == "open" and id(i) not in in_queue]
    horizon = today + timedelta(days=2)
    soon = sorted((i for i in others if _d(i.get("due")) and _d(i.get("due")) <= horizon),
                  key=lambda i: _d(i.get("due")))
    bad = [i for i in others if not _d(i.get("due"))]
    out += ["", f"> Due soon or overdue (D-2) {len(soon) + len(bad)}"]
    for i in soon:
        out.append(f"- [{_dn(_d(i.get('due')), today)}] {i.get('owner') or '?'} - {_title(i)} ({i.get('due')})")
    for i in bad:
        out.append(f"- [bad due] {i.get('owner') or '?'} - {_title(i)} ({i.get('due')!r})")

    tz = cfg["tz"]
    upcoming, invalid = [], []
    for i in items:
        if i.get("status") != "open" or i.get("trigger_at") in (None, ""):
            continue
        dt, err = parse_trigger_at(i.get("trigger_at"))
        if not err and not str(i.get("trigger_prompt") or "").strip():
            err = "trigger_prompt is missing"
        if err:
            invalid.append(f"- [invalid: {err}] {i.get('id')} - trigger_at {str(i.get('trigger_at'))!r}")
        elif now <= dt <= now + timedelta(hours=24):
            who = f"agent {i.get('trigger_agent')}" if i.get("trigger_agent") else "ceo"
            tools = ", tools on" if i.get("trigger_tools") is True else ""
            upcoming.append((dt, f"- {fmt_local(dt, tz)} - {i.get('id')} - {who}{tools}"))
    if upcoming or invalid:
        out += ["", f"> Scheduled triggers (next 24h) {len(upcoming)}"]
        out += [line for _, line in sorted(upcoming, key=lambda x: x[0])] + invalid
    return "\n".join(out)


# ---------------------------------------------------------------- layer 2 inputs and model block
def gather_inputs(cfg: dict, now: datetime) -> list:
    """The queue (age only) plus every configured input file: age, STALE / MISSING, and text for the prompt."""
    recs = [_input_record("queue", Path(cfg["queue_path"]), None, now, 0)]
    for ent in cfg["inputs"]:
        recs.append(_input_record(ent["label"], Path(ent["path"]), ent["max_age_hours"], now,
                                  int(cfg["input_max_chars"])))
    return recs


def _input_record(label, path: Path, max_age_h, now: datetime, max_chars: int) -> dict:
    rec = {"label": label, "path": str(path), "missing": not path.is_file(), "age_s": None, "stale": False,
           "text": ""}
    if rec["missing"]:
        return rec
    rec["age_s"] = now.timestamp() - path.stat().st_mtime
    rec["stale"] = max_age_h is not None and rec["age_s"] > float(max_age_h) * 3600
    if max_chars:
        text = path.read_text(errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n[cut by the daemon: {len(text) - max_chars} more characters not shown]"
        rec["text"] = text
    return rec


def _input_tag(rec: dict) -> str:
    if rec["missing"]:
        return f"{rec['label']} MISSING"
    return f"{rec['label']} {fmt_age(rec['age_s'])}" + (" STALE" if rec["stale"] else "")


def build_prompt(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def _inputs_block(files: list) -> str:
    """Prompt section for the configured input files (gather_inputs()[1:]): header with age and state, then text."""
    if not files:
        return "(no input files configured; only the top block is available)"
    parts = []
    for r in files:
        state = "MISSING" if r["missing"] else ("STALE" if r["stale"] else "fresh")
        age = "n/a" if r["age_s"] is None else fmt_age(r["age_s"])
        parts.append(f"### {r['label']} · {r['path']} · age {age} · {state}\n{r['text']}".rstrip())
    return "\n\n".join(parts)


def parse_structured(text: str) -> Optional[dict]:
    """The model's JSON object (code fences tolerated), or None when the reply is not structured."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    for candidate in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and any(k in obj for k in ("headline", "notable", "ceo_view", "stale")):
            return obj
    return None


def render_model_block(text: str, cfg: dict, full_path: str = "") -> str:
    obj = parse_structured(text)
    if obj is None:
        return "> CEO notes (unstructured reply, shown as is)\n" + text.strip()
    out = []
    if obj.get("headline"):
        out.append(f"> Summary: {str(obj['headline']).strip()}")
    notable = obj.get("notable") or []
    notable = [notable] if isinstance(notable, (str, dict)) else list(notable)
    cap = int(cfg.get("max_notable", 5))
    out.append(f"> Notable {len(notable)}")
    for n in notable[:cap]:
        if isinstance(n, dict):
            src = f" [{n.get('source')}]" if n.get("source") else ""
            n = f"{n.get('text') or n.get('item') or json.dumps(n, ensure_ascii=False)}{src}"
        out.append(f"- {str(n).strip()}")
    if len(notable) > cap:
        out.append(f"- and {len(notable) - cap} more" + (f" (full reply: {full_path})" if full_path else ""))
    if obj.get("ceo_view"):
        out += ["> CEO view", str(obj["ceo_view"]).strip()]
    stale = obj.get("stale") or []
    if stale:
        out.append("> Treated as stale: " + ", ".join(str(s) for s in (stale if isinstance(stale, list) else [stale])))
    return "\n".join(out)


# ---------------------------------------------------------------- subprocess (claude -p, logic_agent.py)
def _child_env(drop=()) -> dict:
    """Drop CLAUDECODE / CLAUDE_CODE_* (nested-session guard) and the messenger secrets the child does not need."""
    drop = set(drop)
    return {k: v for k, v in os.environ.items()
            if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_") and k not in drop}


def run_cmd(cmd: list, stdin_text: str, cwd: str, timeout: int, env_drop=()) -> tuple:
    """(rc, stdout, stderr, ms). Own process group; on timeout or daemon stop the whole group is killed (rc 124)."""
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=cwd, env=_child_env(env_drop), start_new_session=True)
    except FileNotFoundError:
        return 127, "", (f"command not found: {cmd[0]} (a service does not use your shell's PATH; "
                         f"set an absolute path or PATH in the service definition)"), 0
    except OSError as e:
        return 126, "", f"cannot start {cmd[0]}: {e}", 0

    def kill():
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        out, err = p.communicate(input=stdin_text, timeout=timeout)
        return p.returncode, out or "", err or "", int((time.monotonic() - t0) * 1000)
    except subprocess.TimeoutExpired:
        kill()
        out, err = p.communicate()
        return 124, out or "", f"timeout {timeout}s\n{err or ''}", int((time.monotonic() - t0) * 1000)
    except BaseException:
        kill()
        raise


def claude_command(cfg: dict, tools: bool) -> list:
    """`claude -p` with JSON output; tools off unless asked. Never --permission-mode / --continue / --resume."""
    cmd = [str(cfg["claude_cmd"]), "-p", "--output-format", "json"]
    if cfg.get("model"):
        cmd += ["--model", str(cfg["model"])]
    if not tools:
        cmd += ["--tools", ""]
    return cmd + list(cfg.get("claude_extra_args") or [])


def agent_command(cfg: dict, agent: str, tools: bool) -> list:
    cmd = [sys.executable, str(cfg["logic_agent_script"]), agent, "--json"]
    return cmd if tools else cmd + ["--no-tools"]


def _parse_json(out: str) -> dict:
    if not (out or "").strip():
        return {}
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return {"result": out.strip()}
    return obj if isinstance(obj, dict) else {"result": out.strip()}


def interpret(rc: int, out: str, err: str, ms: int, timeout: int) -> dict:
    parsed = _parse_json(out)
    text = str(parsed.get("result") or "").strip()
    is_error = rc != 0 or bool(parsed.get("is_error")) or not text
    error = ""
    if is_error:
        error = (f"timeout after {timeout}s" if rc == 124 else
                 (_first_line(text) if parsed.get("is_error") and text else "")
                 or _first_line(err) or (f"exit code {rc}" if rc else "empty result"))
    cost = parsed.get("total_cost_usd", parsed.get("cost_usd")) or 0.0
    return {"ok": not is_error, "text": text, "error": error, "rc": rc, "cost_usd": float(cost),
            "duration_ms": int(parsed.get("duration_ms") or ms)}


# ---------------------------------------------------------------- messaging (Telegram)
def utf16_len(s: str) -> int:
    """Telegram counts message length in UTF-16 code units (an emoji outside the BMP counts 2)."""
    return len(s.encode("utf-16-le")) // 2


def _hard_split(line: str, limit: int) -> list:
    if utf16_len(line) <= limit:
        return [line]
    parts, cur, n = [], [], 0
    for ch in line:
        w = 2 if ord(ch) > 0xFFFF else 1
        if n + w > limit:
            parts.append("".join(cur))
            cur, n = [], 0
        cur.append(ch)
        n += w
    if cur:
        parts.append("".join(cur))
    return parts


def split_message(text: str, limit: int) -> list:
    """Split on line boundaries into chunks of at most `limit` UTF-16 units; over-long lines are cut, never dropped."""
    if utf16_len(text) <= limit:
        return [text] if text.strip() else []
    chunks, cur, n = [], [], 0
    for line in text.split("\n"):
        for piece in _hard_split(line, limit):
            w = utf16_len(piece)
            if cur and n + 1 + w > limit:
                chunks.append("\n".join(cur))
                cur, n = [], 0
            n += w + (1 if cur else 0)
            cur.append(piece)
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def label_chunks(text: str, limit: int) -> list:
    chunks = split_message(text, limit)
    if len(chunks) <= 1:
        return chunks
    chunks = split_message(text, limit - 12)  # room for the "(i/n)" label
    return [f"({i}/{len(chunks)})\n{c}" for i, c in enumerate(chunks, 1)]


def http_post(url: str, data: dict, timeout: int) -> tuple:
    """(status, body). status 0 = network error (body = reason)."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(getattr(e, "reason", e))


def _tg_result(status: int, body: str) -> tuple:
    """(ok, error, retry_after seconds)."""
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        obj = {}
    if status == 200 and obj.get("ok") is True:
        return True, "", 0
    if status == 0:
        return False, f"network error: {body}", 5
    retry = int((obj.get("parameters") or {}).get("retry_after") or 5)
    return False, f"HTTP {status}: {obj.get('description') or _first_line(body) or 'no description'}", retry


def telegram_send(chunks: list, token: str, chat_id: str, post: Callable, sleep: Callable) -> tuple:
    """(ok, error, chunks sent). One retry per chunk; the token never appears in an error."""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    for n, chunk in enumerate(chunks):
        for attempt in (1, 2):
            status, body = post(url, {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}, 20)
            ok, err, retry_after = _tg_result(status, body)
            if ok:
                break
            err = err.replace(token, "<token>")
            if attempt == 2:
                return False, err, n
            sleep(min(retry_after, 60))
    return True, "", len(chunks)


# ---------------------------------------------------------------- state
class JsonlLog:
    """Append-only JSON lines. Never rewritten; the last row per key is the current state."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def rows(self) -> list:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


class _DaemonLock:
    """One loop per log_dir (non-blocking flock)."""

    def __init__(self, log_dir: Path):
        self.path = Path(log_dir) / "report_daemon.lock"
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise DaemonError(f"another report_daemon loop holds {self.path}")
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def lock_held(log_dir: Path) -> bool:
    path = Path(log_dir) / "report_daemon.lock"
    if not path.is_file():
        return False
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


# ---------------------------------------------------------------- daemon
class Daemon:
    """All time comes from now_fn (aware UTC) and all waiting from sleep_fn, so tests drive a fake clock."""

    def __init__(self, cfg: dict, now_fn: Optional[Callable] = None, sleep_fn: Optional[Callable] = None,
                 runner: Optional[Callable] = None, post: Optional[Callable] = None, dry_run: bool = False,
                 use_model: bool = True, out=None):
        self.cfg = cfg
        self.tz = cfg["tz"]
        self.now = now_fn or (lambda: datetime.now(UTC))
        self.sleep = sleep_fn or time.sleep
        self.runner = runner or run_cmd
        self.post = post or http_post
        self.dry_run = dry_run
        self.use_model = use_model
        self.out = out or sys.stdout
        log_dir = Path(cfg["log_dir"])
        self.log_dir = log_dir
        self.reports = JsonlLog(log_dir / "reports.jsonl")
        self.triggers = JsonlLog(log_dir / "triggers.jsonl")
        self.state_path = log_dir / "state.json"
        self.on_time = timedelta(minutes=cfg["on_time_minutes"])
        self.catchup = timedelta(minutes=cfg["catchup_minutes"])
        self.collision = timedelta(minutes=cfg["collision_minutes"])
        tg = cfg["telegram"]
        self.env_drop = tuple(x for x in (tg.get("token_env"), tg.get("chat_id_env")) if x)
        # (start, end, what) of synchronous runs in this process. A slot or trigger time that passed while
        # the daemon was busy is late because of us, not because it is stale: it is caught up, not missed.
        self.busy: list = []

    def _mark_busy(self, start: datetime, what: str) -> None:
        end = self.now()
        self.busy = [b for b in self.busy if b[1] >= end - timedelta(days=1)] + [(start, end, what)]

    def _busy_during(self, t: datetime) -> Optional[str]:
        return next((b[2] for b in self.busy if b[0] <= t <= b[1]), None)

    # ---- logging and files
    def log(self, msg: str) -> None:
        line = f"{_iso(self.now())} {msg}"
        print(f"[report_daemon] {line}", file=sys.stderr)
        if not self.dry_run:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with (self.log_dir / "daemon.log").open("a") as f:
                f.write(line + "\n")

    def _save_text(self, sub: str, name: str, text: str) -> str:
        if self.dry_run:
            return ""
        p = self.log_dir / sub / f"{_safe_name(name)}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return str(p)

    def installed_at(self) -> datetime:
        """First loop start. Report slots before it (minus catch-up) are not reported as missed."""
        try:
            st = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            st = {}
        dt = _from_iso(st.get("installed_at")) if st.get("installed_at") else None
        if dt is None:
            dt = self.now()
            if not self.dry_run:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps({**st, "installed_at": _iso(dt)}, indent=2))
        return dt

    # ---- delivery
    def deliver(self, text: str) -> tuple:
        """(ok, error, chunk count). Dry run prints to stdout. Without credentials the text still goes to stdout."""
        chunks = label_chunks(text, int(self.cfg["max_chunk"]))
        if self.dry_run or self.cfg.get("delivery") == "none":
            print("\n\n".join(chunks), file=self.out)
            print("", file=self.out)
            return True, "", len(chunks)
        tg = self.cfg["telegram"]
        token = os.environ.get(str(tg.get("token_env") or ""), "").strip()
        chat = os.environ.get(str(tg.get("chat_id_env") or ""), "").strip()
        if not token or not chat:
            err = f"telegram not configured: set {tg.get('token_env')} and {tg.get('chat_id_env')} in the environment"
            self.log(f"delivery failed: {err}; message printed to stdout instead")
            print(text, file=self.out)
            return False, err, len(chunks)
        ok, err, sent = telegram_send(chunks, token, chat, self.post, self.sleep)
        if not ok:
            self.log(f"delivery failed after {sent}/{len(chunks)} chunks: {err}")
        return ok, err, len(chunks)

    # ---- periodic report
    def _slot_key(self, slot: datetime) -> str:
        return f"{slot.astimezone(self.tz):%Y-%m-%d %H:%M} {self.cfg['timezone']}"

    def _slots_between(self, start: datetime, end: datetime) -> list:
        """Report slots s with start < s <= end (aware datetimes in the configured timezone)."""
        d = start.astimezone(self.tz).date() - timedelta(days=1)
        last = end.astimezone(self.tz).date() + timedelta(days=1)
        out = []
        while d <= last:
            for h, m in self.cfg["slots"]:
                s = datetime(d.year, d.month, d.day, h, m, tzinfo=self.tz)
                if start < s <= end:
                    out.append(s)
            d += timedelta(days=1)
        return sorted(out)

    def _handled_slots(self) -> set:
        return {r.get("slot") for r in self.reports.rows() if r.get("slot")}

    def report_plan(self, now: datetime) -> tuple:
        """(slot to run now or None, label, missed slots, superseded slots)."""
        handled = self._handled_slots()
        floor = max(now - timedelta(hours=24), self.installed_at() - self.catchup)
        pending = [s for s in self._slots_between(floor, now) if self._slot_key(s) not in handled]
        catchable = [s for s in pending if now - s <= self.catchup or self._busy_during(s)]
        missed = [s for s in pending if s not in catchable]
        run = catchable[-1] if catchable else None
        label = None
        if run is not None:
            late = now - run
            label = "on time" if late <= self.on_time else f"delayed {_minutes(late)} min"
            if late > self.catchup:
                label += f" (daemon busy with {self._busy_during(run)})"
        return run, label, missed, catchable[:-1]

    def report_imminent(self, now: datetime) -> bool:
        handled = self._handled_slots()
        return any(self._slot_key(s) not in handled for s in self._slots_between(now, now + self.collision))

    def next_report(self, now: datetime) -> Optional[datetime]:
        handled = self._handled_slots()
        return next((s for s in self._slots_between(now, now + timedelta(days=2))
                     if self._slot_key(s) not in handled), None)

    def build_report(self, now: datetime, title: str) -> tuple:
        cfg = self.cfg
        items, qwarn = load_queue(cfg["queue_path"])
        if qwarn:
            self.log(f"warning: {qwarn}")
        top = render_top_block(items, now.astimezone(self.tz).date(), now, cfg)
        inputs = gather_inputs(cfg, now)
        lines = [title, "Inputs: " + " · ".join(_input_tag(r) for r in inputs)]
        if qwarn:
            lines.append(f"Warning: {qwarn}. The top block below may be incomplete.")
        prev = [r for r in self.reports.rows() if r.get("event") in ("sent", "send_failed")]
        if prev and prev[-1].get("event") == "send_failed":
            lines.append(f"Note: the previous report ({prev[-1].get('slot')}) was not delivered: "
                         f"{prev[-1].get('error')}")
        lines += ["", top]
        meta = {"model_ok": None, "model_error": "", "cost_usd": 0.0}
        if not self.use_model:
            return "\n".join(lines), meta
        prompt_file = Path(cfg["prompt_file"])
        if not prompt_file.is_file():
            res = {"ok": False, "error": f"prompt file not found: {prompt_file}", "text": "", "cost_usd": 0.0}
        else:
            prompt = build_prompt(prompt_file.read_text(), {
                "now": fmt_local(now, self.tz), "timezone": cfg["timezone"], "slot": title,
                "top_block": top, "inputs": _inputs_block(inputs[1:])})
            res = interpret(*self.runner(claude_command(cfg, bool(cfg["report_tools"])), prompt,
                                         str(cfg["project_dir"]), int(cfg["timeout_s"]), env_drop=self.env_drop),
                            timeout=int(cfg["timeout_s"]))
        meta.update(model_ok=res["ok"], model_error=res["error"], cost_usd=res.get("cost_usd", 0.0))
        if res["ok"]:
            full = self._save_text("reports", f"model_{_iso(now)}", res["text"])
            lines += ["", render_model_block(res["text"], cfg, full)]
        else:
            self.log(f"model call failed: {res['error']}")
            lines += ["", f"Model block unavailable ({res['error']}); the top block above is complete."]
        return "\n".join(lines), meta

    def run_report(self, slot: Optional[datetime], label: str) -> bool:
        now = self.now()
        key = self._slot_key(slot) if slot is not None else "manual"
        when = fmt_local(slot if slot is not None else now, self.tz)
        title = f"[Report {when}] {label}"
        if not self.dry_run:
            self.reports.append({"ts": _iso(now), "slot": key, "event": "started", "label": label})
        text, meta = self.build_report(now, title)
        ok, err, n = self.deliver(text)
        self._mark_busy(now, f"report {when}")
        if not self.dry_run:
            self._save_text("reports", f"{key}_{_iso(now)}", text)
            self.reports.append({"ts": _iso(self.now()), "slot": key, "event": "sent" if ok else "send_failed",
                                 "label": label, "chunks": n, "error": err, **meta})
        done = ("printed only (dry run)" if self.dry_run else
                "printed and saved (delivery: none)" if self.cfg.get("delivery") == "none" else "sent")
        self.log(f"report {when} [{label}]: {done if ok else 'NOT delivered'} ({n} chunks)"
                 + ("" if meta["model_ok"] in (None, True) else f", model failed: {meta['model_error']}"))
        return ok

    # ---- scheduled triggers
    def plan_triggers(self, items: list, now: datetime) -> list:
        """One plan per open item with trigger_at. action: fire | missed | invalid | pending | closed."""
        last = {}
        for r in self.triggers.rows():
            last[(r.get("id"), r.get("trigger_at"))] = r
        plans = []
        for it in items:
            if it.get("status") != "open" or it.get("trigger_at") in (None, ""):
                continue
            iid = str(it.get("id") or "").strip()
            raw = it.get("trigger_at")
            dt, err = parse_trigger_at(raw)
            prompt = str(it.get("trigger_prompt") or "").strip()
            if not err and not prompt:
                err = "trigger_prompt is missing"
            if not err and not iid:
                err = "id is missing"
            # an invalid item gets its own key, so fixing it (adding Z, adding the prompt) schedules it afresh
            at = f"invalid:{raw}:{err}" if err else _iso(dt)
            p = {"id": iid or "(no id)", "trigger_at": at, "raw": str(raw), "dt": dt, "prompt": prompt,
                 "agent": str(it["trigger_agent"]) if it.get("trigger_agent") else None,
                 "tools": it.get("trigger_tools") is True, "error": err, "label": "", "deferred": False}
            prev = last.get((p["id"], at))
            ev = prev.get("event") if prev else None
            if err:
                p.update(action="closed" if ev == "invalid" else "invalid", state="invalid")
            elif ev in _TERMINAL:
                state = ev if ev != "done" else ("done" if prev.get("ok") else "failed")
                p.update(action="closed", state=state)
            elif ev in ("fired", "retry"):
                p.update(action="closed", state="running")
            else:
                late = now - dt
                p["deferred"] = ev == "deferred"
                if p["deferred"]:
                    delay = "on time" if late <= self.on_time else f"delayed {_minutes(late)} min"
                    p.update(action="fire", state="due", label=f"deferred for the report, {delay}")
                elif late < -self.on_time:
                    p.update(action="pending", state="scheduled")
                elif late <= self.on_time:
                    p.update(action="fire", state="due", label="on time")
                elif late <= self.catchup:
                    p.update(action="fire", state="due", label=f"delayed {_minutes(late)} min (catch-up)")
                elif self._busy_during(dt):
                    p.update(action="fire", state="due",
                             label=f"delayed {_minutes(late)} min (daemon busy with {self._busy_during(dt)})")
                else:
                    p.update(action="missed", state="missed (notice pending)")
            plans.append(p)
        return plans

    def _trigger_row(self, p: dict, event: str, **extra) -> None:
        self.triggers.append({"ts": _iso(self.now()), "id": p["id"], "trigger_at": p["trigger_at"],
                              "event": event, **extra})

    def _execute(self, p: dict) -> dict:
        try:
            return self._execute_once(p)
        except Exception as e:  # a failed run is a failure to report and retry, not a stuck "fired"
            return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}", "cost_usd": 0.0,
                    "duration_ms": 0, "rc": 1}

    def _execute_once(self, p: dict) -> dict:
        cfg = self.cfg
        if p["agent"]:
            script = Path(cfg["logic_agent_script"])
            if not script.is_file():
                return {"ok": False, "text": "", "error": f"logic_agent script not found: {script}", "cost_usd": 0.0,
                        "duration_ms": 0, "rc": 127}
            timeout = int(cfg["agent_timeout_s"])
            raw = self.runner(agent_command(cfg, p["agent"], p["tools"]), p["prompt"], str(cfg["project_dir"]),
                              timeout, env_drop=self.env_drop)
        else:
            timeout = int(cfg["timeout_s"])
            raw = self.runner(claude_command(cfg, p["tools"]), p["prompt"], str(cfg["project_dir"]), timeout,
                              env_drop=self.env_drop)
        return interpret(*raw, timeout=timeout)

    def run_trigger(self, p: dict) -> dict:
        """Write "fired" first, run, retry once, write "done", post one short message."""
        started = self.now()
        self._trigger_row(p, "fired", attempt=1, label=p["label"], agent=p["agent"], tools=p["tools"])
        self.log(f"trigger {p['id']} fired ({p['label']})")
        res, attempts = self._execute(p), 1
        if not res["ok"]:
            self._trigger_row(p, "retry", attempt=2, error=res["error"][:300])
            self.log(f"trigger {p['id']} failed ({res['error']}); retrying in {self.cfg['retry_delay_seconds']}s")
            self.sleep(self.cfg["retry_delay_seconds"])
            res, attempts = self._execute(p), 2
        self._mark_busy(started, f"trigger {p['id']}")
        full = self._save_text("runs", f"{p['id']}_{_iso(self.now())}",
                               res["text"] if res["ok"] else f"FAILED: {res['error']}\n\n{res['text']}")
        self._trigger_row(p, "done", ok=res["ok"], attempts=attempts, error=res["error"][:300],
                          cost_usd=res.get("cost_usd", 0.0), duration_ms=res.get("duration_ms", 0))
        target = f" - agent {p['agent']}" if p["agent"] else ""
        head = f"[Scheduled] {p['id']}{target} - {p['label']} - trigger {fmt_local(p['dt'], self.tz)}"
        if res["ok"]:
            lines = res["text"].splitlines()
            keep = int(self.cfg["trigger_result_lines"])
            body = "\n".join(lines[:keep])
            if len(lines) > keep:
                body += f"\n(+{len(lines) - keep} more lines" + (f": {full})" if full else ")")
            msg = f"{head} - ok\n{body}"
        else:
            msg = f"{head} - FAILED after {attempts} attempts: {res['error']}"
            msg += f"\n(details: {full})" if full else ""
        self.deliver(msg)
        self.log(f"trigger {p['id']} {'ok' if res['ok'] else 'FAILED'} after {attempts} attempt(s)")
        return res

    # ---- loop
    def startup(self) -> None:
        """After a restart: unfinished fires and reports become "interrupted" notices, never silent re-runs."""
        self.installed_at()
        notices = []
        last = {}
        for r in self.triggers.rows():
            last[(r.get("id"), r.get("trigger_at"))] = r
        for (iid, at), r in last.items():
            if r.get("event") in ("fired", "retry"):
                self.triggers.append({"ts": _iso(self.now()), "id": iid, "trigger_at": at, "event": "interrupted",
                                      "fired_ts": r.get("ts")})
                fired = _from_iso(r.get("ts"))
                notices.append(f"[Scheduled] {iid} - INTERRUPTED: fired {fmt_local(fired, self.tz) if fired else '?'}, "
                               f"the daemon stopped before it finished. Not re-run; check what it did, "
                               f"then change trigger_at to run it again.")
        last_r = {}
        for r in self.reports.rows():
            last_r[r.get("slot")] = r
        for slot, r in last_r.items():
            if r.get("event") == "started":
                self.reports.append({"ts": _iso(self.now()), "slot": slot, "event": "interrupted"})
                notices.append(f"[Report {slot}] INTERRUPTED: the daemon stopped while building or sending it. "
                               f"Run --once for a report now.")
        if notices:
            self.deliver("\n".join(notices))

    def tick(self) -> None:
        now = self.now()
        items, qwarn = load_queue(self.cfg["queue_path"])
        if qwarn:
            self.log(f"warning: {qwarn}")
        run_slot, label, missed, superseded = self.report_plan(now)
        notices = []
        for s in missed:
            self.reports.append({"ts": _iso(now), "slot": self._slot_key(s), "event": "missed",
                                 "late_min": _minutes(now - s)})
            notices.append(f"[Report {fmt_local(s, self.tz)}] MISSED: not sent (now {fmt_local(now, self.tz)}, "
                           f"beyond the {self.cfg['catchup_minutes']} min catch-up window). Run --once for one now.")
        for s in superseded:
            self.reports.append({"ts": _iso(now), "slot": self._slot_key(s), "event": "superseded"})
            self.log(f"report {self._slot_key(s)} superseded by the later slot {self._slot_key(run_slot)}")
        hold = run_slot is not None or self.report_imminent(now)
        for p in self.plan_triggers(items, now):
            if p["action"] == "invalid":
                self._trigger_row(p, "invalid", error=p["error"], raw=p["raw"])
                notices.append(f"[Scheduled] {p['id']} - INVALID trigger_at {p['raw']!r}: {p['error']}. "
                               f"Not run; fix the queue item.")
            elif p["action"] == "missed":
                self._trigger_row(p, "missed", late_min=_minutes(now - p["dt"]))
                notices.append(f"[Scheduled] {p['id']} - MISSED: trigger {fmt_local(p['dt'], self.tz)}, now "
                               f"{fmt_local(now, self.tz)} (beyond the {self.cfg['catchup_minutes']} min catch-up "
                               f"window). Not run; change trigger_at to re-schedule.")
            elif p["action"] == "fire" and hold and not p["deferred"]:
                self._trigger_row(p, "deferred", reason="periodic report")
                self.log(f"trigger {p['id']} deferred until the periodic report is sent")
        if notices:
            self.deliver("\n".join(notices))
        if run_slot is not None:
            self.run_report(run_slot, label)
        while True:  # re-plan after every run: a long run shifts labels and may bring a report due
            now = self.now()
            due = [p for p in self.plan_triggers(items, now) if p["action"] == "fire"]
            if not due:
                return
            if self.report_imminent(now) or self.report_plan(now)[0] is not None:
                for q in due:  # the report goes first; these run right after it, even past catch-up
                    if not q["deferred"]:
                        self._trigger_row(q, "deferred", reason="periodic report")
                return
            self.run_trigger(due[0])

    def run_forever(self, max_ticks: Optional[int] = None) -> None:
        with _DaemonLock(self.log_dir):
            self.log(f"started pid={os.getpid()} timezone={self.cfg['timezone']} "
                     f"reports={','.join(self.cfg['report_times'])} poll={self.cfg['poll_seconds']}s")
            self.startup()
            ticks = 0
            try:
                while max_ticks is None or ticks < max_ticks:
                    try:
                        self.tick()
                    except Exception as e:  # one bad tick must not stop the daemon
                        self.log(f"tick failed: {type(e).__name__}: {e}")
                        traceback.print_exc(file=sys.stderr)
                    ticks += 1
                    if max_ticks is None or ticks < max_ticks:
                        self.sleep(self.cfg["poll_seconds"])
            except (SystemExit, KeyboardInterrupt):
                self.log("stopped by signal; an unfinished trigger becomes an 'interrupted' notice at the next start")
                raise


# ---------------------------------------------------------------- status / check-config
def status_rows(cfg: dict, now: Optional[datetime] = None) -> list:
    d = Daemon(cfg, dry_run=True)
    now = now or d.now()
    tz = cfg["tz"]
    rows = [{"daemon": "running" if lock_held(cfg["log_dir"]) else "stopped", "config": str(cfg["config_path"]),
             "timezone": cfg["timezone"], "now": fmt_local(now, tz), "report_times": cfg["report_times"]}]
    reps = [r for r in d.reports.rows() if r.get("event") in ("sent", "send_failed", "missed", "interrupted")]
    nxt = d.next_report(now)
    rows.append({"last_report": ({k: reps[-1].get(k) for k in ("slot", "event", "label", "model_ok", "error")}
                                 if reps else None),
                 "next_report": fmt_local(nxt, tz) if nxt else None})
    items, qwarn = load_queue(cfg["queue_path"])
    for p in d.plan_triggers(items, now):
        rows.append({"trigger": p["id"], "trigger_at": fmt_local(p["dt"], tz) if p["dt"] else p["raw"],
                     "state": p["state"], "next_action": p["action"], "agent": p["agent"], "tools": p["tools"],
                     "error": p["error"]})
    if qwarn:
        rows.append({"warning": qwarn})
    return rows


def _registry_agents(cfg: dict) -> Optional[dict]:
    reg = Path(os.environ.get("LOGIC_AGENTS_REGISTRY") or cfg["project_dir"] / "config" / "logic_agents.yaml")
    try:
        data = yaml.safe_load(reg.read_text()) or {}
        return data.get("agents") or {}
    except (OSError, yaml.YAMLError, AttributeError):
        return None


def check_config(path, env_required: bool = True) -> list:
    """[(level, message)] with level ok | warn | error. Never prints a secret value.
    env_required=False (used by scripts/doctor.py) reports missing Telegram variables as a warning: the
    service reads them from its own definition, which the owner fills after install."""
    try:
        cfg = load_config(path)
    except ConfigError as e:
        return [("error", p) for p in e.problems]
    tz = cfg["tz"]
    now = datetime.now(UTC)
    res = [("ok", f"config {cfg['config_path']}"),
           ("ok", f"timezone {cfg['timezone']} (now {fmt_local(now, tz)})"),
           ("ok", f"report_times {', '.join(cfg['report_times'])}; on-time ±{cfg['on_time_minutes']} min, "
                  f"catch-up {cfg['catchup_minutes']} min, poll {cfg['poll_seconds']}s")]
    res.append(("ok", f"project_dir {cfg['project_dir']}") if cfg["project_dir"].is_dir()
               else ("error", f"project_dir not found: {cfg['project_dir']}"))

    items, qwarn = load_queue(cfg["queue_path"])
    res.append(("error", qwarn) if qwarn and not items else ("ok", f"queue {cfg['queue_path']}: {len(items)} items"))
    if qwarn and items:
        res.append(("warn", qwarn))
    n_examples = len(load_queue(cfg["queue_path"], examples=True)[0]) - len(items)
    if n_examples > 0:
        res.append(("warn", f"queue has {n_examples} template example item(s) (example: true): ignored; delete them"))
    plans = Daemon(cfg, dry_run=True).plan_triggers(items, now)
    agents = _registry_agents(cfg)
    for p in plans:
        if p["error"]:
            res.append(("warn", f"trigger {p['id']}: {p['error']} (trigger_at {p['raw']!r}); it will be reported "
                                f"as invalid, not run"))
        if p["agent"]:
            if not Path(cfg["logic_agent_script"]).is_file():
                res.append(("error", f"trigger {p['id']} uses trigger_agent but "
                                     f"{cfg['logic_agent_script']} is missing"))
            elif agents is not None and p["agent"] not in agents:
                res.append(("warn", f"trigger {p['id']}: agent '{p['agent']}' is not in the logic_agents registry"))
            elif agents is not None and (agents.get(p["agent"]) or {}).get("mode", "headless") != "headless":
                res.append(("warn", f"trigger {p['id']}: agent '{p['agent']}' is not mode: headless; "
                                    f"logic_agent.py will refuse the call"))
    counts = {}
    for p in plans:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    res.append(("ok", "triggers: " + (", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none")))

    pf = Path(cfg["prompt_file"])
    res.append(("ok", f"prompt_file {pf}") if pf.is_file()
               else ("error", f"prompt_file not found: {pf} (copy templates/report_prompt.md)"))
    cmd = str(cfg["claude_cmd"])
    found = shutil.which(cmd)
    if not found:
        res.append(("error", f"claude_cmd '{cmd}' not found on this PATH"))
    else:
        res.append(("ok", f"claude_cmd resolves to {found}"))
        if not os.path.isabs(cmd):
            res.append(("warn", f"claude_cmd is not absolute: the service PATH is not your shell's. "
                                f"Use claude_cmd: {found} or set PATH in the service definition"))
    try:
        Path(cfg["log_dir"]).mkdir(parents=True, exist_ok=True)
        probe = Path(cfg["log_dir"]) / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        res.append(("ok", f"log_dir {cfg['log_dir']} writable"))
    except OSError as e:
        res.append(("error", f"log_dir {cfg['log_dir']} not writable: {e}"))
    for r in gather_inputs(cfg, now)[1:]:
        res.append(("warn", f"input {r['label']} missing: {r['path']}") if r["missing"]
                   else ("ok", f"input {r['label']} age {fmt_age(r['age_s'])}" + (" STALE" if r["stale"] else "")))

    tg = cfg["telegram"]
    token = os.environ.get(str(tg.get("token_env") or ""), "").strip()
    chat = os.environ.get(str(tg.get("chat_id_env") or ""), "").strip()
    if cfg["delivery"] == "none":
        res.append(("warn", f"delivery: none - reports are printed and saved under {cfg['log_dir']}/reports/, "
                            f"not sent. Set delivery: telegram to receive them"))
    elif token and chat:
        res.append(("ok", f"telegram env {tg['token_env']} and {tg['chat_id_env']} set (values not shown)"))
        if not re.match(r"^\d+:[A-Za-z0-9_-]{20,}$", token):
            res.append(("warn", f"{tg['token_env']} does not look like a bot token (<digits>:<secret>)"))
        if not re.match(r"^(-?\d+|@\w+)$", chat):
            res.append(("warn", f"{tg['chat_id_env']} does not look like a chat id (number, or @channel)"))
    else:
        res.append(("error" if env_required else "warn",
                    f"set {tg['token_env']} and {tg['chat_id_env']} in the environment (and in the "
                    f"service definition: launchd EnvironmentVariables / systemd EnvironmentFile)"))
    return res


# ---------------------------------------------------------------- CLI
def _sigterm(signum, frame):
    raise SystemExit(128 + signum)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Reporting daemon-lite: two-layer report + scheduled triggers (docs/05)")
    ap.add_argument("--config", help="config file (default config/report_daemon.yaml or $REPORT_DAEMON_CONFIG)")
    ap.add_argument("--once", action="store_true", help="build and send one report now (outside the schedule)")
    ap.add_argument("--dry-run", action="store_true", help="with --once: print to stdout; send and record nothing")
    ap.add_argument("--no-model", action="store_true", help="with --once: top block only, no model call")
    ap.add_argument("--status", action="store_true", help="lock, last/next report, trigger states (JSON lines)")
    ap.add_argument("--check-config", action="store_true", help="validate config, queue, CLI path and environment")
    a = ap.parse_args(argv)
    if (a.dry_run or a.no_model) and not a.once:
        ap.error("--dry-run and --no-model work with --once (the loop always delivers)")
    path = config_path(a.config)

    if a.check_config:
        rows = check_config(path)
        for level, msg in rows:
            print(f"{level:<5} {msg}")
        errors = sum(1 for lv, _ in rows if lv == "error")
        print(f"-- {errors} error(s), {sum(1 for lv, _ in rows if lv == 'warn')} warning(s)")
        return 1 if errors else 0
    try:
        cfg = load_config(path)
    except ConfigError as e:
        for p in e.problems:
            print(f"[report_daemon] config error: {p}", file=sys.stderr)
        return EX_CONFIG
    if a.status:
        for row in status_rows(cfg):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.once:
        d = Daemon(cfg, dry_run=a.dry_run, use_model=not a.no_model)
        ok = d.run_report(None, "manual" + (", dry run" if a.dry_run else ""))
        if a.dry_run:
            print("--- scheduled triggers (dry run: nothing executed, nothing recorded) ---")
            items, _ = load_queue(cfg["queue_path"])
            for p in d.plan_triggers(items, d.now()):
                print(f"{p['action']:<8} {p['id']} {p['trigger_at']} {p['label'] or p['state']}"
                      + (f" ({p['error']})" if p["error"] else ""))
        return 0 if ok else 1

    signal.signal(signal.SIGTERM, _sigterm)
    try:
        Daemon(cfg).run_forever()
    except DaemonError as e:
        print(f"[report_daemon] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
