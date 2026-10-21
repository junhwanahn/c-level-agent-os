#!/usr/bin/env python3
"""Installation check for a C-Level Agent OS project. scripts/install.py runs it at the end.

  python3 scripts/doctor.py               # check the CEO project this script is in
  python3 scripts/doctor.py --root DIR    # check another CEO project

One line per check:  ✓ ok   ⚠ works, but read it   ✗ fix it before relying on the setup.
Exit code 1 when there is at least one ✗.

What it checks: Python and PyYAML, the claude CLI (absolute path, and that it accepts the flags the
runner and the daemon pass), placeholders left in the installed files ({{...}} and //ABSOLUTE/PATH/TO),
every project agent's folder, handoff file and protected zone (denied path rules must be absolute; a
missing path is a warning), template example items left in the queue, the report daemon config
(timezone, report times, queue, prompt, Telegram variables), pytest (optional) and the rendered service file.
It reads files and runs `claude --help`; it never calls a model, never prints a secret, and changes
nothing except creating the daemon's log directory when it is missing.
Dependencies: Python 3.9+; the PyYAML checks are skipped (and reported) when PyYAML is missing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
CLAUDE_SETUP_URL = "https://code.claude.com/docs/en/setup"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
PATH_MARK = "ABSOLUTE/PATH"
RUNTIME_VARS = {"now", "timezone", "slot", "top_block", "inputs"}  # filled by report_daemon.py at run time
TEMPLATE_EXAMPLE_IDS = {"send_partner_reply_0115", "reauth_video_api_0116", "confirm_prior_commitment_0117",
                        "weekly_review_0116", "rerun_analysis_0114"}
CLAUDE_CANDIDATES = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude",
                     "/usr/local/bin/claude", "~/.npm-global/bin/claude")
CLI_FLAGS = ("--print", "--output-format", "--model", "--append-system-prompt", "--resume",
             "--allowedTools", "--disallowedTools", "--tools")
MODES = ("headless", "live", "in_session")
REQUIRED_FILES = ("CLAUDE.md", "HANDOFF.md", "config/owner_action_queue.yaml", "config/logic_agents.yaml",
                  "config/report_daemon.yaml", "config/report_prompt.md", "config/autonomy_policy.md",
                  "config/access_control.yaml", "scripts/logic_agent.py", "scripts/report_daemon.py")
REQUIRED_DIRS = (".claude/agents", "advisory_drafts/reviews")
_RULE = re.compile(r"^(\w+)\((.*)\)$")
_GLOB = re.compile(r"[*?\[]")


class Report:
    """Collects (level, message) rows and prints each one as it comes."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.rows: list = []
        enc = getattr(self.out, "encoding", None) or "utf-8"
        try:
            "✓⚠✗".encode(enc)
            self.sym = {"ok": "✓", "warn": "⚠", "fail": "✗"}
        except (UnicodeEncodeError, LookupError):
            self.sym = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}

    def add(self, level: str, msg: str) -> None:
        self.rows.append((level, msg))
        print(f"{self.sym[level]} {msg}", file=self.out)

    def ok(self, msg):
        self.add("ok", msg)

    def warn(self, msg):
        self.add("warn", msg)

    def fail(self, msg):
        self.add("fail", msg)

    def count(self, level: str) -> int:
        return sum(1 for lv, _ in self.rows if lv == level)

    @property
    def failures(self) -> list:
        return [m for lv, m in self.rows if lv == "fail"]


# ---------------------------------------------------------------- helpers
def _rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _load_yaml(yaml, path: Path):
    try:
        return yaml.safe_load(path.read_text()), None
    except (OSError, yaml.YAMLError) as e:
        return None, str(e).splitlines()[0] if str(e) else type(e).__name__


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_agent_path(root: Path, raw) -> Path:
    p = Path(os.path.expanduser(str(raw or "")))
    return p if p.is_absolute() else (root / p).resolve()


def rule_path(rule: str) -> tuple:
    """(tool, absolute path or None, problem or None) for a path rule such as Edit(//abs/**)."""
    m = _RULE.match(str(rule).strip())
    if not m:
        return None, None, None
    tool, arg = m.group(1), m.group(2).strip()
    if tool not in ("Edit", "Read", "Write", "NotebookEdit", "MultiEdit"):
        return tool, None, None  # Bash(...) and other patterns are not paths
    if arg.startswith("//"):
        return tool, arg[1:], None
    if arg.startswith("~/"):
        return tool, os.path.expanduser(arg), None
    if arg.startswith("/"):
        return tool, None, (f"{rule}: a single leading slash is relative to the settings source, not absolute; "
                            f"write Edit(/{arg}) for the absolute path")
    return tool, None, f"{rule}: relative to the agent's working directory; write an absolute path (Edit(//...))"


