#!/usr/bin/env python3
"""One-step installer for C-Level Agent OS.

  python3 scripts/install.py                          # plain questions; installs into this folder
  python3 scripts/install.py --dest ~/my-company      # installs into another folder
  python3 scripts/install.py --answers answers.yaml   # no questions (for agents and tests); see ANSWERS below
  python3 scripts/install.py --reconfigure delivery   # change one part in place: delivery (telegram | none)
  python3 scripts/install.py --reconfigure delivery --to telegram    # the same without a question
  python3 scripts/install.py --reconfigure schedule   # time zone and report times
  python3 scripts/install.py --render-service         # (re)write service/ from config/ and print the commands
  python3 scripts/install.py --doctor [--dest DIR]    # only run the check (same as scripts/doctor.py)

What it does, in order:
  1. creates the folders (config/, scripts/, tests/, .claude/agents/, state/report_daemon/,
     advisory_drafts/reviews/, service/)
  2. writes CLAUDE.md (the CEO charter) and the role agents from templates/, filling every placeholder
     from your answers: company, owner, language, projects (name, folder, how you work with it, handoff
     file), each project's protected zone, data sources, time zone, report times, messenger
  3. protected zone: every path you name becomes an absolute deny rule (Edit(//abs/path/**)) for that
     project's agent and for the CEO session (.claude/settings.json). Leaving it empty needs an explicit
     "none"; the summary then shows NO PROTECTED ZONE in a box. A path that does not exist is refused
     (interactive: asked, default no; answers file: NOT COMPLETE unless the project has allow_missing)
  4. writes the owner action queue without the template's examples (plus your real setup steps),
     the files the charter refers to (config/autonomy_policy.md, config/access_control.yaml, HANDOFF.md,
     advisory_drafts/reviews/), config/report_daemon.yaml and a service file for launchd or systemd
  5. copies the runner, the daemon, this installer, the check, their tests and templates/ (so --reconfigure
     and --render-service work in the company folder), merges .gitignore, runs `git init`
  6. runs scripts/doctor.py, then prints the summary READ BACK FROM THE WRITTEN FILES (never from the
     answers). The install is COMPLETE only when doctor reports no failure and the files say what you
     answered; a placeholder left anywhere means NOT COMPLETE (exit code 1)

A folder that already has an installation: interactive asks first (1 keep it and only check it, 2 answer
again and overwrite with .bak copies, 3 stop). With --answers, an existing file that differs is kept unless
--force, and a kept file that disagrees with the answers (delivery, report times, projects, protected zones,
service file) makes the install NOT COMPLETE. HANDOFF.md and the queue are your records: never replaced
(only template examples are removed from the queue and missing setup steps appended).
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
      allow_missing: ["config/pricing.yaml"]   # optional: protected paths that may not exist yet (or true)
    - name: notes
      path: "~/projects/notes"
      protected: none                  # ONLY when the owner said nothing there moves money or runs live
                                       # (same as protected: [] plus no_protected_zone: true)
  no_protected_zone: true              # needed only when there are no projects
  claude_cmd: /absolute/path/to/claude # default: detected
  service: auto                        # auto | launchd | systemd | none
  git_init: true
A project without a protected-zone decision (missing, empty or null `protected`, and no explicit none)
keeps the template's placeholder rule, so the install is reported NOT COMPLETE until you answer it.

Dependencies: Python 3.9+ and PyYAML (interactive mode offers to install PyYAML for you).
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = TEMPLATE_ROOT / "templates"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402  (stdlib only at import time)

ROLES = ("cto", "cro", "cfo", "cmo")
MODES = ("headless", "live", "in_session")
DELIVERIES = ("telegram", "none")
RECONFIGURE = ("delivery", "schedule")
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
GROUP = re.compile(r"^(UNIT|CRITICAL|METRIC)_(\d+)_([A-Z_]+)$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
DEFAULT_INSTRUCTION = "Agents prepare everything up to the last click; I take only that click."
DIRS = ("config", "scripts", "tests", ".claude/agents", "state/report_daemon", "advisory_drafts/reviews", "service")
COPIES = (("scripts/logic_agent.py", "scripts/logic_agent.py"), ("scripts/report_daemon.py", "scripts/report_daemon.py"),
          ("scripts/doctor.py", "scripts/doctor.py"), ("scripts/install.py", "scripts/install.py"),
          ("tests/test_logic_agent.py", "tests/test_logic_agent.py"),
          ("tests/test_report_daemon.py", "tests/test_report_daemon.py"), ("requirements.txt", "requirements.txt"),
          ("requirements-dev.txt", "requirements-dev.txt"), ("templates/report_prompt.md", "config/report_prompt.md"))
GITIGNORE = ("state/", "__pycache__/", "*.pyc", ".pytest_cache/", ".DS_Store", "service/", "*.bak-*")
# files whose presence means "this folder already has an installation"
GENERATED = ("CLAUDE.md", "config/logic_agents.yaml", "config/report_daemon.yaml", "config/install_answers.yaml")
RECORDS = ("HANDOFF.md", "config/owner_action_queue.yaml")
# the questions of a fresh interactive install, in order, and those asked once more per project folder
BASE_QUESTIONS = ("company", "company_one_line", "owner", "language", "timezone", "report_times", "messenger",
                  "roles", "owner_instruction", "add_project", "live_services", "data_item")
PROJECT_QUESTIONS = ("p_path", "p_name", "p_desc", "p_mode", "p_handoff", "p_protected_item", "add_another")
SECONDS_PER_QUESTION = 15  # the estimate shown on the first screen
REGISTERED = {"missing": "not yet", "same": "yes, up to date", "differs": "yes, but an older copy"}
YES = ("y", "yes", "예", "네", "응", "ㅇ")
NO = ("n", "no", "아니오", "아니요", "아니", "ㄴ")
NONE_WORDS = ("none", "없음")

# ---------------------------------------------------------------- messages (en, ko)
MSG = {
    "intro": ("C-Level Agent OS installer. Folder: {dest}\n"
              "{n} questions ({p} more for each project folder you add), about {m} minutes.\n"
              "Press Enter to accept the value in [brackets]; a question without one says what Enter does.\n"
              "Stop at any time with Ctrl+C: no installation file is written before the last question.",
              "C-Level Agent OS 설치. 설치 폴더: {dest}\n"
              "질문 {n}개(프로젝트 폴더를 추가하면 하나당 {p}개 더), 약 {m}분.\n"
              "[대괄호] 안의 값이 기본값이며 Enter 를 누르면 그대로 쓴다. 대괄호가 없는 질문은 Enter 가 무엇을 하는지 적혀 있다.\n"
              "언제든 Ctrl+C 로 멈출 수 있다. 마지막 질문 전에는 설치 파일을 하나도 쓰지 않는다."),
    "stopped": ("\nStopped (Ctrl+C). No installation file was written.",
                "\n멈췄다(Ctrl+C). 설치 파일은 하나도 쓰지 않았다."),
    "existing": ("This folder already has an installation ({files}).\n"
                 "  1 = keep it: change nothing; check it and show what the files say\n"
                 "  2 = answer the questions again and overwrite (your last answers are the defaults; each replaced\n"
                 "      file keeps a .bak copy; HANDOFF.md and the queue are never replaced)\n"
                 "  3 = stop\n"
                 "  (only how reports reach you: python3 scripts/install.py --reconfigure delivery)\n  Choose",
                 "이 폴더에는 이미 설치돼 있다({files}).\n"
                 "  1 = 유지: 아무것도 바꾸지 않고 점검한 뒤 실제 파일 내용을 보여준다\n"
                 "  2 = 질문에 다시 답하고 새 답으로 덮어쓴다(지난 답이 기본값. 바뀌는 파일마다 .bak 사본을 남긴다.\n"
                 "      HANDOFF.md 와 큐는 바꾸지 않는다)\n"
                 "  3 = 중단\n"
                 "  (보고 받는 곳만 바꾸려면: python3 scripts/install.py --reconfigure delivery)\n  선택"),
    "aborted": ("Stopped. Nothing was changed.", "중단했다. 아무것도 바꾸지 않았다."),
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
    "p_path_hint": ("  Tip: drag the folder from Finder into this window to type its path.",
                    "  팁: Finder 에서 폴더를 이 터미널 창으로 끌어다 놓으면 경로가 입력된다."),
    "p_path": ("  Its folder (absolute path, or starting with ~; Enter = do not add a project)",
               "  폴더 경로(절대경로 또는 ~ 로 시작. Enter = 프로젝트를 추가하지 않고 돌아감)"),
    "p_skipped": ("  No project added.", "  프로젝트를 추가하지 않았다."),
    "p_name": ("  Short name (letters, digits, _ or -)", "  짧은 이름(영문·숫자·_·-)"),
    "p_desc": ("  One line on what it does (optional; Enter = leave empty)", "  하는 일 한 줄(선택. Enter = 비워 둠)"),
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
    "p_protected_none": ("  No protected path for {name}. Type a path to add it; type none if nothing there moves\n"
                         "  money or controls a live service; Enter = do not add this project",
                         "  {name} 의 보호 경로가 없다. 경로를 입력하면 추가된다. 돈이 움직이거나 라이브 서비스를 제어하는\n"
                         "  파일이 정말 없으면 none. Enter = 이 프로젝트를 추가하지 않음"),
    "no_projects_note": ("No project added, so there is no protected zone yet (the summary shows it in a box).\n"
                         "To add a project later, run the installer again and choose 2.",
                         "프로젝트 없이 진행한다. 아직 보호 구역이 없다(요약에 상자로 표시된다).\n"
                         "나중에 추가하려면 설치기를 다시 실행해 2 를 고른다."),
    "live_services": ("Live services agents must never start, stop or restart (optional, e.g. com.myshop.worker; "
                      "Enter = none)",
                      "에이전트가 시작·중지·재시작하면 안 되는 라이브 서비스 이름(선택, 예: com.myshop.worker. Enter = 없음)"),
    "data_sources": ("Key numbers the CEO should read, one per line as name = path or API\n"
                     "(optional, e.g. revenue = ~/shop/revenue.csv; empty line = done)",
                     "CEO 가 읽을 핵심 수치, 한 줄에 하나씩 이름 = 경로 또는 API\n"
                     "(선택, 예: revenue = ~/shop/revenue.csv. 빈 줄 = 끝)"),
    "data_item": ("  data source", "  데이터 소스"),
    "yaml_missing": ("PyYAML (the only dependency) is missing. The command that installs it:\n  {cmd}",
                     "필수 의존성 PyYAML 이 없다. 설치 명령:\n  {cmd}"),
    "yaml_install_now": ("Run it now?", "지금 실행할까?"),
    "yaml_failed": ("Installing PyYAML failed. Use a virtual environment instead:\n"
                    "  python3 -m venv .venv && .venv/bin/python -m pip install pyyaml\n"
                    "  .venv/bin/python scripts/install.py",
                    "PyYAML 설치에 실패했다. 가상환경으로 진행한다:\n"
                    "  python3 -m venv .venv && .venv/bin/python -m pip install pyyaml\n"
                    "  .venv/bin/python scripts/install.py"),
    "invalid": ("  {err} (Ctrl+C to stop)", "  {err} (멈추려면 Ctrl+C)"),
    "err_name": ("start with a letter; letters, digits, _ or - only", "영문자로 시작하고 영문·숫자·_·- 만 쓴다"),
    "err_name_taken": ("{name} is already used by another project", "{name} 은(는) 다른 프로젝트가 이미 쓴다"),
    "err_placeholder": ("that is a template placeholder, not a real path; type the real path",
                        "그건 템플릿 자리표시자다. 실제 경로를 입력한다"),
    "err_tz": ("'{v}' is not an IANA time zone name (e.g. Asia/Seoul, Europe/Berlin, UTC)",
               "'{v}' 는 IANA 타임존 이름이 아니다(예: Asia/Seoul, Europe/Berlin, UTC)"),
    "err_times": ("'{v}': write 24h times such as 08:00, 20:00", "'{v}': 08:00, 20:00 처럼 24시간제 시각으로 쓴다"),
    "err_messenger": ("type telegram or none", "telegram 또는 none 을 입력한다"),
    "err_roles": ("choose from cto, cro, cfo, cmo", "cto, cro, cfo, cmo 중에서 고른다"),
    "err_choice": ("type one of {opts}", "{opts} 중 하나를 입력한다"),
    "err_yes": ("type y or n (Enter = {d})", "y 또는 n 을 입력한다(Enter = {d})"),
    "closed": ("No answer (input closed). For an install without questions use --answers FILE.",
               "입력이 닫혀 답을 받을 수 없다. 질문 없이 설치하려면 --answers FILE 을 쓴다."),
    "no_install": ("No installation in {dest} (config/report_daemon.yaml is missing): run python3 scripts/install.py first.",
                   "{dest} 에 설치가 없다(config/report_daemon.yaml 없음). 먼저 python3 scripts/install.py 를 실행한다."),
    "need_to": ("Without questions, say the new value: --to telegram or --to none.",
                "질문 없이 바꾸려면 새 값을 준다: --to telegram 또는 --to none."),
    "no_templates": ("templates/service/ not found next to this script ({path}): copy the template's templates/ folder "
                     "here (README section 4), then run this again.",
                     "이 스크립트 옆에 templates/service/ 가 없다({path}). 템플릿의 templates/ 폴더를 여기에 복사한 뒤"
                     "(README 4절) 다시 실행한다."),
    "no_service_manager": ("No service manager here (launchd or systemd): run the daemon yourself "
                           "(python3 scripts/report_daemon.py) under whatever keeps processes running.",
                           "서비스 관리자(launchd·systemd)가 없다. 프로세스를 유지하는 다른 수단으로 "
                           "python3 scripts/report_daemon.py 를 직접 실행한다."),
    "box_none": ("NO PROTECTED ZONE: {who}", "NO PROTECTED ZONE: {who} (보호 구역 없음)"),
    "box_no_project": ("no project registered yet", "등록된 프로젝트 없음"),
    "box_l2": ("You confirmed that nothing there moves money or controls a live service.",
               "그곳에는 돈이 움직이거나 라이브 서비스를 제어하는 파일이 없다고 확인했다."),
    "box_l2_noproj": ("No project agent exists, so no project file is protected yet.",
                      "프로젝트 에이전트가 없어 보호되는 프로젝트 파일도 아직 없다."),
    "box_l4_noproj": ("To add a project with its protected zone: run the installer again and choose 2.",
                      "보호 구역과 함께 프로젝트를 추가하려면: 설치기를 다시 실행해 2 를 고른다."),
    "box_l3": ("Agents are NOT denied any file there.", "에이전트는 그곳의 어떤 파일도 거부당하지 않는다."),
    "box_l4": ("To add one: run the installer again and choose 2, or add", "추가하려면: 설치기를 다시 실행해 2 를 고르거나,"),
    "box_l5": ("  denied_tools (\"Edit(//absolute/path/**)\") in config/logic_agents.yaml",
               "  config/logic_agents.yaml 의 denied_tools 에 \"Edit(//절대/경로/**)\" 를 넣는다"),
    "saved_files": ("saved as files under {d}/reports/, not sent", "{d}/reports/ 에 파일로만 저장, 발송 없음"),
    "signin_no": ("NOT signed in to Claude Code: the model part of each report works only after you sign in "
                  "(run claude, type /login)",
                  "Claude Code 에 로그인되어 있지 않다. 모델 보고(요약·CEO 의견)는 로그인 후 동작한다(claude 실행 후 /login)"),
    "signin_ok": ("ok", "로그인됨"),
    "signin_unknown": ("not checked (python3 scripts/doctor.py --check-login makes one short call)",
                       "확인 못 함(python3 scripts/doctor.py --check-login 은 짧은 호출 1회)"),
    "nothing_changed": ("nothing changed (kept as it is)", "바꾼 것 없음(그대로 유지)"),
    "status_ok": ("STATUS: COMPLETE", "STATUS: COMPLETE (설치 완료)"),
    "status_not": ("STATUS: NOT COMPLETE - fix these, then run the installer again (or python3 scripts/doctor.py):",
                   "STATUS: NOT COMPLETE (미완료) - 아래를 고친 뒤 설치기를 다시 실행한다(또는 python3 scripts/doctor.py):"),
    "fix_template_copies": ("{n} kept file(s) are old template copies with placeholders left ({files}): run "
                            "python3 scripts/install.py --force to replace them (a .bak copy of each is kept)",
                            "남아 있던 {n}개 파일이 자리표시자가 남은 옛 템플릿 사본이다({files}). "
                            "python3 scripts/install.py --force 로 바꾼다(파일마다 .bak 사본을 남긴다)"),
    "fix_template_copies_keep": ("files with placeholders are old template copies: run the installer again and "
                                 "choose 2 (each replaced file keeps a .bak copy)",
                                 "자리표시자가 남은 파일은 옛 템플릿 사본이다. 설치기를 다시 실행해 2 를 고른다"
                                 "(바뀌는 파일마다 .bak 사본)"),
    "fix_kept_differs": ("these files were kept because they already existed and differ, so they do not say what you "
                         "answered: re-run with --force to replace them (a .bak copy is kept), or change only the "
                         "delivery with python3 scripts/install.py --reconfigure delivery",
                         "아래 파일은 이미 있었고 내용이 달라 그대로 두었기 때문에 이번 답과 다르다. --force 로 다시 "
                         "실행해 바꾸거나(.bak 사본을 남긴다), 보고 받는 곳만 바꾸려면 "
                         "python3 scripts/install.py --reconfigure delivery"),
    "next": ("next:", "다음:"),
    "next_report": ("1. see a report now (prints only):", "1. 지금 보고 보기(출력만):"),
    "next_service": ("2. register the report service. The exact lines for this computer:",
                     "2. 보고 서비스 등록. 이 컴퓨터용 명령:"),
    "next_reregister": ("2. re-register the report service (the registered copy is older). The exact lines:",
                        "2. 보고 서비스 다시 등록(등록된 사본이 예전 것이다). 이 컴퓨터용 명령:"),
    "next_registered": ("2. report service: registered and up to date", "2. 보고 서비스: 등록됨, 최신"),
    "next_agent": ("3. first project agent check:", "3. 첫 프로젝트 에이전트 점검:"),
    "next_queue": ("your to-do list from now on: config/owner_action_queue.yaml (it is the top of every report)",
                   "이제부터 할 일 목록: config/owner_action_queue.yaml (모든 보고의 맨 위)"),
    "next_queue_open": ("open it: {cmd}", "여는 법: {cmd}"),
    "svc_block_all": ("paste these lines into Terminal as they are:", "아래 줄을 그대로 터미널에 붙여넣는다:"),
    "svc_block_copy_tg": ("paste these lines. TextEdit opens the installed copy: replace REPLACE_WITH_BOT_TOKEN and "
                          "REPLACE_WITH_CHAT_ID (README, words used here), save, close it:",
                          "아래 줄을 붙여넣으면 텍스트 편집기가 열린다. REPLACE_WITH_BOT_TOKEN 과 REPLACE_WITH_CHAT_ID 를 "
                          "바꾸고(README 용어 상자) 저장한 뒤 닫는다:"),
    "svc_block_after_tg": ("after saving, paste these lines:", "저장한 뒤 아래 줄을 붙여넣는다:"),
    "svc_block_env_tg": ("Telegram: in this line replace PASTE_TOKEN and PASTE_CHAT_ID first, then press Enter:",
                         "텔레그램: 이 줄의 PASTE_TOKEN 과 PASTE_CHAT_ID 를 먼저 바꾼 뒤 Enter:"),
    "register_again": ("To register it again anyway (for example after 'Bootstrap failed'):",
                       "그래도 다시 등록하려면('Bootstrap failed' 가 났을 때 등):"),
    "restart_after": ("The registered service reads the new setting after a restart. Paste:",
                      "등록된 서비스는 재시작해야 새 설정을 읽는다. 붙여넣는다:"),
    "rendered": ("wrote {rel}", "{rel} 를 썼다"),
    "rendered_same": ("{rel} is already up to date", "{rel} 는 이미 최신이다"),
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


def default_name(folder: str, taken=()) -> str:
    """A valid, unused project name from a folder name (shop bot -> shop-bot; 2024 -> project-2024)."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", folder).strip("-_")[:40]
    if not NAME_RE.match(base):
        base = f"project-{base}"[:40] if base else "project"
    name, n = base, 2
    while name in taken:
        name, n = f"{base[:36]}-{n}", n + 1
    return name


