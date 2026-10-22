#!/usr/bin/env python3
"""First plan: the CEO agent reads the company files and writes the first two-week plan (one model run).

  python3 scripts/first_plan.py                  # about 5-15 minutes; uses your Claude subscription
  python3 scripts/first_plan.py --dry-run        # print the command and the prompt; no model call
  python3 scripts/first_plan.py --max-turns 60   # more room when it stopped at the turn limit (0 = no limit)

What it does, in order:
  1. fills templates/first_plan_prompt.md with the company, the report language, the installed roles, each
     project's handoff file and today's date
  2. runs `claude -p` in this folder. It may read files (Read, Glob, Grep) and write ONLY the plan file under
     advisory_drafts/ and config/owner_action_queue.yaml (--allowedTools). The deny rules of
     .claude/settings.json (protected zone, live services) are passed again as --disallowedTools; a deny always
     wins over an allow. No --permission-mode: anything else (shell commands, other files) is denied
     automatically in a run without a terminal
  3. checks what it left: the plan file advisory_drafts/<date>-first-plan.md (when the model could not write it,
     its reply is saved there instead); the queue still parses and keeps every earlier item as it was (otherwise
     the earlier version is restored and only the new items are added back); new items other than fact
     questions stay ready: false (nothing is prepared yet); the protected files are unchanged
  4. prints the plan path, the number of queue items added and the next command
The raw reply is kept under state/first_plan/. Exit code: 0 plan saved, 1 no plan, 2 not an installation.
Dependencies: Python 3.9+, PyYAML.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import doctor  # noqa: E402  (stdlib only at import time)

PLAN_DIR = "advisory_drafts"
QUEUE = "config/owner_action_queue.yaml"
PROMPT = "templates/first_plan_prompt.md"
DEFAULT_MAX_TURNS = 40
DEFAULT_TIMEOUT = 1800  # seconds; the prompt asks for 10 minutes, the notice says 5-15
PROGRESS_SECONDS = 60
SNAPSHOT_CAP = 5000  # files per protected path compared before and after
REQUIRED = ("id", "due", "owner", "title", "status")

MSG = {
    "intro": ("First plan: the CEO agent reads the company files and writes a two-week plan.",
              "첫 계획: CEO 에이전트가 회사 파일을 읽고 2주 계획을 씁니다."),
    "notice": ("It takes about 5-15 minutes and uses your own Claude subscription. Almost no messages while it "
               "runs is normal.",
               "약 5~15분 걸리고 본인의 Claude 구독 사용량을 씁니다. 진행 중 메시지가 거의 없어도 정상입니다."),
    "scope": ("It may write only {plan} and {queue}; everything else is read-only ({n} protected-zone rule(s) "
              "denied).",
              "쓸 수 있는 파일은 {plan} 과 {queue} 뿐이고 나머지는 읽기만 합니다(보호 구역 거부 규칙 {n}개)."),
    "progress": ("  ... {m} min, still working", "  ... {m}분 경과, 진행 중"),
    "no_install": ("No installation in {root} (config/report_daemon.yaml or CLAUDE.md is missing): run "
                   "python3 scripts/install.py first.",
                   "{root} 에 설치가 없습니다(config/report_daemon.yaml 또는 CLAUDE.md 없음). 먼저 "
                   "python3 scripts/install.py 를 실행합니다."),
    "no_prompt": ("Prompt template not found: {path} (copy the template's templates/ folder here).",
                  "프롬프트 템플릿이 없습니다: {path} (템플릿의 templates/ 폴더를 여기에 복사합니다)."),
    "no_claude": ("claude CLI not found. Install Claude Code and sign in (run claude, type /login).",
                  "claude CLI 를 찾지 못했습니다. Claude Code 를 설치하고 로그인합니다(claude 실행 후 /login)."),
    "retry_no_turns": ("This claude does not know --max-turns; running again without it.",
                       "이 claude 는 --max-turns 를 모릅니다. 그 옵션 없이 다시 실행합니다."),
    "saved_model": ("saved:    {path} (written by the CEO agent)", "저장:     {path} (CEO 에이전트가 직접 씀)"),
    "saved_reply": ("saved:    {path} (the agent could not write it; its reply was saved there)",
                    "저장:     {path} (에이전트가 직접 쓰지 못해 답변을 그 파일에 저장함)"),
    "saved_other": ("saved:    {path} (the agent named it {name}; renamed so the reports find it)",
                    "저장:     {path} (에이전트가 {name} 으로 써서 보고가 찾도록 이름을 바꿈)"),
    "no_plan": ("NO PLAN: the run ended without a plan file. {why}", "계획 없음: 계획 파일 없이 끝났습니다. {why}"),
    "queue_added": ("queue:    {n} item(s) added ({prep} being prepared: ready: false; {facts} fact question(s))",
                    "큐 추가:  {n}건 (준비 중 ready:false {prep}건, 사실 확인 질문 {facts}건)"),
    "queue_none": ("queue:    no item added", "큐 추가:  없음"),
    "queue_restored": ("queue:    the agent's edit did not parse as a list; the earlier queue was restored",
                       "큐:       에이전트의 수정이 YAML 목록으로 읽히지 않아 이전 큐로 되돌림"),
    "queue_rebuilt": ("queue:    the agent's edit was redone as a plain append to the earlier queue ({why}); its "
                      "version: {bak}",
                      "큐:       에이전트의 수정을 이전 큐 끝에 덧붙이는 방식으로 다시 했다({why}). 에이전트가 쓴 판: {bak}"),
    "queue_warn": ("  check:  {id}: {problem}", "  확인:   {id}: {problem}"),
    "queue_unchecked": ("queue:    could not be read before the run, so it was not checked (python3 scripts/doctor.py)",
                        "큐:       실행 전부터 읽을 수 없어 점검하지 않음(python3 scripts/doctor.py)"),
    "protected_ok": ("protected zone: unchanged ({n} file(s) compared)", "보호 구역: 변경 없음 (파일 {n}개 비교)"),
    "protected_none": ("protected zone: none registered", "보호 구역: 등록된 것 없음"),
    "protected_changed": ("PROTECTED ZONE CHANGED during the run: {files}. Check them (git diff, backups) now.",
                          "실행 중 보호 구역이 바뀌었습니다: {files}. 지금 확인합니다(git diff, 백업)."),
    "took": ("took:     {t}{turns}", "소요:     {t}{turns}"),
    "turns": ("; {n} turns", ", 턴 {n}"),
    "next": ("next:     python3 scripts/report_daemon.py --once --dry-run   (today's first owner step, each role's "
             "next action and the open questions)",
             "다음:     python3 scripts/report_daemon.py --once --dry-run   (오늘 할 일·역할별 다음 행동·질문이 "
             "보고에 나옵니다)"),
    "why_turns": ("It stopped at the turn limit ({n}): run python3 scripts/first_plan.py --max-turns {m}.",
                  "턴 한도({n})에서 멈췄습니다: python3 scripts/first_plan.py --max-turns {m} 로 다시 실행합니다."),
    "why_login": ("claude is not signed in: run claude, type /login, then run this again.",
                  "claude 에 로그인되어 있지 않습니다: claude 실행 후 /login, 그다음 다시 실행합니다."),
    "why_timeout": ("Stopped after {s} s (--timeout).", "{s}초 뒤 멈췄습니다(--timeout)."),
    "why_error": ("Error: {e}", "오류: {e}"),
    "raw": ("raw reply: {path}", "원본 답변: {path}"),
}


def say(ko: bool, key: str, **kw) -> str:
    en, k = MSG[key]
    return (k if ko else en).format(**kw)


def is_korean(language: str) -> bool:
    return str(language or "").strip().lower() in ("korean", "ko", "한국어", "kor")


def _yaml(path: Path):
    import yaml
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


# ---------------------------------------------------------------- context from the installed files
def context(root: Path, today: date) -> dict:
    """Everything the prompt and the command need, read from the installed files (never guessed)."""
    answers = _yaml(root / "config" / "install_answers.yaml")
    answers = answers if isinstance(answers, dict) else {}
    cfg = _yaml(root / "config" / "report_daemon.yaml")
    cfg = cfg if isinstance(cfg, dict) else {}
    reg = _yaml(root / "config" / "logic_agents.yaml")
    agents = (reg.get("agents") or {}) if isinstance(reg, dict) else {}
    company = str(answers.get("company") or "")
    if not company:
        try:
            m = re.match(r"#\s*(.+?)\s+CEO Agent\s*$", (root / "CLAUDE.md").read_text().splitlines()[0])
            company = m.group(1) if m else root.name
        except (OSError, IndexError):
            company = root.name
    language = str(answers.get("language") or ("Korean" if os.environ.get("LANG", "").startswith("ko") else "English"))
    handoffs = []
    for name, a in (agents.items() if isinstance(agents, dict) else []):
        a = a if isinstance(a, dict) else {}
        path = doctor.resolve_agent_path(root, a.get("path"))
        handoffs.append((str(name), path / str(a.get("handoff") or "HANDOFF.md")))
    try:
        settings = json.loads((root / ".claude" / "settings.json").read_text())
        deny = [str(r) for r in ((settings.get("permissions") or {}).get("deny") or [])]
    except (OSError, ValueError, AttributeError):
        deny = []
    return {"root": root, "company": company, "owner": str(answers.get("owner") or "the owner"),
            "language": language, "ko": is_korean(language), "today": today,
            "roles": roles(root),
            "handoffs": handoffs, "deny": deny,
            "claude_cmd": str(cfg.get("claude_cmd") or "claude"), "model": str(cfg.get("model") or "")}


def roles(root: Path) -> list:
    """Installed role agents, in the charter's order (cto, cro, cfo, cmo), then any added role."""
    found = {p.stem for p in (root / ".claude" / "agents").glob("*.md")}
    order = [r for r in ("cto", "cro", "cfo", "cmo") if r in found]
    return order + sorted(found - set(order))