def glob_base(path: str) -> str:
    """The part of a path pattern before the first glob character: what must exist on disk."""
    m = _GLOB.search(path)
    base = path[:m.start()] if m else path
    return base.rstrip("/") or "/"


def find_claude(configured: Optional[str]) -> tuple:
    """(resolved absolute path or None, path found outside PATH or None)."""
    cmd = str(configured or "claude")
    expanded = os.path.expanduser(cmd)
    if os.path.isabs(expanded):
        resolved = expanded if os.access(expanded, os.X_OK) and Path(expanded).is_file() else None
    else:
        resolved = shutil.which(cmd)
    if resolved:
        return resolved, None
    for c in CLAUDE_CANDIDATES:
        p = os.path.expanduser(c)
        if os.access(p, os.X_OK) and Path(p).is_file():
            return None, p
    return None, None


def missing_flags(claude: str, flags) -> Optional[list]:
    """Flags absent from `claude --help`, or None when the help text cannot be read."""
    try:
        r = subprocess.run([claude, "--help"], capture_output=True, text=True, timeout=30,
                           env={k: v for k, v in os.environ.items()
                                if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")})
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (r.stdout or "") + (r.stderr or "")
    if "--" not in text:
        return None
    return [f for f in flags if not re.search(rf"(^|[\s,]){re.escape(f)}\b", text)]


# ---------------------------------------------------------------- checks
def check_python(rep: Report) -> None:
    v = sys.version_info
    msg = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    rep.ok(msg) if v >= (3, 9) else rep.fail(f"{msg}: Python 3.9 or newer is required")


def check_yaml(rep: Report):
    try:
        import yaml
    except ImportError:
        rep.fail(f"PyYAML is missing: {Path(sys.executable).name} -m pip install --user pyyaml  "
                 f"(macOS has no 'pip' command; this form always works). The checks that read YAML are skipped")
        return None
    rep.ok(f"PyYAML {getattr(yaml, '__version__', '?')}")
    return yaml


def check_files(rep: Report, root: Path) -> None:
    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    missing += [d + "/" for d in REQUIRED_DIRS if not (root / d).is_dir()]
    if missing:
        for f in missing:
            rep.fail(f"missing: {f} (run python3 scripts/install.py, or see README 'Manual install')")
    else:
        rep.ok(f"all {len(REQUIRED_FILES) + len(REQUIRED_DIRS)} files and folders the charter refers to exist")
    agents = sorted(p.stem for p in (root / ".claude" / "agents").glob("*.md"))
    if (root / ".claude" / "agents").is_dir():
        rep.ok(f"C-level role agents: {', '.join(agents)}") if agents else rep.warn(
            "no role agents in .claude/agents/ (copy templates/agents/*.md, or re-run install.py)")


def scan_targets(root: Path) -> list:
    files = [root / "CLAUDE.md", root / "HANDOFF.md"]
    files += sorted((root / ".claude" / "agents").glob("*.md"))
    files += sorted((root / "config").glob("*.yaml")) + sorted((root / "config").glob("*.md"))
    files += sorted((root / "service").glob("*")) if (root / "service").is_dir() else []
    return [f for f in files if f.is_file()]


def check_placeholders(rep: Report, root: Path) -> None:
    clean = True
    for f in scan_targets(root):
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        holes, paths = [], []
        for n, line in enumerate(lines, 1):
            if f.suffix in (".yaml", ".yml") and line.lstrip().startswith("#"):
                continue  # comments change nothing
            names = [x for x in PLACEHOLDER.findall(line)
                     if not (f.name == "report_prompt.md" and x in RUNTIME_VARS)]
            holes += [(n, x) for x in names]
            if PATH_MARK in line and f.name != "logic_agents.yaml":  # the registry check reports those per agent
                paths.append((n, line.strip()))
        rel = _rel(root, f)
        if holes:
            clean = False
            more = f" (and {len(holes) - 1} more)" if len(holes) > 1 else ""
            rep.fail(f"{rel}: {len(holes)} placeholder(s) left, first at line {holes[0][0]}: "
                     f"{{{{{holes[0][1]}}}}}{more}")
        for n, line in paths[:3]:
            clean = False
            rep.fail(f"{rel}:{n}: placeholder path left (protects or points to nothing): {line[:120]}")
        if len(paths) > 3:
            rep.fail(f"{rel}: and {len(paths) - 3} more placeholder paths")
    if clean:
        rep.ok("no placeholders left ({{...}} or //ABSOLUTE/PATH/TO) in the installed files")


def check_claude(rep: Report, root: Path, yaml) -> None:
    configured, effort = None, None
    if yaml:
        cfg, _ = _load_yaml(yaml, root / "config" / "report_daemon.yaml")
        configured = (cfg or {}).get("claude_cmd") if isinstance(cfg, dict) else None
        reg, _ = _load_yaml(yaml, root / "config" / "logic_agents.yaml")
        if isinstance(reg, dict):
            effort = (reg.get("defaults") or {}).get("effort")
    resolved, elsewhere = find_claude(configured)
    if not resolved:
        if elsewhere:
            rep.fail(f"claude CLI not on this PATH but found at {elsewhere}: set claude_cmd: {elsewhere} "
                     f"in config/report_daemon.yaml")
        else:
            rep.fail(f"claude CLI not found. Install Claude Code ({CLAUDE_SETUP_URL}), run `claude` once to "
                     f"sign in, then run python3 scripts/install.py again (or set claude_cmd)")
        return
    rep.ok(f"claude CLI: {resolved}")
    if not os.path.isabs(os.path.expanduser(str(configured or "claude"))):
        rep.warn(f"claude_cmd in config/report_daemon.yaml is '{configured or 'claude'}', not an absolute path: a "
                 f"service does not use your shell's PATH. Set claude_cmd: {resolved}")
    flags = list(CLI_FLAGS) + (["--effort"] if effort else [])
    missing = missing_flags(resolved, flags)
    if missing is None:
        rep.warn("could not read `claude --help`; check the flags in docs/04 against your CLI by hand")
    elif missing:
        hint = " (or delete defaults.effort in config/logic_agents.yaml)" if "--effort" in missing else ""
        rep.warn(f"`claude --help` does not list {', '.join(missing)}, which the runner or daemon pass: "
                 f"update Claude Code (claude update){hint}")
    else:
        rep.ok("claude CLI lists every flag the runner and the daemon pass")


def check_registry(rep: Report, root: Path, yaml) -> list:
    """Checks config/logic_agents.yaml; returns the absolute protected path rules found."""
    p = root / "config" / "logic_agents.yaml"
    if not p.is_file():
        return []
    reg, err = _load_yaml(yaml, p)
    if err or not isinstance(reg, dict) or "agents" not in reg:
        rep.fail(f"config/logic_agents.yaml cannot be read as a registry ({err or 'no agents section'})")
        return []
    status = reg.get("permissions_status")
    if status == "proposed":
        rep.ok("registry permissions_status: proposed (allowlists are passed only after you confirm them)")
    elif status == "confirmed":
        pc = reg.get("permissions_confirmed") or {}
        lack = [k for k in ("date", "by", "quote", "scope") if not str(pc.get(k) or "").strip()]
        if lack:
            rep.fail(f"permissions_status is confirmed but permissions_confirmed lacks {', '.join(lack)}: "
                     f"record the owner's own words, date and scope")
        else:
            rep.ok(f"registry permissions_status: confirmed ({pc.get('date')}, by {pc.get('by')})")
    else:
        rep.fail(f"permissions_status must be proposed or confirmed (got {status!r})")
    protected = []
    for r in ((reg.get("defaults") or {}).get("denied_tools") or []):
        if PATH_MARK in str(r) or "{{" in str(r):
            rep.fail(f"defaults.denied_tools: placeholder rule protects nothing: {r}")
    agents = reg.get("agents") or {}
    if not agents:
        rep.warn("no project agents registered: the CEO works alone until you add one "
                 "(re-run install.py, or edit config/logic_agents.yaml)")
    for name, a in agents.items():
        a = a or {}
        mode = a.get("mode", "headless")
        path = resolve_agent_path(root, a.get("path"))
        problems = 0
        if mode not in MODES:
            rep.fail(f"agent {name}: mode must be one of {', '.join(MODES)} (got {mode!r})")
            problems += 1
        if not path.is_dir():
            (rep.fail if mode == "headless" else rep.warn)(f"agent {name}: folder not found: {path}")
            problems += 1
        handoff = path / str(a.get("handoff") or "HANDOFF.md")
        if path.is_dir() and not handoff.is_file():
            rep.warn(f"agent {name}: handoff file not found: {handoff} (create it; the agent reads it first)")
        denied = [str(r) for r in (a.get("denied_tools") or [])]
        allowed = [str(r) for r in (a.get("allowed_tools") or [])]
        path_rules = 0
        for kind, rules in (("denied", denied), ("allowed", allowed)):
            for r in rules:
                if PATH_MARK in r or "{{" in r:
                    rep.fail(f"agent {name}: {kind} rule is still a placeholder: {r}")
                    problems += 1
                    continue
                tool, abs_path, problem = rule_path(r)
                if tool in ("Write", "NotebookEdit", "MultiEdit"):
                    rep.warn(f"agent {name}: Claude Code never consults {r}; use Edit(...) for the same path")
                if problem:
                    (rep.fail if kind == "denied" else rep.warn)(f"agent {name}: {kind} rule {problem}")
                    problems += kind == "denied"
                elif abs_path:
                    if kind == "denied":
                        path_rules += tool in ("Edit", "Read")
                        protected.append(r)
                    if not Path(glob_base(abs_path)).exists():
                        rep.warn(f"agent {name}: {kind} rule points to a path that does not exist (yet): {r}")
        if path_rules == 0 and not any(PATH_MARK in r for r in denied):
            if str(a.get("protected_zone") or "").strip().lower() == "none":
                rep.warn(f"agent {name}: NO PROTECTED ZONE (protected_zone: none, confirmed by the owner): "
                         f"agents are not denied any file in {path}")
            else:
                rep.fail(f"agent {name}: no protected zone decided. List the files that move money or control "
                         f"live services in denied_tools (Edit(//absolute/path/**)), or set protected_zone: none "
                         f"only if the owner confirms there are none")
                problems += 1
        if not problems:
            rep.ok(f"agent {name} ({mode}): folder {path}, {path_rules} protected path rule(s)")
    return protected


def check_ceo_settings(rep: Report, root: Path, protected: list) -> None:
    if not protected:
        return
    p = root / ".claude" / "settings.json"
    try:
        deny = set((json.loads(p.read_text()).get("permissions") or {}).get("deny") or [])
    except (OSError, ValueError, AttributeError):
        deny = set()
    missing = [r for r in protected if r.startswith(("Edit(", "Read(")) and r not in deny]
    if missing:
        rep.warn(f"the CEO session itself is not denied {len(missing)} protected path rule(s) (first: {missing[0]}): "
                 f"add them to permissions.deny in .claude/settings.json (install.py does this)")
    else:
        rep.ok(f"the CEO session denies the same protected paths (.claude/settings.json, {len(protected)} rule(s))")


def check_queue(rep: Report, root: Path, yaml) -> None:
    p = root / "config" / "owner_action_queue.yaml"
    if not p.is_file():
        return
    data, err = _load_yaml(yaml, p)
    if err:
        rep.fail(f"config/owner_action_queue.yaml cannot be parsed: {err}")
        return
    data = data or []
    if not isinstance(data, list):
        rep.fail("config/owner_action_queue.yaml must be a YAML list of items (or [] when empty)")
        return
    examples = [i for i in data if isinstance(i, dict)
                and (i.get("example") is True or i.get("id") in TEMPLATE_EXAMPLE_IDS)]
    if examples:
        rep.fail(f"queue still has {len(examples)} template example item(s) "
                 f"({', '.join(str(i.get('id')) for i in examples[:3])}): delete them; otherwise they look like "
                 f"overdue owner actions")
    else:
        owner = [i for i in data if isinstance(i, dict) and i.get("owner") == "human" and i.get("status") == "open"]
        rep.ok(f"queue: {len(data)} item(s), {len(owner)} open for you, no template examples")


def check_report_config(rep: Report, root: Path) -> None:
    script, cfg = root / "scripts" / "report_daemon.py", root / "config" / "report_daemon.yaml"
    if not (script.is_file() and cfg.is_file()):
        return
    try:
        rd = _load_module(script, "_doctor_report_daemon")
        rows = rd.check_config(cfg, env_required=False)
    except Exception as e:  # a broken install must still get a readable line
        rep.fail(f"report daemon check could not run: {type(e).__name__}: {e}")
        return
    for level, msg in rows:
        if msg.startswith(("claude_cmd", "config ")):
            continue  # the claude check above covers it
        {"ok": rep.ok, "warn": rep.warn, "error": rep.fail}.get(level, rep.warn)(f"report daemon: {msg}")


def check_pytest(rep: Report) -> None:
    if importlib.util.find_spec("pytest"):
        rep.ok("pytest installed (optional): python3 -m pytest tests -q")
    else:
        rep.warn("pytest not installed (optional, only for the tests): python3 -m pip install --user pytest, "
                 "or run python3 -m unittest discover tests")


def _service_python(text: str, kind: str) -> Optional[str]:
    if kind == "launchd":
        m = re.search(r"<key>ProgramArguments</key>\s*<array>\s*(?:<!--.*?-->\s*)*<string>([^<]+)</string>", text, re.S)
    else:
        m = re.search(r"^ExecStart=\"?([^\s\"]+)", text, re.M)
    return m.group(1) if m else None


def check_service(rep: Report, root: Path) -> None:
    sdir = root / "service"
    files = sorted(sdir.glob("*.plist")) + sorted(sdir.glob("*.service")) if sdir.is_dir() else []
    if not files:
        rep.warn("no rendered service file in service/: re-run install.py, or fill templates/service/ by hand "
                 "(README section 3)")
        return
    for f in files:
        kind = "launchd" if f.suffix == ".plist" else "systemd"
        text = f.read_text(errors="replace")
        rel = _rel(root, f)
        if PATH_MARK in text:
            rep.fail(f"{rel}: placeholder paths left; re-run install.py")
            continue
        rep.ok(f"service file rendered ({kind}): {rel}")
        py = _service_python(text, kind)
        if not py or not Path(py).is_file():
            rep.fail(f"{rel}: the python3 it runs does not exist: {py}")
        else:
            try:
                r = subprocess.run([py, "-c", "import yaml"], capture_output=True, timeout=20)
                if r.returncode != 0:
                    rep.warn(f"{rel}: {py} has no PyYAML; install it for that python ({py} -m pip install --user pyyaml)")
            except (OSError, subprocess.TimeoutExpired):
                rep.warn(f"{rel}: could not run {py}")
        if "REPLACE_WITH_" in text:
            rep.warn(f"{rel}: the Telegram token and chat id are still REPLACE_WITH_...: you put them into the "
                     f"installed copy (owner action in the queue), never into git")
        installed = (Path.home() / "Library" / "LaunchAgents" / f.name if kind == "launchd"
                     else Path.home() / ".config" / "systemd" / "user" / f.name)
        if installed.is_file():
            rep.ok(f"service registered: {installed}")
        else:
            rep.warn(f"service not registered yet ({installed} missing): README section 3, or the owner action "
                     f"in config/owner_action_queue.yaml")


# ---------------------------------------------------------------- main
def run(root, out=None) -> Report:
    root = Path(root).expanduser().resolve()
    rep = Report(out)
    print(f"doctor: {root}", file=rep.out)
    check_python(rep)
    yaml = check_yaml(rep)
    check_files(rep, root)
    check_placeholders(rep, root)
    check_claude(rep, root, yaml)
    if yaml:
        protected = check_registry(rep, root, yaml)
        check_ceo_settings(rep, root, protected)
        check_queue(rep, root, yaml)
        check_report_config(rep, root)
    check_pytest(rep)
    check_service(rep, root)
    print(f"doctor: {rep.count('ok')} ok, {rep.count('warn')} warning(s), {rep.count('fail')} failure(s)",
          file=rep.out)
    if rep.failures:
        print("Fix every ✗ line, then run python3 scripts/doctor.py again.".replace("✗", rep.sym["fail"].strip()),
              file=rep.out)
    return rep


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Check a C-Level Agent OS installation")
    ap.add_argument("--root", default=str(HERE.parent), help="CEO project root (default: the parent of scripts/)")
    a = ap.parse_args(argv)
    return 1 if run(a.root).failures else 0


if __name__ == "__main__":
    sys.exit(main())