def clean_path_input(raw: str) -> str:
    """A typed or Finder-dragged path: drag-and-drop adds backslash escapes and a trailing space."""
    s = str(raw).strip()
    try:
        parts = shlex.split(s)
    except ValueError:
        parts = []
    return parts[0] if len(parts) == 1 else s.strip("'\"")


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


def has_placeholder_text(path: Path) -> bool:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return False
    code = [ln for ln in lines if not (path.suffix in (".yaml", ".yml") and ln.lstrip().startswith("#"))]
    return any(PLACEHOLDER.search(ln) or doctor.PATH_MARK in ln for ln in code)


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


def display_exists(disp: str) -> bool:
    m = doctor._RULE.match(disp)
    if m:
        _, abs_path, _ = doctor.rule_path(disp)
        disp = abs_path or ""
    return bool(disp) and Path(doctor.glob_base(disp)).exists()


# ---------------------------------------------------------------- answers
def _abs(dest: Path, raw) -> Path:
    p = Path(os.path.expanduser(str(raw or "")))
    return (p if p.is_absolute() else dest / p).resolve()


def _explicit_none(entries) -> bool:
    if isinstance(entries, str):
        return entries.strip().lower() in NONE_WORDS
    return isinstance(entries, list) and len(entries) == 1 and str(entries[0]).strip().lower() in NONE_WORDS


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
    if plan["messenger"] not in DELIVERIES:
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
        allow = p.get("allow_missing")
        allow_all = allow is True
        allow_set = {str(x) for x in allow} if isinstance(allow, list) else set()
        proj = {"name": name, "description": str(p.get("description") or "").strip(), "path": str(path),
                "mode": mode, "handoff": str(p.get("handoff") or "HANDOFF.md"), "protected_raw": [],
                "protected": [], "rules": [], "allow_missing": allow if (allow_all or allow_set) else None}
        entries = p.get("protected")
        if _explicit_none(entries) or (p.get("no_protected_zone") is True and not entries):
            proj["state"] = "none"
        elif entries:
            if isinstance(entries, str):
                entries = [x for x in re.split(r"[\n,]+", entries) if x.strip()]
            proj["state"] = "paths"
            for e in entries:
                disp, rules = protected_rules(e, path)
                proj["protected_raw"].append(str(e))
                proj["protected"].append(disp)
                proj["rules"] += rules
                if is_placeholder(e):
                    problems.append(f"project {name}: protected path {e!r} is a placeholder, not a real path")
                elif not display_exists(disp) and not (allow_all or str(e) in allow_set):
                    problems.append(f"project {name}: protected path does not exist: {disp} (a typo leaves the real "
                                    f"files unprotected). Fix the path, or list it under allow_missing for this "
                                    f"project if it will be created later")
        else:  # missing, empty or null: not answered, and never read as "none"
            proj["state"] = "unanswered"
            ph = f"/ABSOLUTE/PATH/TO/{name}/live/**"
            proj["protected"], proj["rules"] = [ph], [f"Edit(/{ph})"]
            problems.append(f"project {name}: protected zone not answered (list its paths in `protected`, or set "
                            f"protected: none only if the owner confirmed there are none)")
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
            if p.get("allow_missing"):
                e["allow_missing"] = p["allow_missing"]
        elif p["state"] == "none":
            e["protected"] = "none"
        rec["projects"].append(e)
    if plan["no_protected_zone"]:
        rec["no_protected_zone"] = True
    return rec