def plan_rel(root: Path, today: date) -> str:
    """advisory_drafts/<date>-first-plan.md, or -2, -3 ... when a plan of that day exists."""
    rel, n = f"{PLAN_DIR}/{today.isoformat()}-first-plan.md", 2
    while (root / rel).exists():
        rel, n = f"{PLAN_DIR}/{today.isoformat()}-first-plan-{n}.md", n + 1
    return rel


def build_prompt(template: str, ctx: dict, plan: str) -> str:
    handoffs = "; ".join(f"{n}: {p}" + ("" if p.is_file() else " (missing)") for n, p in ctx["handoffs"])
    values = {"company": ctx["company"], "owner": ctx["owner"], "language": ctx["language"],
              "today": ctx["today"].isoformat(), "stamp": ctx["today"].strftime("%Y%m%d"),
              "due_by": (ctx["today"] + timedelta(days=14)).isoformat(), "plan_path": plan, "queue_path": QUEUE,
              "roles": ", ".join(ctx["roles"]) or "(none installed)",
              "handoffs": handoffs or "(no project registered yet)"}
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


def allowed_tools(root: Path) -> list:
    """Read anywhere; write only the plan folder and the queue of this company folder."""
    base = "/" + str(root).lstrip("/")
    out = ["Read", "Glob", "Grep"]
    for target in (f"{base}/{PLAN_DIR}/**", f"{base}/{QUEUE}"):
        out += [f"Edit(/{target})", f"Write(/{target})"]
    return out


