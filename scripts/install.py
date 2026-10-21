#!/usr/bin/env python3
"""One-step installer for C-Level Agent OS.

  python3 scripts/install.py                         # a few plain questions; installs into this folder
  python3 scripts/install.py --dest ~/my-company     # installs into another folder
  python3 scripts/install.py --answers answers.yaml  # no questions (for agents and tests); see ANSWERS below
  python3 scripts/install.py --doctor [--dest DIR]   # only run the check (same as scripts/doctor.py)

What it does, in order:
  1. creates the folders (config/, scripts/, tests/, .claude/agents/, state/report_daemon/,
     advisory_drafts/reviews/, service/)
  2. writes CLAUDE.md (the CEO charter) and the role agents from templates/, filling every placeholder
     from your answers: company, owner, language, projects (name, folder, how you work with it, handoff
     file), each project's protected zone, data sources, time zone, report times, messenger
  3. protected zone: every path you name becomes an absolute deny rule (Edit(//abs/path/**)) for that
     project's agent and for the CEO session (.claude/settings.json). Leaving it empty needs an explicit
     "none"; the summary then shows NO PROTECTED ZONE in a box
  4. writes the owner action queue without the template's examples (plus your real setup steps),
     the files the charter refers to (config/autonomy_policy.md, config/access_control.yaml, HANDOFF.md,
     advisory_drafts/reviews/), config/report_daemon.yaml and a service file for launchd or systemd
  5. copies the runner, the daemon, this check and their tests, merges .gitignore, runs `git init`
  6. runs scripts/doctor.py. The install is COMPLETE only when doctor reports no failure; a placeholder
     or placeholder path left anywhere means NOT COMPLETE (exit code 1)

Existing files are never overwritten silently: identical files are skipped, different ones are kept
unless you agree (interactive) or pass --force (a .bak-<time> copy is kept). HANDOFF.md and the queue
are your records: they are never replaced (only template examples are removed from the queue).
Secrets are never asked for or written: the Telegram token goes into the installed service copy by you.

ANSWERS (YAML; every key optional except each project's protected-zone decision):
  company: "Saebyeok Data Lab"         company_one_line: "one-person data analysis consulting"
  owner: "Mina Seo"                    language: "English"
  timezone: "Asia/Seoul"               report_times: ["08:00", "20:00"]
  messenger: telegram                  # telegram | none (reports only printed and saved)
  roles: [cto, cro, cfo, cmo]          block_budget: 3
  owner_instruction: "Agents prepare everything up to the last click; I take only that click."
  live_services: "newsletter sender"   # services agents must never start/stop/restart
  data_sources: [{name: revenue, source: "~/shop/revenue.csv"}]
  projects:
    - name: newsletter                 # letters, digits, _ and -
      description: "paid newsletter"
      path: "~/projects/newsletter"    # absolute, ~, or relative to --dest
      mode: headless                   # headless | live | in_session
      handoff: HANDOFF.md              # relative to the project folder
      protected: ["billing/**", "config/pricing.yaml"]   # relative to the project folder, ~ or absolute
    - name: notes
      path: "~/projects/notes"
      protected: []
      no_protected_zone: true          # ONLY when the owner said nothing there moves money or runs live
  no_protected_zone: true              # needed only when there are no projects
  claude_cmd: /absolute/path/to/claude # default: detected
  service: auto                        # auto | launchd | systemd | none
  git_init: true
A project without `protected` (and without no_protected_zone: true) keeps the template's placeholder
rule, so the install is reported NOT COMPLETE until you answer it.

Dependencies: Python 3.9+ and PyYAML (interactive mode offers to install PyYAML for you).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = TEMPLATE_ROOT / "templates"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402  (stdlib only at import time)

ROLES = ("cto", "cro", "cfo", "cmo")
MODES = ("headless", "live", "in_session")
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
GROUP = re.compile(r"^(UNIT|CRITICAL|METRIC)_(\d+)_([A-Z_]+)$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
DEFAULT_INSTRUCTION = "Agents prepare everything up to the last click; I take only that click."
DIRS = ("config", "scripts", "tests", ".claude/agents", "state/report_daemon", "advisory_drafts/reviews", "service")
COPIES = (("scripts/logic_agent.py", "scripts/logic_agent.py"), ("scripts/report_daemon.py", "scripts/report_daemon.py"),
          ("scripts/doctor.py", "scripts/doctor.py"), ("tests/test_logic_agent.py", "tests/test_logic_agent.py"),
          ("tests/test_report_daemon.py", "tests/test_report_daemon.py"), ("requirements.txt", "requirements.txt"),
          ("requirements-dev.txt", "requirements-dev.txt"), ("templates/report_prompt.md", "config/report_prompt.md"))
GITIGNORE = ("state/", "__pycache__/", "*.pyc", ".pytest_cache/", ".DS_Store", "service/", "*.bak-*")

# ---------------------------------------------------------------- messages (en, ko)
MSG = {
    "intro": ("C-Level Agent OS installer. Folder: {dest}\nPress Enter to accept the value in [brackets].",
              "C-Level Agent OS 설치. 설치 폴더: {dest}\n[대괄호] 안의 값이 기본값이며, Enter 를 누르면 그대로 쓴다."),
    "reuse": ("Found the answers from your last install. Use them as defaults?",
              "지난 설치의 답변이 있다. 기본값으로 쓸까?"),
    "company": ("Company name", "회사 이름"),
    "company_one_line": ("In one line, what does the company do?", "회사가 하는 일 한 줄"),
    "owner": ("Your name (the owner, the only human)", "대표(유일한 사람) 이름"),
    "language": ("Language the CEO agent reports in", "CEO 에이전트 보고 언어"),
    "timezone": ("Your time zone (IANA name, e.g. Asia/Seoul, Europe/Berlin)", "타임존(IANA 이름, 예: Asia/Seoul)"),
    "report_times": ("Report times, 24h, comma-separated", "보고 시각(24시간제, 쉼표로 구분)"),
    "messenger": ("How should reports reach you? telegram, or none (only saved to files for now)",
                  "보고를 어디로 받을까? telegram, 또는 none(당분간 파일로만 저장)"),
    "roles": ("C-level roles to install (any of cto, cro, cfo, cmo)", "설치할 C-level 역할(cto, cro, cfo, cmo 중)"),
    "owner_instruction": ("In your own words: what may the CEO agent do without asking you? (saved verbatim)",
                          "대표 본인의 말로: CEO 에이전트가 묻지 않고 해도 되는 일은? (그대로 저장된다)"),
    "add_project": ("Add a project folder the CEO should manage (a business unit with its own folder)?",
                    "CEO 가 관리할 프로젝트 폴더(자기 폴더가 있는 사업 단위)를 추가할까?"),
    "add_another": ("Add another project?", "프로젝트를 하나 더 추가할까?"),
    "keep_projects": ("Keep these {n} projects from last time: {names}?", "지난번 프로젝트 {n}개를 유지할까: {names}?"),
    "p_name": ("  Short name (letters, digits, _ or -)", "  짧은 이름(영문·숫자·_·-)"),
    "p_desc": ("  One line on what it does", "  하는 일 한 줄"),
    "p_path": ("  Its folder (absolute, or starting with ~)", "  폴더 경로(절대경로 또는 ~ 로 시작)"),
    "p_path_missing": ("  That folder does not exist: {path}. Use it anyway?", "  그 폴더가 없다: {path}. 그래도 쓸까?"),
    "p_mode": ("  How do you work with it?\n"
               "    1 = the CEO calls it in the background (headless, recommended)\n"
               "    2 = you work in it yourself in a terminal every day (live)\n"
               "    3 = the CEO runs it as a helper inside its own session (in_session)\n  Choose",
               "  이 프로젝트와 어떻게 일하나?\n"
               "    1 = CEO 가 백그라운드로 호출(headless, 권장)\n"
               "    2 = 대표가 매일 직접 터미널에서 사용(live)\n"
               "    3 = CEO 세션 안에서 보조로 실행(in_session)\n  선택"),
    "p_handoff": ("  Its handoff file, the note it reads first (relative to its folder)",
                  "  인계 문서(가장 먼저 읽는 메모, 프로젝트 폴더 기준 경로)"),
    "p_protected_intro": ("  Protected zone of {name}: files or folders that move real money or control live services\n"
                          "  (payments, pricing, live trading, send jobs). Agents will be denied changing them.\n"
                          "  One per line: relative to {path}, ~ or absolute. Empty line = done.",
                          "  {name} 의 보호 구역: 실제 돈이 움직이거나 라이브 서비스를 제어하는 파일·폴더\n"
                          "  (결제·가격·실거래·발송 작업 등). 에이전트는 이 경로를 수정할 수 없게 된다.\n"
                          "  한 줄에 하나: {path} 기준 상대경로, ~ 또는 절대경로. 빈 줄 = 끝."),
    "p_protected_item": ("  protected path", "  보호 경로"),
    "p_protected_missing": ("  Not found: {path}. Keep it anyway (it will be protected once it exists)?",
                            "  없는 경로다: {path}. 그래도 넣을까(생기면 그때부터 보호된다)?"),
    "p_placeholder": ("  That is a template placeholder, not a real path. Type the real path.",
                      "  그건 템플릿 자리표시자다. 실제 경로를 입력하라."),
    "p_protected_none": ("  You entered no protected zone for {name}. If nothing there moves money or controls a live\n"
                         "  service, type none to confirm. Press Enter to add paths instead",
                         "  {name} 의 보호 구역을 입력하지 않았다. 돈이 움직이거나 라이브 서비스를 제어하는 파일이\n"
                         "  정말 없으면 none 을 입력해 확인한다. 경로를 넣으려면 그냥 Enter"),
    "no_projects_none": ("You added no project, so there is no protected zone. Type none to confirm, or press Enter to add one",
                         "프로젝트를 추가하지 않아 보호 구역이 없다. 확인하려면 none, 추가하려면 Enter"),
    "live_services": ("Live services agents must never start, stop or restart (names, optional)",
                      "에이전트가 시작·중지·재시작하면 안 되는 라이브 서비스 이름(선택)"),
    "data_sources": ("Key numbers the CEO should read, one per line as name = path or API (optional; empty line = done)",
                     "CEO 가 읽을 핵심 수치, 한 줄에 하나씩 이름 = 경로 또는 API(선택, 빈 줄 = 끝)"),
    "data_item": ("  data source", "  데이터 소스"),
    "overwrite": ("{rel} already exists and is different. Replace it (a backup copy is kept)?",
                  "{rel} 이(가) 이미 있고 내용이 다르다. 바꿀까(백업 사본을 남긴다)?"),
    "yaml_missing": ("PyYAML (the only dependency) is missing. The command that installs it:\n  {cmd}",
                     "필수 의존성 PyYAML 이 없다. 설치 명령:\n  {cmd}"),
    "yaml_install_now": ("Run it now?", "지금 실행할까?"),
    "yaml_failed": ("Installing PyYAML failed. Use a virtual environment instead:\n"
                    "  python3 -m venv .venv && .venv/bin/python -m pip install pyyaml\n"
                    "  .venv/bin/python scripts/install.py",
                    "PyYAML 설치에 실패했다. 가상환경으로 진행한다:\n"
                    "  python3 -m venv .venv && .venv/bin/python -m pip install pyyaml\n"
                    "  .venv/bin/python scripts/install.py"),
    "invalid": ("  {err}", "  {err}"),
    "closed": ("No answer (input closed). For an install without questions use --answers FILE.",
               "입력이 닫혀 답을 받을 수 없다. 질문 없이 설치하려면 --answers FILE 을 쓴다."),
}


def say(lang: str, key: str, **kw) -> str:
    en, ko = MSG[key]
    return (ko if lang == "ko" else en).format(**kw)


def detect_lang() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v:
            return "ko" if v.lower().startswith("ko") else "en"
    return "en"


# ---------------------------------------------------------------- detection
def detect_timezone() -> str:
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text().strip() or "UTC"
    except OSError:
        return "UTC"


def git_user_name() -> str:
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"


def tz_error(name: str) -> Optional[str]:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return None
    except Exception:
        return f"'{name}' is not an IANA time zone name (examples: Asia/Seoul, Europe/Berlin, America/New_York, UTC)"


def parse_times(raw) -> tuple:
    """(['08:00', ...], error or None). Accepts a list or 'HH:MM, HH:MM'; pads 8:00 to 08:00."""
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw or "").strip())
    out = []
    for x in items:
        if x in ("", None):
            continue
        if isinstance(x, int):
            return [], f"{x} is a number: write times as text such as \"20:00\""
        m = HHMM.match(str(x).strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return [], f"'{x}' is not a 24h time such as 08:00"
        out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return (sorted(set(out)), None) if out else ([], "at least one report time is needed, e.g. 08:00")


def is_placeholder(s) -> bool:
    return any(m in str(s) for m in (doctor.PATH_MARK, "{{"))


# ---------------------------------------------------------------- protected zone -> rules
def protected_rules(entry, base: Path) -> tuple:
    """(display pattern, [deny rules]). Paths become absolute Claude Code rules: Edit(//abs/path/**)."""
    s = str(entry).strip()
    if doctor._RULE.match(s):
        return s, [s]  # a full rule, e.g. Edit(//Users/me/shop/live/**)
    if is_placeholder(s):
        p = "/" + s.lstrip("/")
        return p, [f"Edit(/{p})"]
    trailing = s.endswith("/")
    p = s[1:] if s.startswith("//") else os.path.expanduser(s)
    if not os.path.isabs(p):
        p = str(base / p)
    p = os.path.normpath(p) if doctor._GLOB.search(p) else os.path.realpath(p)
    if doctor._GLOB.search(p) or Path(p).is_file():
        disp, pats = p, [p]
    elif Path(p).is_dir() or trailing:
        disp = p.rstrip("/") + "/**"
        pats = [disp]
    else:  # not there yet: cover a file or a folder of that name
        disp, pats = p, [p, p.rstrip("/") + "/**"]
    return disp, [f"Edit(/{x})" for x in pats]


# ---------------------------------------------------------------- answers
def _abs(dest: Path, raw) -> Path:
    p = Path(os.path.expanduser(str(raw or "")))
    return (p if p.is_absolute() else dest / p).resolve()


def normalize(raw: dict, dest: Path, today: date) -> tuple:
    """(plan, problems). Applies defaults; every problem leaves a visible placeholder or a doctor failure."""
    problems = []
    raw = dict(raw or {})
    plan = {
        "company": str(raw.get("company") or dest.name.replace("-", " ").replace("_", " ").title()).strip(),
        "company_one_line": str(raw.get("company_one_line") or "a one-person company run with an AI executive team"),
        "owner": str(raw.get("owner") or git_user_name() or getpass.getuser()),
        "language": str(raw.get("language") or "English"),
        "timezone": str(raw.get("timezone") or detect_timezone()).strip(),
        "messenger": str(raw.get("messenger") or "telegram").strip().lower(),
        "owner_instruction": str(raw.get("owner_instruction") or DEFAULT_INSTRUCTION).strip(),
        "block_budget": raw.get("block_budget") or 3,
        "live_services": str(raw.get("live_services") or "").strip(),
        "service": str(raw.get("service") or "auto").strip().lower(),
        "git_init": raw.get("git_init", True) is not False,
        "today": today.isoformat(),
    }
    err = tz_error(plan["timezone"])
    if err:
        problems.append(f"timezone: {err}")
    times, err = parse_times(raw.get("report_times") or ["08:00", "20:00"])
    plan["report_times"] = times or [str(x) for x in (raw.get("report_times") or [])]
    if err:
        problems.append(f"report_times: {err}")
    if plan["messenger"] not in ("telegram", "none"):
        problems.append(f"messenger must be telegram or none (got {plan['messenger']!r})")
        plan["messenger"] = "telegram"
    roles = raw.get("roles", list(ROLES))
    roles = re.split(r"[,\s]+", roles) if isinstance(roles, str) else list(roles or [])
    roles = [str(r).strip().lower() for r in roles if str(r).strip()]
    bad = [r for r in roles if r not in ROLES]
    if bad:
        problems.append(f"unknown roles {bad}: choose from {', '.join(ROLES)}")
    plan["roles"] = [r for r in ROLES if r in roles]
    if plan["service"] == "auto":
        plan["service"] = {"darwin": "launchd", "linux": "systemd"}.get(sys.platform, "none")
    if plan["service"] not in ("launchd", "systemd", "none"):
        problems.append(f"service must be auto, launchd, systemd or none (got {plan['service']!r})")
        plan["service"] = "none"
    # always an absolute path when one can be found: a service does not use your shell's PATH
    resolved, elsewhere = doctor.find_claude(raw.get("claude_cmd"))
    plan["claude_cmd"] = resolved or elsewhere or str(raw.get("claude_cmd") or "claude")

    sources = []
    for s in raw.get("data_sources") or []:
        if isinstance(s, dict) and s.get("name"):
            sources.append({"NAME": str(s["name"]), "SOURCE": str(s.get("source") or "")})
        elif isinstance(s, str) and re.search(r"[=:]", s):
            k, v = re.split(r"\s*[=:]\s*", s, maxsplit=1)
            sources.append({"NAME": k.strip(), "SOURCE": v.strip()})
    plan["data_sources"] = sources

    projects, seen = [], set()
    for n, p in enumerate(raw.get("projects") or []):
        p = dict(p or {})
        name = str(p.get("name") or "").strip()
        if not NAME_RE.match(name):
            problems.append(f"projects[{n}]: name {name!r} must start with a letter and use letters, digits, _ or -")
            name = name or f"project_{n + 1}"
        if name in seen:
            problems.append(f"projects[{n}]: name {name!r} is used twice")
        seen.add(name)
        if not p.get("path"):
            problems.append(f"project {name}: folder path is missing")
        path = _abs(dest, p.get("path") or f"//ABSOLUTE/PATH/TO/{name}")
        mode = str(p.get("mode") or "headless").strip()
        if mode not in MODES:
            problems.append(f"project {name}: mode must be one of {', '.join(MODES)} (got {mode!r})")
        proj = {"name": name, "description": str(p.get("description") or "").strip(), "path": str(path),
                "mode": mode, "handoff": str(p.get("handoff") or "HANDOFF.md"), "protected_raw": [],
                "protected": [], "rules": []}
        entries = p.get("protected")
        if isinstance(entries, str):
            entries = [x for x in re.split(r"[\n,]+", entries) if x.strip()]
        if entries:
            proj["state"] = "paths"
            for e in entries:
                disp, rules = protected_rules(e, path)
                proj["protected_raw"].append(str(e))
                proj["protected"].append(disp)
                proj["rules"] += rules
                if is_placeholder(e):
                    problems.append(f"project {name}: protected path {e!r} is a placeholder, not a real path")
        elif p.get("no_protected_zone") is True:
            proj["state"] = "none"
        else:
            proj["state"] = "unanswered"
            ph = f"/ABSOLUTE/PATH/TO/{name}/live/**"
            proj["protected"], proj["rules"] = [ph], [f"Edit(/{ph})"]
            problems.append(f"project {name}: protected zone not answered (list its paths in `protected`, or set "
                            f"no_protected_zone: true only if the owner confirmed there are none)")
        projects.append(proj)
    plan["projects"] = projects
    plan["no_protected_zone"] = raw.get("no_protected_zone") is True
    if not projects and not plan["no_protected_zone"]:
        problems.append("no project and no confirmation: set no_protected_zone: true (the owner confirms there "
                        "is nothing to protect yet), or add a project with its protected paths")
    return plan, problems


def answers_record(plan: dict) -> dict:
    """The answers in the --answers schema, for re-running the installer."""
    rec = {k: plan[k] for k in ("company", "company_one_line", "owner", "language", "timezone", "report_times",
                                "messenger", "roles", "owner_instruction", "block_budget", "live_services",
                                "claude_cmd", "service", "git_init")}
    rec["data_sources"] = [{"name": s["NAME"], "source": s["SOURCE"]} for s in plan["data_sources"]]
    rec["projects"] = []
    for p in plan["projects"]:
        e = {k: p[k] for k in ("name", "description", "path", "mode", "handoff")}
        if p["state"] == "paths":
            e["protected"] = p["protected_raw"]
        elif p["state"] == "none":
            e["protected"], e["no_protected_zone"] = [], True
        rec["projects"].append(e)
    if plan["no_protected_zone"]:
        rec["no_protected_zone"] = True
    return rec


# ---------------------------------------------------------------- interactive
class Prompter:
    def __init__(self, lang: str, inp: Callable = input, out=None):
        self.lang, self.inp, self.out = lang, inp, out or sys.stdout

    def _read(self, text: str) -> str:
        try:
            return self.inp(text)
        except EOFError:
            print("\n" + say(self.lang, "closed"), file=self.out)
            raise SystemExit(2)

    def ask(self, key: str, default="", validate: Optional[Callable] = None, **kw) -> str:
        while True:
            suffix = f" [{default}]" if default not in ("", None) else ""
            val = self._read(f"{say(self.lang, key, **kw)}{suffix}: ").strip() or str(default or "")
            err = validate(val) if validate else None
            if not err:
                return val
            print(say(self.lang, "invalid", err=err), file=self.out)

    def yes(self, key: str, default: bool = True, **kw) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            val = self._read(f"{say(self.lang, key, **kw)} [{hint}]: ").strip().lower()
            if not val:
                return default
            if val in ("y", "yes", "예", "네", "ㅇ"):
                return True
            if val in ("n", "no", "아니오", "아니요", "ㄴ"):
                return False

    def lines(self, key: str, **kw) -> list:
        out = []
        while True:
            val = self._read(f"{say(self.lang, key, **kw)}: ").strip()
            if not val:
                return out
            out.append(val)


def ask_project(pr: Prompter, dest: Path) -> dict:
    lang = pr.lang
    name = pr.ask("p_name", validate=lambda v: None if NAME_RE.match(v) else
                  "start with a letter; letters, digits, _ or - only")
    desc = pr.ask("p_desc")
    while True:
        path = pr.ask("p_path", validate=lambda v: "a folder path is needed" if not v else
                      ("that is a template placeholder; type the real folder" if is_placeholder(v) else None))
        full = _abs(dest, path)
        if full.is_dir() or pr.yes("p_path_missing", default=False, path=full):
            break
    mode = MODES[int(pr.ask("p_mode", default="1", validate=lambda v: None if v in ("1", "2", "3") else "1, 2 or 3")) - 1]
    handoff = pr.ask("p_handoff", default="HANDOFF.md")
    while True:
        print(say(lang, "p_protected_intro", name=name, path=full), file=pr.out)
        entries = []
        while True:
            e = pr._read(f"{say(lang, 'p_protected_item')}: ").strip()
            if not e:
                break
            if is_placeholder(e):
                print(say(lang, "p_placeholder"), file=pr.out)
                continue
            disp, _ = protected_rules(e, full)
            if not Path(doctor.glob_base(disp)).exists() and not pr.yes("p_protected_missing", default=False, path=disp):
                continue
            entries.append(e)
        if entries:
            return {"name": name, "description": desc, "path": str(full), "mode": mode, "handoff": handoff,
                    "protected": entries}
        if pr._read(f"{say(lang, 'p_protected_none', name=name)}: ").strip().lower() == "none":
            return {"name": name, "description": desc, "path": str(full), "mode": mode, "handoff": handoff,
                    "protected": [], "no_protected_zone": True}


def collect_interactive(pr: Prompter, dest: Path, prev: dict) -> dict:
    d = prev or {}
    raw = {
        "company": pr.ask("company", default=d.get("company") or dest.name.replace("-", " ").replace("_", " ").title()),
        "company_one_line": pr.ask("company_one_line", default=d.get("company_one_line")
                                   or "a one-person company run with an AI executive team"),
        "owner": pr.ask("owner", default=d.get("owner") or git_user_name() or getpass.getuser()),
        "language": pr.ask("language", default=d.get("language") or ("Korean" if pr.lang == "ko" else "English")),
        "timezone": pr.ask("timezone", default=d.get("timezone") or detect_timezone(), validate=tz_error),
    }
    times = d.get("report_times") or ["08:00", "20:00"]
    raw["report_times"] = pr.ask("report_times", default=", ".join(times), validate=lambda v: parse_times(v)[1])
    raw["messenger"] = pr.ask("messenger", default=d.get("messenger") or "telegram",
                              validate=lambda v: None if v.lower() in ("telegram", "none") else "telegram or none")
    raw["roles"] = pr.ask("roles", default=", ".join(d.get("roles") or ROLES),
                          validate=lambda v: None if all(r in ROLES for r in re.split(r"[,\s]+", v.lower()) if r)
                          else f"choose from {', '.join(ROLES)}")
    raw["owner_instruction"] = pr.ask("owner_instruction", default=d.get("owner_instruction") or DEFAULT_INSTRUCTION)
    projects = []
    if d.get("projects") and pr.yes("keep_projects", n=len(d["projects"]),
                                    names=", ".join(p.get("name", "?") for p in d["projects"])):
        projects = list(d["projects"])
    while pr.yes("add_another" if projects else "add_project", default=not projects):
        projects.append(ask_project(pr, dest))
    raw["projects"] = projects
    if not projects:
        while pr._read(f"{say(pr.lang, 'no_projects_none')}: ").strip().lower() != "none":
            projects.append(ask_project(pr, dest))
            if not pr.yes("add_another", default=False):
                break
        raw["no_protected_zone"] = not projects
    raw["live_services"] = pr.ask("live_services", default=d.get("live_services") or "")
    print(say(pr.lang, "data_sources"), file=pr.out)
    raw["data_sources"] = pr.lines("data_item") or d.get("data_sources") or []
    for k in ("block_budget", "claude_cmd", "service", "git_init"):
        if k in d:
            raw[k] = d[k]
    return raw


# ---------------------------------------------------------------- renderers
def strip_leading_comment(text: str) -> str:
    return re.sub(r"\n<!--.*?-->\n\n?", "\n", text, count=1, flags=re.S)


def render_lines(text: str, values: dict, groups: dict, empty: dict) -> str:
    """Scalar {{NAME}} values; a line with {{UNIT_1_*}} is repeated per item, lines with _2_, _3_ ... are dropped."""
    out = []
    for line in text.splitlines():
        names = PLACEHOLDER.findall(line)
        kinds = {(GROUP.match(n).group(1), GROUP.match(n).group(2)) for n in names if GROUP.match(n)}
        if kinds:
            kind, idx = sorted(kinds)[0]
            if idx != "1":
                continue
            items = groups.get(kind) or []
            if not items and kind in empty:
                out.append(empty[kind])
            for item in items:
                new = line
                for n in names:
                    m = GROUP.match(n)
                    if m and m.group(1) == kind:
                        new = new.replace("{{" + n + "}}", str(item.get(m.group(3), "")))
                out.append(new)
            continue
        out.append(line)
    text = "\n".join(out) + "\n"
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def render_charter(plan: dict, dest: Path) -> str:
    text = strip_leading_comment((TEMPLATES / "CLAUDE.md.ceo").read_text())
    roles = plan["roles"]
    for r in ROLES:
        if r not in roles:
            text = re.sub(rf"^\| {r.upper()} \|.*\n", "", text, flags=re.M)
    files = ("{" + ",".join(roles) + "}" if len(roles) > 1 else (roles[0] if roles else "(none installed)"))
    text = text.replace("{cto,cro,cfo,cmo}", files)
    projects = plan["projects"]
    critical = [{"PATH": c} for p in projects for c in p["protected"]]
    values = {
        "COMPANY": plan["company"], "COMPANY_ONE_LINE": plan["company_one_line"], "OWNER_NAME": plan["owner"],
        "LANGUAGE": plan["language"], "CEO_PROJECT_PATH": str(dest),
        "LIVE_SERVICES": plan["live_services"] or "any live service the company runs",
        "READ_ONLY_PATHS": ", ".join(f"`{p['path']}`" for p in projects) or "(none yet)",
    }
    groups = {"UNIT": [{"NAME": p["name"], "ONE_LINE": p["description"] or p["mode"], "PATH": p["path"]}
                       for p in projects],
              "CRITICAL": critical, "METRIC": plan["data_sources"]}
    empty = {"CRITICAL": f"- (none recorded: at install on {plan['today']} the owner confirmed that no project file "
                         f"moves money or controls a live service)",
             "METRIC": "- (none yet: add one line per metric, `name: path or API`)"}
    return render_lines(text, values, groups, empty)


def q(s) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def render_registry(plan: dict) -> str:
    head = (TEMPLATES / "logic_agents.yaml").read_text().split("\nagents:\n", 1)[0].rstrip("\n")
    head = head.replace("# Copy to: config/logic_agents.yaml", f"# Written by scripts/install.py on {plan['today']}.")
    lines = [head, ""]
    if not plan["projects"]:
        return "\n".join(lines + ["agents: {}", ""])
    lines.append("agents:")
    for p in plan["projects"]:
        lines += [f"  {p['name']}:", f"    path: {q(p['path'])}", f"    role: {q(p['description'] or p['mode'])}",
                  f"    handoff: {q(p['handoff'])}", f"    mode: {p['mode']}"]
        if p["mode"] == "headless":
            lines += ["    allowed_tools:                     # proposed; passed only after you confirm (permissions_status)",
                      f"      - {q('Edit(/' + p['path'] + '/**)')}"]
        if p["state"] == "none":
            lines += [f"    protected_zone: none               # owner confirmed at install {plan['today']}: nothing here "
                      f"moves money or controls a live service", "    denied_tools: []"]
        else:
            note = ("REPLACE: protected zone not answered at install" if p["state"] == "unanswered"
                    else "critical zone: files that move real money or control live services")
            lines.append(f"    denied_tools:                      # {note}")
            lines += [f"      - {q(r)}" for r in p["rules"]]
        lines.append("")
    return "\n".join(lines)


def starter_items(plan: dict, dest: Path, label: str, service_file: Optional[str]) -> list:
    ko = plan["language"].strip().lower() in ("korean", "ko", "한국어", "kor")  # titles follow the report language
    stamp, today = plan["today"].replace("-", ""), plan["today"]
    items = []
    if plan["messenger"] == "telegram":
        items.append({
            "id": f"setup_telegram_{stamp}", "due": today, "owner": "human", "status": "open", "action": "local",
            "ready": True,
            "title": ("텔레그램 보고 봇 만들기: 토큰·chat id 를 설치된 서비스 정의에 입력(절차 준비됨)" if ko else
                      "Create the Telegram bot for reports; put its token and chat id into the installed service "
                      "definition (steps prepared)"),
            "prep": ["Telegram: open @BotFather > /newbot > copy the token",
                     "Send your bot one message, open https://api.telegram.org/bot<token>/getUpdates, copy chat.id",
                     f"Put both into the installed copy of {service_file or 'the service definition'} "
                     f"(REPLACE_WITH_BOT_TOKEN, REPLACE_WITH_CHAT_ID); never into a file in git"]})
    if service_file:
        if plan["service"] == "launchd":
            target = f"~/Library/LaunchAgents/{label}.plist"
            steps = [f"cp {service_file} ~/Library/LaunchAgents/ && chmod 600 {target}",
                     f"Edit {target}: the two REPLACE_WITH_ values (if you use Telegram)",
                     f"plutil -lint {target} && launchctl bootstrap gui/$(id -u) {target}",
                     "python3 scripts/report_daemon.py --status"]
        else:
            steps = [f"mkdir -p ~/.config/systemd/user && cp {service_file} ~/.config/systemd/user/",
                     "Telegram: printf 'REPORT_TELEGRAM_TOKEN=...\\nREPORT_TELEGRAM_CHAT_ID=...\\n' > "
                     "~/.config/report-daemon/env && chmod 600 ~/.config/report-daemon/env",
                     "systemctl --user daemon-reload && systemctl --user enable --now report-daemon",
                     "loginctl enable-linger \"$USER\"", "python3 scripts/report_daemon.py --status"]
        items.append({
            "id": f"register_report_service_{stamp}", "due": today, "owner": "human", "status": "open",
            "action": "local", "ready": True,
            "title": ("보고 데몬 서비스 등록(명령 준비됨, README 3절)" if ko else
                      "Register the report daemon service (commands prepared, README section 3)"),
            "prep": [f"cd {dest}"] + steps})
    if any(p["mode"] == "headless" for p in plan["projects"]):
        items.append({
            "id": f"confirm_allowlist_{stamp}", "due": today, "owner": "human", "status": "open", "action": "local",
            "ready": True,
            "title": ("프로젝트 에이전트 허용 목록 검토: 확정하면 permissions_status: confirmed + 원문·날짜·범위 기록" if ko else
                      "Review the proposed allowlist of the project agents; to confirm, set permissions_status: "
                      "confirmed and record your words, date and scope"),
            "prep": ["config/logic_agents.yaml (allowed_tools per agent; denied_tools stay as they are)",
                     "Until then each agent has only the tools of its own .claude/settings*.json; its first report "
                     "ends with 'Needed permission: ...'"]})
    return items


def render_queue(plan: dict, items: list) -> str:
    import yaml
    t = (TEMPLATES / "owner_action_queue.yaml").read_text()
    head = t.split("\n- id:", 1)[0]
    head = re.sub(r"#\n# EXAMPLES:.*?\n(?=\n|$)", "", head, flags=re.S).rstrip("\n")
    head = head.replace("# Copy to: config/owner_action_queue.yaml", f"# Written by scripts/install.py on {plan['today']}.")
    body = yaml.safe_dump(items, sort_keys=False, allow_unicode=True, width=1000) if items else "[]\n"
    return head + "\n\n" + body


def remove_example_blocks(text: str) -> tuple:
    """Remove top-level queue items that are template examples, keeping every comment. (text, removed ids)."""
    parts = re.split(r"(?m)^(?=- )", text)
    keep, removed = [], []
    for part in parts:
        m = re.match(r"- id:\s*['\"]?([^'\"\n]+)", part)
        if m and (re.search(r"(?m)^\s+example:\s*true\s*$", part) or m.group(1).strip() in doctor.TEMPLATE_EXAMPLE_IDS):
            removed.append(m.group(1).strip())
            continue
        keep.append(part)
    return "".join(keep), removed


def set_yaml_value(text: str, key: str, literal: str) -> str:
    pat = re.compile(rf"^({re.escape(key)}:[ \t]*)(\"[^\"\n]*\"|\[[^\]\n]*\]|[^\s#]+)", re.M)
    new, n = pat.subn(lambda m: m.group(1) + literal, text, count=1)
    if not n:
        raise ValueError(f"key {key} not found in templates/report_daemon.yaml")
    return new


def render_daemon_config(plan: dict) -> str:
    t = (TEMPLATES / "report_daemon.yaml").read_text()
    t = t.replace("# Copy to: config/report_daemon.yaml   and   templates/report_prompt.md -> config/report_prompt.md",
                  f"# Written by scripts/install.py on {plan['today']}. Prompt: config/report_prompt.md")
    t = set_yaml_value(t, "timezone", q(plan["timezone"]))
    t = set_yaml_value(t, "report_times", "[" + ", ".join(q(x) for x in plan["report_times"]) + "]")
    t = set_yaml_value(t, "claude_cmd", q(plan["claude_cmd"]))
    return set_yaml_value(t, "delivery", plan["messenger"])


def render_policy(plan: dict) -> str:
    return render_lines((TEMPLATES / "autonomy_policy.md").read_text(),
                        {"INSTALL_DATE": plan["today"], "OWNER_INSTRUCTION": plan["owner_instruction"],
                         "BLOCK_BUDGET": plan["block_budget"]}, {}, {})


def render_handoff(plan: dict) -> str:
    agents = ", ".join(f"{p['name']} ({p['mode']})" for p in plan["projects"]) or "none yet"
    return render_lines((TEMPLATES / "HANDOFF.md").read_text().replace("Copy to: HANDOFF.md in the CEO project root.\n", ""),
                        {"INSTALL_DATE": plan["today"], "OWNER_INSTRUCTION": plan["owner_instruction"],
                         "AGENT_SUMMARY": agents}, {}, {})


def render_access(plan: dict, dest: Path) -> str:
    head = "\n".join(ln for ln in (TEMPLATES / "access_control.yaml").read_text().splitlines()
                     if ln.startswith("#") and "Copy to:" not in ln)
    lines = [head, f"# Written by scripts/install.py on {plan['today']}.", "", "ceo:", f"  path: {q(dest)}",
             "  access: RW", ""]
    if not plan["projects"]:
        return "\n".join(lines + ["projects: {}", ""])
    lines.append("projects:")
    for p in plan["projects"]:
        lines += [f"  {p['name']}:", f"    path: {q(p['path'])}", "    ceo_access: R"]
    return "\n".join(lines) + "\n"


def service_path_env(plan: dict) -> str:
    dirs = []
    for exe in (plan["claude_cmd"], shutil.which("node"), sys.executable):
        if exe and os.path.isabs(os.path.expanduser(str(exe))):
            dirs.append(str(Path(os.path.expanduser(str(exe))).parent))
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] if plan["service"] == "launchd" else \
        [str(Path.home() / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


def render_service(plan: dict, dest: Path, label: str) -> tuple:
    """(file name, text) for the platform's service manager, or (None, None)."""
    from xml.sax.saxutils import escape
    if plan["service"] == "launchd":
        t = (TEMPLATES / "service" / "com.example.report-daemon.plist").read_text()
        note = (f"<!--\n  launchd user agent for scripts/report_daemon.py, written by scripts/install.py on "
                f"{plan['today']}.\n  Register it: README section 3, or the owner action 'register_report_service' "
                f"in config/owner_action_queue.yaml.\n  Put the Telegram values into the INSTALLED copy "
                f"(~/Library/LaunchAgents/{label}.plist), never into git.\n  After editing the installed copy: "
                f"launchctl bootout gui/$(id -u)/{label}, then launchctl bootstrap gui/$(id -u) "
                f"~/Library/LaunchAgents/{label}.plist (kickstart keeps the old definition).\n-->")
        t = re.sub(r"<!--\n  launchd user agent.*?-->", lambda m: note, t, count=1, flags=re.S)
        t = t.replace("<string>com.example.report-daemon</string>", f"<string>{escape(label)}</string>")
        t = t.replace("<string>/usr/bin/python3</string>", f"<string>{escape(sys.executable)}</string>")
        t = t.replace("/ABSOLUTE/PATH/TO/ceo_project", escape(str(dest)))
        t = re.sub(r"(<key>PATH</key>\s*<string>)[^<]*(</string>)",
                   lambda m: m.group(1) + escape(service_path_env(plan)) + m.group(2), t)
        if plan["messenger"] == "none":
            t = re.sub(r"\s*<key>REPORT_TELEGRAM_TOKEN</key>\s*<string>[^<]*</string>"
                       r"\s*<key>REPORT_TELEGRAM_CHAT_ID</key>\s*<string>[^<]*</string>", "", t)
        return f"{label}.plist", t
    if plan["service"] == "systemd":
        t = (TEMPLATES / "service" / "report-daemon.service").read_text()
        d = str(dest)
        sq = lambda x: f'"{x}"' if " " in x else x  # noqa: E731  (systemd accepts double-quoted arguments)
        t = t.replace("cp templates/service/report-daemon.service", "cp service/report-daemon.service")
        t = t.replace("ExecStart=/usr/bin/python3 %h/ceo_project/scripts/report_daemon.py --config "
                      "%h/ceo_project/config/report_daemon.yaml",
                      f"ExecStart={sq(sys.executable)} {sq(d + '/scripts/report_daemon.py')} "
                      f"--config {sq(d + '/config/report_daemon.yaml')}")
        t = t.replace("WorkingDirectory=%h/ceo_project", f"WorkingDirectory={d}")
        t = t.replace("%h/ceo_project", d)
        t = re.sub(r"^Environment=PATH=.*$", lambda m: f"Environment=PATH={service_path_env(plan)}", t, flags=re.M)
        if plan["messenger"] == "none":
            t = t.replace("EnvironmentFile=%h/", "EnvironmentFile=-%h/")
        return "report-daemon.service", t
    return None, None


# ---------------------------------------------------------------- writing
class Writer:
    def __init__(self, dest: Path, force: bool, confirm: Callable):
        self.dest, self.force, self.confirm = dest, force, confirm
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.created, self.same, self.kept, self.replaced, self.notes = [], [], [], [], []

    def write(self, rel: str, text: str, record: bool = False) -> bool:
        """record=True: the owner's own records (never replaced once they exist)."""
        p = self.dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            old = p.read_text(errors="replace")
            if old == text:
                self.same.append(rel)
                return True
            if record or not (self.force or self.confirm(rel)):
                self.kept.append(rel)
                return False
            backup = p.with_name(f"{p.name}.bak-{self.stamp}")
            shutil.copy2(p, backup)
            self.replaced.append(f"{rel} (backup {backup.name})")
        else:
            self.created.append(rel)
        p.write_text(text)
        return True

    def copy(self, src: Path, rel: str) -> None:
        dst = self.dest / rel
        if dst.exists() and dst.resolve() == src.resolve():
            self.same.append(rel)
            return
        if self.write(rel, src.read_text()):
            os.chmod(dst, src.stat().st_mode & 0o777)


def merge_gitignore(dest: Path) -> list:
    p = dest / ".gitignore"
    have = p.read_text().splitlines() if p.is_file() else []
    add = [x for x in GITIGNORE if x not in have]
    if add:
        p.write_text("\n".join(have + add).lstrip("\n") + "\n")
    return add


def merge_ceo_settings(dest: Path, rules: list) -> tuple:
    """Add the protected-zone deny rules to the CEO project's .claude/settings.json. (added count, error)."""
    if not rules:
        return 0, None
    p = dest / ".claude" / "settings.json"
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        return 0, f".claude/settings.json is not valid JSON ({e}); add these deny rules by hand: {rules}"
    deny = data.setdefault("permissions", {}).setdefault("deny", [])
    new = [r for r in rules if r not in deny]
    if new:
        deny.extend(new)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return len(new), None


def git_init(dest: Path) -> Optional[str]:
    if not shutil.which("git"):
        return None
    r = subprocess.run(["git", "-C", str(dest), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if r.returncode == 0:
        return None
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=False)
    return "git init"


def install(plan: dict, problems: list, dest: Path, force: bool = False,
            confirm: Optional[Callable] = None, out=None) -> int:
    import yaml
    out = out or sys.stdout
    confirm = confirm or (lambda rel: False)
    w = Writer(dest, force, confirm)
    for d in DIRS:
        (dest / d).mkdir(parents=True, exist_ok=True)
    (dest / "advisory_drafts" / "reviews" / ".gitkeep").touch()
    label = f"com.{slug(plan['company'])}.report-daemon"
    service_name, service_text = render_service(plan, dest, label)
    service_rel = f"service/{service_name}" if service_name else None

    w.write("CLAUDE.md", render_charter(plan, dest))
    for r in plan["roles"]:
        w.copy(TEMPLATES / "agents" / f"{r}.md", f".claude/agents/{r}.md")
    w.write("config/logic_agents.yaml", render_registry(plan))
    w.write("config/report_daemon.yaml", render_daemon_config(plan))
    w.write("config/autonomy_policy.md", render_policy(plan))
    w.write("config/access_control.yaml", render_access(plan, dest))
    rec = answers_record(plan)
    w.write("config/install_answers.yaml",
            "# Answers of the last scripts/install.py run (no secrets). Re-run: python3 scripts/install.py "
            "--answers config/install_answers.yaml\n" + yaml.safe_dump(rec, sort_keys=False, allow_unicode=True))
    for src, rel in COPIES:
        if (TEMPLATE_ROOT / src).is_file():
            w.copy(TEMPLATE_ROOT / src, rel)
    if service_rel:
        w.write(service_rel, service_text)
    queue = dest / "config" / "owner_action_queue.yaml"
    if queue.is_file():
        text, removed = remove_example_blocks(queue.read_text())
        if removed:
            backup = queue.with_name(f"{queue.name}.bak-{w.stamp}")
            shutil.copy2(queue, backup)
            queue.write_text(text)
            w.notes.append(f"removed {len(removed)} template example item(s) from the queue (backup {backup.name})")
        w.kept.append("config/owner_action_queue.yaml")
    else:
        w.write("config/owner_action_queue.yaml", render_queue(plan, starter_items(plan, dest, label, service_rel)))
    w.write("HANDOFF.md", render_handoff(plan), record=True)
    added = merge_gitignore(dest)
    if added:
        w.notes.append(f".gitignore: added {' '.join(added)}")
    real_rules = [r for p in plan["projects"] for r in p["rules"] if not is_placeholder(r)]
    n, err = merge_ceo_settings(dest, real_rules)
    if err:
        problems.append(err)
    elif n:
        w.notes.append(f".claude/settings.json: {n} deny rule(s) for the CEO session")
    if plan["git_init"] and git_init(dest):
        w.notes.append("git init (nothing committed)")

    print("", file=out)
    print("== doctor ==", file=out)
    rep = doctor.run(dest, out=out)
    complete = not problems and not rep.failures
    print_summary(plan, problems, w, rep, complete, dest, service_rel, label, out)
    return 0 if complete else 1


def _box(lines: list) -> list:
    width = max(len(x) for x in lines) + 8
    return ["!" * width] + [f"!!  {x.ljust(width - 8)}  !!" for x in lines] + ["!" * width]


def print_summary(plan, problems, w, rep, complete, dest, service_rel, label, out) -> None:
    p = lambda s="": print(s, file=out)  # noqa: E731
    p()
    p("== install summary ==")
    p(f"folder:   {dest}")
    p(f"created:  {len(w.created)} file(s); unchanged {len(w.same)}; kept as they were {len(w.kept)}"
      + (f"; replaced {len(w.replaced)}" if w.replaced else ""))
    for rel in w.kept:
        if rel not in ("HANDOFF.md", "config/owner_action_queue.yaml"):
            p(f"  kept existing {rel} (different from the new one; re-run with --force to replace it)")
    for r in w.replaced:
        p(f"  replaced {r}")
    for note in w.notes:
        p(f"  {note}")
    projects = plan["projects"]
    p("projects: " + (", ".join(f"{x['name']} ({x['mode']}, {len(x['protected'])} protected)" for x in projects)
                      or "none yet"))
    for x in projects:
        for disp in x["protected"]:
            p(f"  protected  {x['name']}: {disp}")
    unprotected = [x["name"] for x in projects if x["state"] == "none"]
    if unprotected or (not projects and plan["no_protected_zone"]):
        who = ", ".join(unprotected) if unprotected else "no project registered yet"
        p()
        for line in _box([f"NO PROTECTED ZONE: {who}",
                          "You confirmed that nothing there moves money or controls a live service.",
                          "Agents are NOT denied any file there.",
                          "To add one later: denied_tools in config/logic_agents.yaml",
                          "  (\"Edit(//absolute/path/**)\"), then python3 scripts/doctor.py"]):
            p(line)
    p(f"reports:  {', '.join(plan['report_times'])} {plan['timezone']} via {plan['messenger']}")
    p(f"doctor:   {rep.count('ok')} ok, {rep.count('warn')} warning(s), {rep.count('fail')} failure(s)")
    p()
    if complete:
        p("STATUS: COMPLETE")
    else:
        p("STATUS: NOT COMPLETE - fix these, then run the installer again (or python3 scripts/doctor.py):")
        for x in problems:
            p(f"  - {x}")
        for x in rep.failures:
            p(f"  - {x}")
    p()
    cd = "" if Path.cwd().resolve() == dest.resolve() else f"cd {shlex.quote(str(dest))} && "
    p("next:")
    p(f"  1. see a report now (prints only):  {cd}python3 scripts/report_daemon.py --once --dry-run")
    if service_rel:
        p(f"  2. register the report service:     README section 3 (file ready: {service_rel}, label {label})")
    headless = [x["name"] for x in projects if x["mode"] == "headless"]
    if headless:
        p(f"  3. first project agent check:       {cd}python3 scripts/logic_agent.py {headless[0]} "
          f"-p \"Read your handoff and report status in 5 lines\" --dry-run")
    p("  your to-do list from now on:        config/owner_action_queue.yaml (it is the top of every report)")


# ---------------------------------------------------------------- main
def _has_yaml() -> bool:
    import importlib
    importlib.invalidate_caches()
    try:
        importlib.import_module("yaml")
        return True
    except ImportError:
        return False


def ensure_yaml(interactive: bool, lang: str, pr: Optional[Prompter]) -> bool:
    """PyYAML present, or installed now with the owner's yes (interactive only)."""
    if _has_yaml():
        return True
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    cmd = [sys.executable, "-m", "pip", "install"] + ([] if in_venv else ["--user"]) + ["pyyaml"]
    print(say(lang, "yaml_missing", cmd=" ".join(shlex.quote(c) for c in cmd)))
    if not interactive or not pr.yes("yaml_install_now", default=True):
        return False
    if subprocess.call(cmd) != 0:
        print(say(lang, "yaml_failed"))
        return False
    import site
    site.addsitedir(site.getusersitepackages())  # a --user install made after start-up is not on sys.path yet
    if _has_yaml():
        return True
    print(say(lang, "yaml_failed"))
    return False


def main(argv: Optional[list] = None, inp: Callable = input) -> int:
    ap = argparse.ArgumentParser(description="Install C-Level Agent OS into a folder (see the module docstring)")
    ap.add_argument("--dest", default=".", help="CEO project folder (default: the current folder)")
    ap.add_argument("--answers", help="YAML answers file: install without questions")
    ap.add_argument("--force", action="store_true", help="replace existing generated files (a backup is kept)")
    ap.add_argument("--doctor", action="store_true", help="only run the installation check")
    ap.add_argument("--lang", choices=("en", "ko"), help="language of the questions (default: from your locale)")
    a = ap.parse_args(argv)
    dest = Path(os.path.expanduser(a.dest)).resolve()
    lang = a.lang or detect_lang()
    if a.doctor:
        return 1 if doctor.run(dest).failures else 0
    interactive = not a.answers
    pr = Prompter(lang, inp=inp) if interactive else None
    if not ensure_yaml(interactive, lang, pr):
        return 1
    import yaml
    dest.mkdir(parents=True, exist_ok=True)
    if a.answers:
        try:
            raw = yaml.safe_load(Path(a.answers).expanduser().read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            print(f"cannot read the answers file {a.answers}: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw, dict):
            print(f"the answers file must be a YAML mapping: {a.answers}", file=sys.stderr)
            return 2
        confirm = None
    else:
        print(say(lang, "intro", dest=dest))
        prev_path = dest / "config" / "install_answers.yaml"
        prev = {}
        if prev_path.is_file() and pr.yes("reuse", default=True):
            prev = yaml.safe_load(prev_path.read_text()) or {}
        raw = collect_interactive(pr, dest, prev)
        confirm = lambda rel: pr.yes("overwrite", default=False, rel=rel)  # noqa: E731
    plan, problems = normalize(raw, dest, date.today())
    return install(plan, problems, dest, force=a.force, confirm=confirm)


if __name__ == "__main__":
    sys.exit(main())