# ---------------------------------------------------------------- interactive
class Prompter:
    def __init__(self, lang: str, inp: Callable = input, out=None):
        self.lang, self.inp, self.out = lang, inp, out or sys.stdout

    def t(self, key: str, **kw) -> str:
        return say(self.lang, key, **kw)

    def _read(self, text: str) -> str:
        try:
            return self.inp(text)
        except EOFError:
            print("\n" + self.t("closed"), file=self.out)
            raise SystemExit(2)

    def ask(self, key: str, default="", validate: Optional[Callable] = None, **kw) -> str:
        while True:
            suffix = f" [{default}]" if default not in ("", None) else ""
            val = self._read(f"{self.t(key, **kw)}{suffix}: ").strip() or str(default or "")
            err = validate(val) if validate else None
            if not err:
                return val
            print(self.t("invalid", err=err), file=self.out)

    def yes(self, key: str, default: bool = True, **kw) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            val = self._read(f"{self.t(key, **kw)} [{hint}]: ").strip().lower()
            if not val:
                return default
            if val in YES:
                return True
            if val in NO:
                return False
            print(self.t("invalid", err=self.t("err_yes", d="y" if default else "n")), file=self.out)

    def lines(self, key: str, **kw) -> list:
        out = []
        while True:
            val = self._read(f"{self.t(key, **kw)}: ").strip()
            if not val:
                return out
            out.append(val)