def command(claude: str, ctx: dict, max_turns: int) -> list:
    """The prompt goes on stdin (the tool lists are variadic). Never --permission-mode / --continue / --resume."""
    cmd = [claude, "-p", "--output-format", "json", "--allowedTools", *allowed_tools(ctx["root"])]
    if ctx["deny"]:
        cmd += ["--disallowedTools", *ctx["deny"]]
    if max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]
    if ctx["model"]:
        cmd += ["--model", ctx["model"]]
    return cmd


# ---------------------------------------------------------------- running claude
def child_env() -> dict:
    """Drop CLAUDECODE / CLAUDE_CODE_* (nested-session guard) and the report daemon's messenger secrets."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")
            and not k.startswith("REPORT_TELEGRAM_")}


def run_cmd(cmd: list, stdin_text: str, cwd: str, timeout: int, progress: Optional[Callable] = None,
            every: int = PROGRESS_SECONDS) -> tuple:
    """(rc, stdout, stderr, seconds). Own process group, killed on timeout (rc 124) or Ctrl+C."""
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=cwd, env=child_env(), start_new_session=True)
    except OSError as e:
        return 127, "", f"cannot start {cmd[0]}: {e}", 0.0

    def kill():
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        p.stdin.write(stdin_text)
        p.stdin.close()
    except BrokenPipeError:
        pass
    try:
        import threading
        out, err = [], []
        readers = [threading.Thread(target=lambda s=s, b=b: b.append(s.read()), daemon=True)
                   for s, b in ((p.stdout, out), (p.stderr, err))]
        for r in readers:
            r.start()
        next_note = every
        while p.poll() is None:
            elapsed = time.monotonic() - t0
            if elapsed > timeout:
                kill()
                p.wait()
                for r in readers:
                    r.join(5)
                return 124, "".join(out), f"timeout {timeout}s\n" + "".join(err), elapsed
            if progress and elapsed >= next_note:
                progress(int(elapsed // 60))
                next_note += every
            time.sleep(0.5)
        for r in readers:
            r.join(30)
        return p.returncode, "".join(out), "".join(err), time.monotonic() - t0
    except BaseException:
        kill()
        raise


def interpret(rc: int, out: str, err: str) -> dict:
    try:
        obj = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        obj = {"result": out.strip()}
    obj = obj if isinstance(obj, dict) else {"result": out.strip()}
    text = str(obj.get("result") or "").strip()
    subtype = str(obj.get("subtype") or "")
    is_error = rc != 0 or bool(obj.get("is_error")) or subtype.startswith("error")
    first = next((ln.strip() for ln in (text or err or "").splitlines() if ln.strip()), "")
    return {"ok": not is_error, "text": text, "subtype": subtype, "rc": rc, "turns": obj.get("num_turns"),
            "error": "" if not is_error else (first or f"exit code {rc}"), "stderr": err, "raw": out}


# ---------------------------------------------------------------- checking what the run left
def protected_files(rules: list) -> dict:
    """{path: (size, mtime_ns)} under every absolute Edit/Read deny rule (capped per rule)."""
    out = {}
    for r in rules:
        tool, abs_path, _ = doctor.rule_path(r)
        if tool not in ("Edit", "Read") or not abs_path:
            continue
        base = Path(doctor.glob_base(abs_path))
        files = [base] if base.is_file() else (p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []
        for n, f in enumerate(files):
            if n >= SNAPSHOT_CAP:
                break
            try:
                st = f.stat()
                out[str(f)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
    return out


def item_problems(item: dict, today: date) -> list:
    probs = [f"{k} is missing" for k in REQUIRED if not str(item.get(k) or "").strip()]
    try:
        date.fromisoformat(str(item.get("due")))
    except ValueError:
        if item.get("due"):
            probs.append(f"due {item.get('due')!r} is not YYYY-MM-DD")
    if item.get("owner") == "human" and item.get("action") not in ("external", "local", "facts"):
        probs.append("action must be external, local or facts")
    return probs


def check_queue(queue: Path, before_text: str, stamp: str, today: date) -> dict:
    """What the run did to the queue. Earlier items are never lost or changed: when the agent changed or removed
    one (or left a new item that needs a fix), the earlier text is restored and only the new items are added."""
    import yaml
    import install  # noqa: E402  (the installer's append keeps comments and checks the result)
    res = {"added": [], "restored": False, "rebuilt": "", "backup": "", "problems": [], "unchecked": False}
    try:
        before = yaml.safe_load(before_text) or []
    except yaml.YAMLError:
        before = None
    if not isinstance(before, list):  # broken before the run (doctor says so): nothing to compare against
        res["unchecked"] = True
        return res
    after_text = queue.read_text() if queue.is_file() else ""
    try:
        after = yaml.safe_load(after_text)
    except yaml.YAMLError:
        after = None
    if not isinstance(after, list):
        queue.write_text(before_text)
        res["restored"] = True
        return res
    old = {str(i.get("id")): i for i in before if isinstance(i, dict)}
    kept = {str(i.get("id")): i for i in after if isinstance(i, dict) and str(i.get("id")) in old}
    changed = [iid for iid, item in old.items() if kept.get(iid) != item]
    new, why = [], []
    for n, i in enumerate(x for x in after if not (isinstance(x, dict) and str(x.get("id")) in old)):
        if not isinstance(i, dict):
            why.append("an entry that is not an item")
            continue
        i = dict(i)
        if not str(i.get("id") or "").strip() or str(i.get("id")) == "None":
            i["id"] = f"first_plan_item_{n + 1}_{stamp}"
            why.append("an item without id")
        if not i.get("status"):
            i["status"] = "open"
            why.append(f"{i['id']} without status")
        if i.get("ready") is True and i.get("action") != "facts":
            i["ready"] = False  # nothing is prepared yet: only a fact question is ready to answer
            why.append(f"{i['id']} ready: true before anything was prepared")
        new.append(i)
    if changed:
        why.insert(0, f"earlier item(s) {', '.join(changed)} changed or removed")
    elif new and not after_text.startswith(re.sub(r"(?m)^\[\][ \t]*\n?\Z", "", before_text).rstrip("\n")):
        why.insert(0, "the file was rewritten instead of appended to")  # so the earlier comments stay
    if why:
        bak = queue.with_name(f"{queue.name}.bak-first-plan-{datetime.now():%Y%m%d-%H%M%S}")
        bak.write_text(queue.read_text())
        text, added, failed = install.queue_add_items(before_text, new)
        queue.write_text(text)
        res.update(rebuilt="; ".join(why), backup=bak.name)
        res["added"] = [i for i in (yaml.safe_load(text) or []) if isinstance(i, dict) and i.get("id") in added]
        res["problems"] += [(iid, "not added (the queue could not take it)") for iid in failed]
    else:
        res["added"] = new
    for i in res["added"]:
        res["problems"] += [(i["id"], p) for p in item_problems(i, today)]
    return res


def fmt_time(seconds: float, ko: bool) -> str:
    m, s = divmod(int(seconds), 60)
    return (f"{m}분 {s}초" if ko else f"{m} min {s} s") if m else (f"{s}초" if ko else f"{s} s")


# ---------------------------------------------------------------- main
def run(root: Path, max_turns: int = DEFAULT_MAX_TURNS, timeout: int = DEFAULT_TIMEOUT, dry_run: bool = False,
        runner: Callable = run_cmd, today: Optional[date] = None, out=None) -> int:
    out = out or sys.stdout
    today = today or date.today()
    if not ((root / "config" / "report_daemon.yaml").is_file() and (root / "CLAUDE.md").is_file()):
        print(say(False, "no_install", root=root), file=sys.stderr)
        return 2
    ctx = context(root, today)
    ko = ctx["ko"]
    template = next((p for p in (root / PROMPT, HERE.parent / PROMPT) if p.is_file()), None)
    if template is None:
        print(say(ko, "no_prompt", path=root / PROMPT), file=sys.stderr)
        return 2
    plan = plan_rel(root, today)
    prompt = build_prompt(template.read_text(), ctx, plan)
    resolved, elsewhere = doctor.find_claude(ctx["claude_cmd"])
    claude = resolved or elsewhere
    cmd = command(claude or ctx["claude_cmd"], ctx, max_turns)
    if dry_run:
        print(" ".join(shlex.quote(c) for c in cmd) + "  < prompt", file=out)
        print("", file=out)
        print(prompt, file=out)
        return 0
    if not claude:
        print(say(ko, "no_claude"), file=sys.stderr)
        return 1
    queue = root / QUEUE
    before_text = queue.read_text() if queue.is_file() else "[]\n"
    if not queue.is_file():
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(before_text)
    (root / PLAN_DIR).mkdir(parents=True, exist_ok=True)
    drafts_before = {p: p.stat().st_mtime_ns for p in (root / PLAN_DIR).glob("*.md")}
    prot_before = protected_files(ctx["deny"])
    for key in ("intro", "notice"):
        print(say(ko, key), file=out)
    print(say(ko, "scope", plan=plan, queue=QUEUE, n=len(ctx["deny"])), file=out, flush=True)

    def progress(minutes):
        print(say(ko, "progress", m=minutes), file=out, flush=True)

    t0 = time.monotonic()
    rc, sout, serr, _ = runner(cmd, prompt, str(root), timeout, progress)
    if rc != 0 and "max-turns" in serr and re.search(r"unknown option|unrecognized", serr, re.I):
        print(say(ko, "retry_no_turns"), file=out, flush=True)
        cmd = command(claude, ctx, 0)
        rc, sout, serr, _ = runner(cmd, prompt, str(root), timeout, progress)
    took = time.monotonic() - t0
    res = interpret(rc, sout, serr)
    raw_dir = root / "state" / "first_plan"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    raw.write_text(json.dumps({"cmd": cmd, "rc": rc, "stdout": sout, "stderr": serr[-4000:]}, ensure_ascii=False,
                              indent=1))

    # the plan file
    print("", file=out)
    plan_path, how, other = root / plan, None, ""
    if plan_path.is_file():
        how = "saved_model"
    else:
        fresh = [p for p in (root / PLAN_DIR).glob("*.md") if drafts_before.get(p) != p.stat().st_mtime_ns]
        fresh = [p for p in fresh if "plan" in p.name.lower()] or fresh
        if len(fresh) == 1:  # the agent picked another name: move it where the reports look
            other, how = fresh[0].name, "saved_other"
            os.replace(fresh[0], plan_path)
        elif res["ok"] and len(res["text"]) > 200:  # a real answer, not the one-line confirmation
            plan_path.write_text(res["text"].rstrip() + "\n")
            how = "saved_reply"
    if how:
        print(say(ko, how, path=plan_path.relative_to(root), name=other), file=out)
    else:
        if res["subtype"] == "error_max_turns":
            why = say(ko, "why_turns", n=max_turns, m=max(max_turns * 2, 60))
        elif rc == 124:
            why = say(ko, "why_timeout", s=timeout)
        elif "logged in" in (res["error"] + serr).lower() or "/login" in res["error"]:
            why = say(ko, "why_login")
        else:
            why = say(ko, "why_error", e=res["error"] or "-")
        print(say(ko, "no_plan", why=why), file=out)

    # the queue
    q = check_queue(queue, before_text, today.strftime("%Y%m%d"), today)
    if q["unchecked"]:
        print(say(ko, "queue_unchecked"), file=out)
    if q["restored"]:
        print(say(ko, "queue_restored"), file=out)
    if q["rebuilt"]:
        print(say(ko, "queue_rebuilt", why=q["rebuilt"], bak=q["backup"]), file=out)
    added = q["added"]
    if added:
        facts = sum(1 for i in added if i.get("action") == "facts" and i.get("ready") is True)
        prep = sum(1 for i in added if i.get("ready") is not True)
        print(say(ko, "queue_added", n=len(added), prep=prep, facts=facts), file=out)
        for i in added:
            print(f"  - {i.get('title') or i.get('id')} ({i.get('due')}, {i.get('action')}, "
                  f"ready: {str(i.get('ready') is True).lower()})", file=out)
    elif not (q["restored"] or q["unchecked"]):
        print(say(ko, "queue_none"), file=out)
    for iid, problem in q["problems"]:
        print(say(ko, "queue_warn", id=iid, problem=problem), file=out)

    # the protected zone
    prot_after = protected_files(ctx["deny"])
    changed = sorted(k for k in set(prot_before) | set(prot_after) if prot_before.get(k) != prot_after.get(k))
    if changed:
        print(say(ko, "protected_changed", files=", ".join(changed[:5]) + (" ..." if len(changed) > 5 else "")),
              file=out)
    elif any(doctor.rule_path(r)[1] for r in ctx["deny"]):
        print(say(ko, "protected_ok", n=len(prot_before)), file=out)
    else:
        print(say(ko, "protected_none"), file=out)

    turns = say(ko, "turns", n=res["turns"]) if res["turns"] else ""
    print(say(ko, "took", t=fmt_time(took, ko), turns=turns), file=out)
    print(say(ko, "raw", path=raw.relative_to(root)), file=out)
    if how:
        print(say(ko, "next"), file=out)
    return 0 if how and not changed else 1


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="The CEO agent writes the first two-week plan and the owner's first "
                                             "queue items (one model run, about 5-15 minutes)")
    ap.add_argument("--root", default=str(HERE.parent), help="company (CEO) folder (default: the parent of scripts/)")
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                    help=f"turn limit for the run (default {DEFAULT_MAX_TURNS}; 0 = no limit)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"seconds (default {DEFAULT_TIMEOUT})")
    ap.add_argument("--dry-run", action="store_true", help="print the command and the prompt; no model call")
    a = ap.parse_args(argv)
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("PyYAML is missing: python3 -m pip install --user pyyaml", file=sys.stderr)
        return 2
    try:
        return run(Path(os.path.expanduser(a.root)).resolve(), max_turns=a.max_turns, timeout=a.timeout,
                   dry_run=a.dry_run)
    except KeyboardInterrupt:
        print("\nStopped (Ctrl+C).", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