def ask_project(pr: Prompter, dest: Path, taken=()) -> Optional[dict]:
    """One project, or None when the owner leaves it (Enter on the folder or on the protected-zone question)."""
    print(pr.t("p_path_hint"), file=pr.out)
    while True:
        raw = pr.ask("p_path")
        if not raw:
            print(pr.t("p_skipped"), file=pr.out)
            return None
        path = clean_path_input(raw)
        if is_placeholder(path):
            print(pr.t("invalid", err=pr.t("err_placeholder")), file=pr.out)
            continue
        full = _abs(dest, path)
        if full.is_dir() or pr.yes("p_path_missing", default=False, path=full):
            break
    name = pr.ask("p_name", default=default_name(full.name, taken),
                  validate=lambda v: pr.t("err_name") if not NAME_RE.match(v) else
                  (pr.t("err_name_taken", name=v) if v in taken else None))
    desc = pr.ask("p_desc")
    mode = MODES[int(pr.ask("p_mode", default="1", validate=lambda v: None if v in ("1", "2", "3")
                            else pr.t("err_choice", opts="1, 2, 3"))) - 1]
    handoff = pr.ask("p_handoff", default="HANDOFF.md")
    base = {"name": name, "description": desc, "path": str(full), "mode": mode, "handoff": handoff}
    print(pr.t("p_protected_intro", name=name, path=full), file=pr.out)
    entries, allowed = [], []

    def add(e: str) -> None:
        e = clean_path_input(e)
        if is_placeholder(e):
            print(pr.t("invalid", err=pr.t("err_placeholder")), file=pr.out)
            return
        disp, _ = protected_rules(e, full)
        if display_exists(disp):
            entries.append(e)
        elif pr.yes("p_protected_missing", default=False, path=disp):
            entries.append(e)
            allowed.append(e)

    while True:
        e = pr._read(f"{pr.t('p_protected_item')}: ").strip()
        if e:
            add(e)
            continue
        if entries:
            break
        ans = pr._read(f"{pr.t('p_protected_none', name=name)}: ").strip()
        if not ans:
            print(pr.t("p_skipped"), file=pr.out)
            return None
        if ans.lower() in NONE_WORDS:
            return {**base, "protected": "none"}
        add(ans)  # a path typed here counts; then more paths, or an empty line to finish
    res = {**base, "protected": entries}
    if allowed:
        res["allow_missing"] = allowed
    return res


def collect_interactive(pr: Prompter, dest: Path, prev: dict) -> dict:
    d = prev or {}
    raw = {
        "company": pr.ask("company", default=d.get("company") or dest.name.replace("-", " ").replace("_", " ").title()),
        "company_one_line": pr.ask("company_one_line", default=d.get("company_one_line")
                                   or "a one-person company run with an AI executive team"),
        "owner": pr.ask("owner", default=d.get("owner") or git_user_name() or getpass.getuser()),
        "language": pr.ask("language", default=d.get("language") or ("Korean" if pr.lang == "ko" else "English")),
        "timezone": pr.ask("timezone", default=d.get("timezone") or detect_timezone(),
                           validate=lambda v: pr.t("err_tz", v=v) if tz_error(v) else None),
    }
    times = d.get("report_times") or ["08:00", "20:00"]
    raw["report_times"] = pr.ask("report_times", default=", ".join(str(x) for x in times),
                                 validate=lambda v: pr.t("err_times", v=v) if parse_times(v)[1] else None)
    raw["messenger"] = pr.ask("messenger", default=d.get("messenger") or "telegram",
                              validate=lambda v: None if v.lower() in DELIVERIES else pr.t("err_messenger"))
    raw["roles"] = pr.ask("roles", default=", ".join(d.get("roles") or ROLES),
                          validate=lambda v: None if all(r in ROLES for r in re.split(r"[,\s]+", v.lower()) if r)
                          else pr.t("err_roles"))
    raw["owner_instruction"] = pr.ask("owner_instruction", default=d.get("owner_instruction") or DEFAULT_INSTRUCTION)
    projects = []
    if d.get("projects") and pr.yes("keep_projects", n=len(d["projects"]),
                                    names=", ".join(str(p.get("name", "?")) for p in d["projects"])):
        projects = list(d["projects"])
    while pr.yes("add_another" if projects else "add_project", default=False):
        p = ask_project(pr, dest, taken={str(x.get("name")) for x in projects})
        if p:
            projects.append(p)
    raw["projects"] = projects
    if not projects:
        print(pr.t("no_projects_note"), file=pr.out)
        raw["no_protected_zone"] = True  # declining every project is the decision; the summary boxes it
    raw["live_services"] = pr.ask("live_services", default=d.get("live_services") or "")
    print(pr.t("data_sources"), file=pr.out)
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


def service_blocks(dest: Path, svc_rel: str, kind: str, label: str, messenger: str, registered: bool,
                   with_cd: Optional[bool] = None) -> list:
    """[(note key, [lines])]: the exact lines to paste for this computer (no <label>, no inline # comments:
    an interactive zsh does not treat # as a comment, so a pasted comment becomes arguments)."""
    qq = lambda s: shlex.quote(str(s))  # noqa: E731
    if with_cd is None:
        with_cd = Path.cwd().resolve() != dest.resolve()
    cd = [f"cd {qq(dest)}"] if with_cd else []
    tg = messenger == "telegram"
    status = "python3 scripts/report_daemon.py --status"
    if kind == "launchd":
        target = f"~/Library/LaunchAgents/{label}.plist"
        first = cd + ([f"launchctl bootout gui/$(id -u)/{label}"] if registered else [])
        first.append(f"cp {qq(svc_rel)} ~/Library/LaunchAgents/ && chmod 600 {target}")
        rest = [f"plutil -lint {target}", f"launchctl bootstrap gui/$(id -u) {target}", status]
        if tg:
            return [("svc_block_copy_tg", first + [f"open -e {target}"]), ("svc_block_after_tg", rest)]
        return [("svc_block_all", first + rest)]
    unit = "~/.config/systemd/user"
    first = cd + [f"mkdir -p {unit} ~/.config/report-daemon && cp {qq(svc_rel)} {unit}/"]
    start = (["systemctl --user daemon-reload && systemctl --user restart report-daemon"] if registered else
             ["systemctl --user daemon-reload && systemctl --user enable --now report-daemon",
              'loginctl enable-linger "$USER"'])
    if tg:
        env = ("printf 'REPORT_TELEGRAM_TOKEN=PASTE_TOKEN\\nREPORT_TELEGRAM_CHAT_ID=PASTE_CHAT_ID\\n' > "
               "~/.config/report-daemon/env && chmod 600 ~/.config/report-daemon/env")
        return [("svc_block_all", first), ("svc_block_env_tg", [env]), ("svc_block_all", start + [status])]
    return [("svc_block_all", first + start + [status])]


def restart_lines(kind: str, label: str) -> list:
    if kind == "launchd":
        return [f"launchctl kickstart -k gui/$(id -u)/{label}", "python3 scripts/report_daemon.py --status"]
    return ["systemctl --user restart report-daemon", "python3 scripts/report_daemon.py --status"]


def _ko(plan_or_language) -> bool:
    lang = plan_or_language["language"] if isinstance(plan_or_language, dict) else str(plan_or_language)
    return lang.strip().lower() in ("korean", "ko", "한국어", "kor")  # queue titles follow the report language


def telegram_item(plan: dict, stamp: str, service_file: Optional[str]) -> dict:
    ko = _ko(plan)
    return {
        "id": f"setup_telegram_{stamp}", "due": plan["today"], "owner": "human", "status": "open", "action": "local",
        "ready": True,
        "title": ("텔레그램 보고 봇 만들기: 토큰·chat id 를 설치된 서비스 정의에 입력(절차 준비됨)" if ko else
                  "Create the Telegram bot for reports; put its token and chat id into the installed service "
                  "definition (steps prepared)"),
        "prep": ["Telegram: open @BotFather > /newbot > copy the token",
                 "Send your bot one message, then open https://api.telegram.org/bot<token>/getUpdates in a browser "
                 "(replace <token> including the angle brackets) and copy the number after \"chat\":{\"id\":",
                 "Empty result {\"ok\":true,\"result\":[]}: send the bot another message and reload the page",
                 f"Put both into the installed copy of {service_file or 'the service definition'} "
                 f"(REPLACE_WITH_BOT_TOKEN, REPLACE_WITH_CHAT_ID); never into a file in git"]}


def register_item(plan: dict, dest: Path, stamp: str, svc_rel: str, kind: str, label: str, registered: bool,
                  reason: str = "") -> dict:
    ko = _ko(plan)
    prep = []
    for note, lines in service_blocks(dest, svc_rel, kind, label, plan["messenger"], registered, with_cd=True):
        prep.append(say("ko" if ko else "en", note))
        prep += lines
    if registered:
        title = (f"보고 데몬 서비스 다시 등록({reason}, 명령 준비됨)" if ko else
                 f"Re-register the report daemon service ({reason}; commands prepared)")
        iid = f"reregister_report_service_{stamp}"
    else:
        title = ("보고 데몬 서비스 등록(명령 준비됨, README 3절)" if ko else
                 "Register the report daemon service (commands prepared, README section 3)")
        iid = f"register_report_service_{stamp}"
    return {"id": iid, "due": plan["today"], "owner": "human", "status": "open", "action": "local", "ready": True,
            "title": title, "prep": prep}


def allowlist_item(plan: dict, stamp: str) -> dict:
    ko = _ko(plan)
    return {
        "id": f"confirm_allowlist_{stamp}", "due": plan["today"], "owner": "human", "status": "open", "action": "local",
        "ready": True,
        "title": ("프로젝트 에이전트 허용 목록 확정: Claude Code 에서 CEO 에게 본인 말로 확정하면 CEO 가 원문·날짜·범위를 "
                  "기록(직접 고쳐도 된다)" if ko else
                  "Confirm the proposed allowlist of the project agents: say it to the CEO in Claude Code in your own "
                  "words and it records your quote, date and scope (or edit the file yourself)"),
        "prep": ["Proposed: config/logic_agents.yaml, allowed_tools per agent (denied_tools stay as they are)",
                 "In Claude Code in this folder, for example: \"I confirm the allowlist for <agent>: it may edit files "
                 "in its own folder.\" The CEO then sets permissions_status: confirmed and writes "
                 "permissions_confirmed (date, by, quote, scope)",
                 "Until then each agent has only the tools of its own .claude/settings*.json; its first report "
                 "ends with 'Needed permission: ...'"]}


def starter_items(plan: dict, dest: Path, label: str, service_file: Optional[str], svc_state: str = "missing",
                  confirmed: bool = False) -> list:
    """The owner's remaining setup steps. svc_state: missing | differs | same (the registered copy)."""
    stamp = plan["today"].replace("-", "")
    items = []
    if plan["messenger"] == "telegram":
        items.append(telegram_item(plan, stamp, service_file))
    if service_file and svc_state != "same":
        items.append(register_item(plan, dest, stamp, service_file, plan["service"], label, svc_state == "differs",
                                   reason="the installer wrote a new service file"))
    if any(p["mode"] == "headless" for p in plan["projects"]) and not confirmed:
        items.append(allowlist_item(plan, stamp))
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


def queue_add_items(text: str, items: list) -> tuple:
    """(new text, added ids, dropped ids). Appends each item unless an open or done item with the same id prefix
    (everything before the date stamp) exists; a dropped one does not count. Comments are kept."""
    import yaml
    try:
        data = yaml.safe_load(text) or []
    except yaml.YAMLError:
        return text, [], []
    if not isinstance(data, list):
        return text, [], []
    ids = {str(i.get("id")) for i in data if isinstance(i, dict)}
    live = {str(i.get("id")) for i in data if isinstance(i, dict) and i.get("status") in ("open", "done")}
    new = []
    for item in items:
        prefix = re.sub(r"_\d{8}(_\d+)?$", "_", item["id"])
        if any(i.startswith(prefix) for i in live):
            continue
        iid, n = item["id"], 2
        while iid in ids:
            iid, n = f"{item['id']}_{n}", n + 1
        new.append({**item, "id": iid})
        ids.add(iid)
    if not new:
        return text, [], []
    dump = yaml.safe_dump(new, sort_keys=False, allow_unicode=True, width=1000)
    if re.search(r"(?m)^\[\]\s*$", text):
        out = re.sub(r"(?m)^\[\]\s*$", lambda m: dump.rstrip("\n"), text, count=1)
    else:
        out = text.rstrip("\n") + "\n" + dump
    try:
        ok = isinstance(yaml.safe_load(out), list)
    except yaml.YAMLError:
        ok = False
    return (out, [i["id"] for i in new], []) if ok else (text, [], [])


def queue_drop_open(text: str, prefix: str) -> tuple:
    """(new text, ids): set status: dropped on open items whose id starts with prefix."""
    parts = re.split(r"(?m)^(?=- )", text)
    out, dropped = [], []
    for part in parts:
        m = re.match(r"- id:\s*['\"]?([^'\"\n]+)", part)
        if m and m.group(1).strip().startswith(prefix):
            new, n = re.subn(r"(?m)^(\s+status:\s*)['\"]?open['\"]?\s*$", r"\1dropped", part, count=1)
            if n:
                dropped.append(m.group(1).strip())
                part = new
        out.append(part)
    return "".join(out), dropped


def set_yaml_value(text: str, key: str, literal: str) -> str:
    pat = re.compile(rf"^({re.escape(key)}:[ \t]*)(\"[^\"\n]*\"|'[^'\n]*'|\[[^\]\n]*\]|[^\s#]+)", re.M)
    new, n = pat.subn(lambda m: m.group(1) + literal, text, count=1)
    if not n:
        raise ValueError(f"key {key} not found")
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
    """(file name, text) for the platform's service manager, or (None, None). No date inside: the same
    config renders the same file, so a re-run does not look like a change."""
    from xml.sax.saxutils import escape
    if plan["service"] == "launchd":
        t = (TEMPLATES / "service" / "com.example.report-daemon.plist").read_text()
        note = (f"<!--\n  launchd user agent for scripts/report_daemon.py, written by scripts/install.py.\n"
                f"  Register it: paste the commands from the install summary (python3 scripts/install.py "
                f"--render-service prints them again).\n  Put the Telegram values into the INSTALLED copy "
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


# ---------------------------------------------------------------- reading back what is on disk
def _load_yaml_file(path: Path):
    import yaml
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def read_actual(dest: Path) -> dict:
    """What the installed files say. The summary is rendered from this, never from the answers."""
    reg = _load_yaml_file(dest / "config" / "logic_agents.yaml")
    projects = None
    if isinstance(reg, dict):
        projects = []
        agents = reg.get("agents") or {}
        for name, a in (agents.items() if isinstance(agents, dict) else []):
            a = a if isinstance(a, dict) else {}
            prot, rules, placeholder = [], [], False
            for r in a.get("denied_tools") or []:
                r = str(r)
                if is_placeholder(r):
                    placeholder = True
                    continue
                _, abs_path, _ = doctor.rule_path(r)
                if abs_path:
                    rules.append(r)
                    prot.append((abs_path, Path(doctor.glob_base(abs_path)).exists()))
            projects.append({"name": str(name), "mode": str(a.get("mode") or "headless"), "protected": prot,
                             "rules": sorted(rules), "placeholder": placeholder,
                             "none": str(a.get("protected_zone") or "").strip().lower() == "none"})
    cfg = _load_yaml_file(dest / "config" / "report_daemon.yaml")
    report = None
    if isinstance(cfg, dict):
        report = {"delivery": str(cfg.get("delivery") or "telegram").strip().lower(),
                  "report_times": [str(x) for x in (cfg.get("report_times") or [])],
                  "timezone": str(cfg.get("timezone") or ""),
                  "log_dir": str(cfg.get("log_dir") or "state/report_daemon"),
                  "claude_cmd": str(cfg.get("claude_cmd") or "claude")}
    services = []
    sdir = dest / "service"
    files = (sorted(sdir.glob("*.plist")) + sorted(sdir.glob("*.service"))) if sdir.is_dir() else []
    for f in files:
        text = f.read_text(errors="replace")
        kind = "launchd" if f.suffix == ".plist" else "systemd"
        m = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", text)
        installed = doctor.installed_service_path(f, kind)
        state = "missing"
        if installed.is_file():
            state = "differs" if doctor.service_differs(text, installed.read_text(errors="replace")) else "same"
        services.append({"rel": f"service/{f.name}", "kind": kind, "label": m.group(1) if m else f.stem,
                         "telegram": kind != "launchd" or "REPORT_TELEGRAM_TOKEN" in text, "installed": state})
    return {"projects": projects, "report": report, "services": services}


def differences(plan: dict, actual: dict, service_rel: Optional[str]) -> list:
    """Places where the files on disk do not say what the answers said (a kept file, typically)."""
    out = []
    rep = actual["report"]
    if rep is None:
        out.append("config/report_daemon.yaml cannot be read")
    else:
        if rep["delivery"] != plan["messenger"]:
            out.append(f"config/report_daemon.yaml says delivery: {rep['delivery']}; you answered {plan['messenger']}")
        if rep["report_times"] != plan["report_times"]:
            out.append(f"config/report_daemon.yaml says report_times {', '.join(rep['report_times']) or '(none)'}; "
                       f"you answered {', '.join(plan['report_times'])}")
        if rep["timezone"] != plan["timezone"]:
            out.append(f"config/report_daemon.yaml says timezone {rep['timezone']}; you answered {plan['timezone']}")
    projects = actual["projects"]
    if projects is None:
        out.append("config/logic_agents.yaml cannot be read as a registry")
    else:
        have = {p["name"]: p for p in projects}
        want = {p["name"]: p for p in plan["projects"]}
        if set(have) != set(want):
            out.append(f"config/logic_agents.yaml lists the projects {', '.join(sorted(have)) or '(none)'}; "
                       f"you answered {', '.join(sorted(want)) or '(none)'}")
        for name in sorted(set(have) & set(want)):
            h, w = have[name], want[name]
            if h["mode"] != w["mode"]:
                out.append(f"config/logic_agents.yaml: {name} is mode {h['mode']}; you answered {w['mode']}")
            want_rules = sorted(r for r in w["rules"] if not is_placeholder(r) and doctor.rule_path(r)[1])
            if h["rules"] != want_rules or h["none"] != (w["state"] == "none"):
                out.append(f"config/logic_agents.yaml: the protected zone of {name} is not the one you answered")
    if service_rel:
        svc = next((s for s in actual["services"] if s["rel"] == service_rel), None)
        if svc is None:
            out.append(f"{service_rel} is missing")
        elif plan["messenger"] == "telegram" and not svc["telegram"]:
            out.append(f"{service_rel} has no Telegram variables; you answered telegram")
    return out


# ---------------------------------------------------------------- writing
class Writer:
    def __init__(self, dest: Path, force: bool, confirm: Optional[Callable] = None):
        self.dest, self.force, self.confirm = dest, force, confirm or (lambda rel: False)
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.created, self.same, self.kept, self.replaced, self.notes = [], [], [], [], []
        self.updated = 0  # installer-owned files refreshed without a backup (templates/)

    def write(self, rel: str, text: str, record: bool = False, owned: bool = False) -> bool:
        """record=True: the owner's own records (never replaced once they exist).
        owned=True: the installer's own data (templates/): refreshed without asking and without a backup."""
        p = self.dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            old = p.read_text(errors="replace")
            if old == text:
                self.same.append(rel)
                return True
            if owned:
                self.updated += 1
                p.write_text(text)
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

    def copy(self, src: Path, rel: str, owned: bool = False) -> None:
        dst = self.dest / rel
        if dst.exists() and dst.resolve() == src.resolve():
            self.same.append(rel)
            return
        if self.write(rel, src.read_text(), owned=owned):
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


def template_files() -> list:
    return [p for p in sorted(TEMPLATES.rglob("*"))
            if p.is_file() and p.name != ".DS_Store" and "__pycache__" not in p.parts]


def update_queue(dest: Path, w: Writer, items: list, drop_prefixes=()) -> None:
    queue = dest / "config" / "owner_action_queue.yaml"
    text = queue.read_text()
    original = text
    text, removed = remove_example_blocks(text)
    if removed:
        w.notes.append(f"removed {len(removed)} template example item(s) from the queue")
    dropped = []
    for prefix in drop_prefixes:
        text, d = queue_drop_open(text, prefix)
        dropped += d
    text, added, _ = queue_add_items(text, items)
    if text != original:
        backup = queue.with_name(f"{queue.name}.bak-{w.stamp}")
        shutil.copy2(queue, backup)
        queue.write_text(text)
        if added:
            w.notes.append(f"queue: added {', '.join(added)} (backup {backup.name})")
        if dropped:
            w.notes.append(f"queue: set status dropped on {', '.join(dropped)}")


def write_answers_record(dest: Path, rec: dict, w: Writer) -> None:
    import yaml
    w.write("config/install_answers.yaml",
            "# Answers of the last scripts/install.py run (no secrets). Re-run: python3 scripts/install.py "
            "--answers config/install_answers.yaml\n" + yaml.safe_dump(rec, sort_keys=False, allow_unicode=True))


def install(plan: dict, problems: list, dest: Path, force: bool = False,
            confirm: Optional[Callable] = None, out=None, lang: str = "en") -> int:
    out = out or sys.stdout
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
    write_answers_record(dest, answers_record(plan), w)
    for src, rel in COPIES:
        if (TEMPLATE_ROOT / src).is_file():
            w.copy(TEMPLATE_ROOT / src, rel)
    for src in template_files():  # so --reconfigure, --render-service and a re-install work in this folder
        w.copy(src, str(src.relative_to(TEMPLATE_ROOT)), owned=True)
    if service_rel:
        w.write(service_rel, service_text)
    svc = next((s for s in read_actual(dest)["services"] if s["rel"] == service_rel), None)
    reg = _load_yaml_file(dest / "config" / "logic_agents.yaml")
    confirmed = isinstance(reg, dict) and reg.get("permissions_status") == "confirmed"
    items = starter_items(plan, dest, label, service_rel, svc["installed"] if svc else "missing", confirmed)
    queue = dest / "config" / "owner_action_queue.yaml"
    if queue.is_file():
        drop = ("setup_telegram_",) if plan["messenger"] == "none" else ()
        if svc and svc["installed"] != "same" and any(r.startswith(f"{service_rel} ") for r in w.replaced):
            # the service file changed: the open (re)register step must carry the new commands
            drop += ("reregister_report_service_",) if svc["installed"] == "differs" else ("register_report_service_",)
        update_queue(dest, w, items, drop_prefixes=drop)
        w.kept.append("config/owner_action_queue.yaml")
    else:
        w.write("config/owner_action_queue.yaml", render_queue(plan, items))
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
    actual = read_actual(dest)
    fixes = []
    copies = [rel for rel in w.kept if rel not in RECORDS and has_placeholder_text(dest / rel)]
    if copies:
        fixes.append(say(lang, "fix_template_copies", n=len(copies), files=", ".join(copies)))
    diffs = differences(plan, actual, service_rel)
    if diffs:
        fixes.append(say(lang, "fix_kept_differs"))
        fixes += [f"  {x}" for x in diffs]
    fixes += problems
    complete = not fixes and not rep.failures
    for line in summary_lines(dest, actual, rep, fixes, complete, lang, w=w):
        print(line, file=out)
    return 0 if complete else 1


# ---------------------------------------------------------------- summary (read from the files)
def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _box(lines: list) -> list:
    width = max(_width(x) for x in lines) + 8
    return ["!" * width] + [f"!!  {x}{' ' * (width - 8 - _width(x))}  !!" for x in lines] + ["!" * width]


def summary_lines(dest: Path, actual: dict, rep, fixes: list, complete: bool, lang: str,
                  w: Optional[Writer] = None, notes=()) -> list:
    L = ["", "== install summary ==", f"folder:   {dest}"]
    if w is None:
        L.append(f"files:    {say(lang, 'nothing_changed')}")
    else:
        L.append(f"created:  {len(w.created)} file(s); unchanged {len(w.same)}; kept as they were {len(w.kept)}"
                 + (f"; replaced {len(w.replaced)}" if w.replaced else "")
                 + (f"; templates/ refreshed {w.updated}" if w.updated else ""))
        for rel in w.kept:
            if rel not in RECORDS:
                L.append(f"  kept existing {rel} (different from the new one; re-run with --force to replace it)")
        L += [f"  replaced {r}" for r in w.replaced]
        L += [f"  {n}" for n in w.notes]
    L += [f"  {n}" for n in notes]
    projects = actual["projects"]
    if projects is None:
        L.append("projects: (config/logic_agents.yaml cannot be read)")
    else:
        L.append("projects: " + (", ".join(f"{x['name']} ({x['mode']}, {len(x['protected'])} protected)"
                                           for x in projects) or "none yet"))
        for x in projects:
            for path, exists in x["protected"]:
                L.append(f"  protected  {x['name']}: {path}" + ("" if exists else "  (does not exist yet)"))
            if x["placeholder"]:
                L.append(f"  protected  {x['name']}: (a template placeholder, protects nothing)")
        unprotected = [x["name"] for x in projects if x["none"] and not x["protected"]]
        if unprotected or not projects:
            who = ", ".join(unprotected) if unprotected else say(lang, "box_no_project")
            L.append("")
            L += _box([say(lang, "box_none", who=who), say(lang, "box_l2"), say(lang, "box_l3"),
                       say(lang, "box_l4"), say(lang, "box_l5")] if unprotected else
                      [say(lang, "box_none", who=who), say(lang, "box_l2_noproj"), say(lang, "box_l4_noproj")])
    r = actual["report"]
    if r is None:
        L.append("reports:  (config/report_daemon.yaml cannot be read)")
    else:
        where = f" ({say(lang, 'saved_files', d=r['log_dir'])})" if r["delivery"] == "none" else ""
        L.append(f"reports:  {', '.join(r['report_times'])} {r['timezone']} via {r['delivery']}{where}")
    for s in actual["services"]:
        L.append(f"service:  {s['rel']} (label {s['label']}; registered: {REGISTERED[s['installed']]})")
    login = getattr(rep, "login", None)
    L.append("sign-in:  " + say(lang, {True: "signin_ok", False: "signin_no"}.get(login, "signin_unknown")))
    L.append(f"doctor:   {rep.count('ok')} ok, {rep.count('warn')} warning(s), {rep.count('fail')} failure(s)")
    L.append("")
    if not complete:
        L.append(say(lang, "status_not"))
        L += [f"  - {x}" if not x.startswith("  ") else f"  {x}" for x in fixes]
        L += [f"  - {x}" for x in rep.failures]
        return L
    L.append(say(lang, "status_ok"))
    L += ["", say(lang, "next")]
    cd = "" if Path.cwd().resolve() == dest.resolve() else f"cd {shlex.quote(str(dest))} && "
    L.append(f"  {say(lang, 'next_report')}  {cd}python3 scripts/report_daemon.py --once --dry-run")
    for s in actual["services"]:
        delivery = r["delivery"] if r else "none"
        if s["installed"] == "same":
            L.append(f"  {say(lang, 'next_registered')}")
            continue
        L.append(f"  {say(lang, 'next_reregister' if s['installed'] == 'differs' else 'next_service')}")
        for note, lines in service_blocks(dest, s["rel"], s["kind"], s["label"], delivery,
                                          registered=s["installed"] != "missing"):
            L.append(f"     {say(lang, note)}")
            L += [f"       {ln}" for ln in lines]
    headless = [x["name"] for x in (projects or []) if x["mode"] == "headless"]
    if headless:
        L.append(f"  {say(lang, 'next_agent')}  {cd}python3 scripts/logic_agent.py {headless[0]} "
                 f"-p \"Read your handoff and report status in 5 lines\" --dry-run")
    opener = "open -e" if sys.platform == "darwin" else "xdg-open"
    L.append(f"  {say(lang, 'next_queue')}")
    L.append(f"     {say(lang, 'next_queue_open', cmd=f'{opener} config/owner_action_queue.yaml')}")
    return L


def keep_mode(dest: Path, lang: str, out=None) -> int:
    """Change nothing: run the check and show what the files say."""
    out = out or sys.stdout
    print("", file=out)
    print("== doctor ==", file=out)
    rep = doctor.run(dest, out=out)
    fixes = []
    if any("placeholder" in m for m in rep.failures):
        fixes.append(say(lang, "fix_template_copies_keep"))
    complete = not rep.failures
    for line in summary_lines(dest, read_actual(dest), rep, fixes, complete, lang):
        print(line, file=out)
    return 0 if complete else 1


# ---------------------------------------------------------------- --render-service, --reconfigure
def charter_company(dest: Path) -> str:
    try:
        m = re.match(r"#\s*(.+?)\s+CEO Agent\s*$", (dest / "CLAUDE.md").read_text().splitlines()[0])
        return m.group(1) if m and "{{" not in m.group(1) else ""
    except (OSError, IndexError):
        return ""


def service_plan(dest: Path, messenger: Optional[str] = None) -> tuple:
    """(plan-like dict for render_service, label), from the files on disk."""
    answers = _load_yaml_file(dest / "config" / "install_answers.yaml")
    answers = answers if isinstance(answers, dict) else {}
    actual = read_actual(dest)
    r = actual["report"] or {}
    kind = str(answers.get("service") or "auto").lower()
    if kind not in ("launchd", "systemd", "none"):
        kind = {"darwin": "launchd", "linux": "systemd"}.get(sys.platform, "none")
    label = None
    for s in actual["services"]:
        if s["kind"] == kind or kind == "none":
            kind, label = s["kind"], s["label"]
            break
    company = str(answers.get("company") or charter_company(dest) or dest.name)
    resolved, elsewhere = doctor.find_claude(r.get("claude_cmd"))
    plan = {"service": kind, "today": date.today().isoformat(), "messenger": messenger or r.get("delivery") or "none",
            "claude_cmd": resolved or elsewhere or str(r.get("claude_cmd") or "claude")}
    return plan, label or f"com.{slug(company)}.report-daemon"


def _need_templates(lang: str) -> bool:
    if (TEMPLATES / "service").is_dir():
        return True
    print(say(lang, "no_templates", path=TEMPLATES / "service"), file=sys.stderr)
    return False


def write_service(dest: Path, w: Writer, messenger: Optional[str] = None) -> tuple:
    """(service rel or None, kind, label, changed)."""
    plan, label = service_plan(dest, messenger)
    name, text = render_service(plan, dest, label)
    if not name:
        return None, plan["service"], label, False
    rel = f"service/{name}"
    before = len(w.same)
    w.write(rel, text)
    (dest / (read_actual(dest)["report"] or {}).get("log_dir", "state/report_daemon")).mkdir(parents=True, exist_ok=True)
    return rel, plan["service"], label, len(w.same) == before


def render_service_cmd(dest: Path, lang: str, out=None) -> int:
    out = out or sys.stdout
    if not (dest / "config" / "report_daemon.yaml").is_file():
        print(say(lang, "no_install", dest=dest), file=sys.stderr)
        return 2
    if not _need_templates(lang):
        return 2
    w = Writer(dest, force=True)
    rel, kind, label, changed = write_service(dest, w, None)
    if not rel:
        print(say(lang, "no_service_manager"), file=out)
        return 1
    print(say(lang, "rendered" if changed else "rendered_same", rel=rel)
          + (f" ({w.replaced[0].split('(', 1)[1]}" if w.replaced else ""), file=out)
    svc = next(s for s in read_actual(dest)["services"] if s["rel"] == rel)
    delivery = (read_actual(dest)["report"] or {}).get("delivery", "none")
    if svc["installed"] == "same":
        print(say(lang, "next_registered"), file=out)
        print(say(lang, "register_again"), file=out)
    else:
        print(say(lang, "next_reregister" if svc["installed"] == "differs" else "next_service"), file=out)
    for note, lines in service_blocks(dest, rel, kind, label, delivery, registered=svc["installed"] != "missing"):
        print(f"  {say(lang, note)}", file=out)
        for ln in lines:
            print(f"    {ln}", file=out)
    return 0


def update_answers_record(dest: Path, changes: dict, w: Writer) -> None:
    rec = _load_yaml_file(dest / "config" / "install_answers.yaml")
    if isinstance(rec, dict):
        rec.update(changes)
        w.force = True
        write_answers_record(dest, rec, w)


def reconfigure(dest: Path, item: str, pr: Optional[Prompter], to: Optional[str], raw: Optional[dict],
                lang: str, out=None) -> int:
    """Change one part of an existing installation in place (other edits in the files are kept)."""
    out = out or sys.stdout
    cfg_path = dest / "config" / "report_daemon.yaml"
    if not cfg_path.is_file():
        print(say(lang, "no_install", dest=dest), file=sys.stderr)
        return 2
    actual = read_actual(dest)
    cur = actual["report"] or {}
    raw = raw or {}
    w = Writer(dest, force=True)
    text = cfg_path.read_text()
    answers = _load_yaml_file(dest / "config" / "install_answers.yaml")
    answers = answers if isinstance(answers, dict) else {}
    language = str(answers.get("language") or ("Korean" if lang == "ko" else "English"))
    plan = {"language": language, "today": date.today().isoformat()}
    restart = False
    if item == "delivery":
        new = to or raw.get("messenger")
        if new is None and pr:
            new = pr.ask("messenger", default=cur.get("delivery") or "telegram",
                         validate=lambda v: None if v.lower() in DELIVERIES else pr.t("err_messenger"))
        if new is None:
            print(say(lang, "need_to"), file=sys.stderr)
            return 2
        new = str(new).strip().lower()
        if new not in DELIVERIES:
            print(f"delivery must be telegram or none (got {new!r})", file=sys.stderr)
            return 2
        try:
            text = set_yaml_value(text, "delivery", new)
        except ValueError:
            text = text.rstrip("\n") + f"\ndelivery: {new}\n"
        w.write("config/report_daemon.yaml", text)
        rel, kind, label = None, None, None
        if _need_templates(lang):
            rel, kind, label, _ = write_service(dest, w, new)
        svc = next((s for s in read_actual(dest)["services"] if s["rel"] == rel), None)
        stamp = plan["today"].replace("-", "")
        items, drop = [], ()
        plan["messenger"] = new
        if new == "telegram":
            items.append(telegram_item(plan, stamp, rel))
        else:
            drop = ("setup_telegram_",)
        if svc and svc["installed"] != "same":  # the open (re)register step must carry the new commands
            registered = svc["installed"] == "differs"
            drop += ("reregister_report_service_",) if registered else ("register_report_service_",)
            items.append(register_item(plan, dest, stamp, rel, kind, label, registered=registered,
                                       reason=f"delivery changed to {new}"))
        if (dest / "config" / "owner_action_queue.yaml").is_file():
            update_queue(dest, w, items, drop_prefixes=drop)
        update_answers_record(dest, {"messenger": new}, w)
        w.notes.insert(0, f"delivery: {cur.get('delivery')} -> {new}")
    else:  # schedule
        tz = raw.get("timezone")
        times = raw.get("report_times")
        if pr and tz is None:
            tz = pr.ask("timezone", default=cur.get("timezone") or detect_timezone(),
                        validate=lambda v: pr.t("err_tz", v=v) if tz_error(v) else None)
        if pr and times is None:
            times = pr.ask("report_times", default=", ".join(cur.get("report_times") or ["08:00", "20:00"]),
                           validate=lambda v: pr.t("err_times", v=v) if parse_times(v)[1] else None)
        tz = str(tz or cur.get("timezone") or "")
        parsed, err = parse_times(times if times is not None else cur.get("report_times"))
        for e in (tz_error(tz), err):
            if e:
                print(e, file=sys.stderr)
                return 2
        text = set_yaml_value(text, "timezone", q(tz))
        text = set_yaml_value(text, "report_times", "[" + ", ".join(q(x) for x in parsed) + "]")
        w.write("config/report_daemon.yaml", text)
        update_answers_record(dest, {"timezone": tz, "report_times": parsed}, w)
        w.notes.insert(0, f"schedule: {', '.join(cur.get('report_times') or [])} {cur.get('timezone')} -> "
                          f"{', '.join(parsed)} {tz}")
        restart = True
    print("", file=out)
    print("== doctor ==", file=out)
    rep = doctor.run(dest, out=out)
    actual = read_actual(dest)
    for line in summary_lines(dest, actual, rep, [], not rep.failures, lang, w=w):
        print(line, file=out)
    if rep.failures:  # summary_lines hides "next" then, but re-registering is often the fix itself
        delivery = (actual["report"] or {}).get("delivery", "none")
        for s in actual["services"]:
            if s["installed"] == "differs":
                print(f"\n{say(lang, 'next_reregister')}", file=out)
                for note, lines in service_blocks(dest, s["rel"], s["kind"], s["label"], delivery, registered=True):
                    print(f"  {say(lang, note)}", file=out)
                    for ln in lines:
                        print(f"    {ln}", file=out)
    running = [s for s in actual["services"] if s["installed"] == "same"]
    if restart and running and not rep.failures:
        print(f"\n{say(lang, 'restart_after')}", file=out)
        for ln in restart_lines(running[0]["kind"], running[0]["label"]):
            print(f"  {ln}", file=out)
    return 0 if not rep.failures else 1


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
    cmd = ([sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
           + ([] if in_venv else ["--user"]) + ["pyyaml"])
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
    ap.add_argument("--answers", help="YAML answers file: install (or --reconfigure) without questions")
    ap.add_argument("--force", action="store_true", help="replace existing generated files (a backup is kept)")
    ap.add_argument("--doctor", action="store_true", help="only run the installation check")
    ap.add_argument("--reconfigure", choices=RECONFIGURE,
                    help="change one part of an existing installation in place: delivery or schedule")
    ap.add_argument("--to", help="with --reconfigure delivery: telegram or none, without a question")
    ap.add_argument("--render-service", action="store_true",
                    help="(re)write the service file in service/ from config/ and print the commands to register it")
    ap.add_argument("--lang", choices=("en", "ko"), help="language of the questions (default: from your locale)")
    a = ap.parse_args(argv)
    if a.to and a.reconfigure != "delivery":
        ap.error("--to goes with --reconfigure delivery")
    dest = Path(os.path.expanduser(a.dest)).resolve()
    lang = a.lang or detect_lang()
    if a.doctor:
        return 1 if doctor.run(dest).failures else 0
    interactive = not (a.answers or a.to or a.render_service)
    pr = Prompter(lang, inp=inp) if interactive else None
    if not ensure_yaml(interactive, lang, pr):
        return 1
    import yaml
    if a.render_service:
        return render_service_cmd(dest, lang)
    raw = None
    if a.answers:
        try:
            raw = yaml.safe_load(Path(a.answers).expanduser().read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            print(f"cannot read the answers file {a.answers}: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw, dict):
            print(f"the answers file must be a YAML mapping: {a.answers}", file=sys.stderr)
            return 2
    try:
        if a.reconfigure:
            return reconfigure(dest, a.reconfigure, pr, a.to, raw, lang)
        dest.mkdir(parents=True, exist_ok=True)
        force = a.force
        if raw is None:
            existing = [r for r in GENERATED if (dest / r).is_file()]
            print(say(lang, "intro", dest=dest, n=len(BASE_QUESTIONS), p=len(PROJECT_QUESTIONS),
                      m=math.ceil(len(BASE_QUESTIONS) * SECONDS_PER_QUESTION / 60)))
            if existing and not a.force:
                choice = pr.ask("existing", default="1", files=", ".join(existing),
                                validate=lambda v: None if v in ("1", "2", "3") else pr.t("err_choice", opts="1, 2, 3"))
                if choice == "1":
                    return keep_mode(dest, lang)
                if choice == "3":
                    print(say(lang, "aborted"))
                    return 0
                force = True
            prev = _load_yaml_file(dest / "config" / "install_answers.yaml")
            raw = collect_interactive(pr, dest, prev if isinstance(prev, dict) else {})
    except KeyboardInterrupt:
        print(say(lang, "stopped"))
        return 130
    plan, problems = normalize(raw, dest, date.today())
    return install(plan, problems, dest, force=force, lang=lang)


if __name__ == "__main__":
    sys.exit(main())
