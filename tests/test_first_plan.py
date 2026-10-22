"""Tests for scripts/first_plan.py (the first two-week plan).

Pinned behaviour: the run may read anything but write only the plan file and the queue (--allowedTools), the
.claude/settings.json deny rules are passed again (--disallowedTools), --max-turns is set (0 = none; a CLI that does
not know it gets one retry without it), never --permission-mode / --continue / --resume, the prompt carries the
company, the language, the plan path and the no-invention rule | after the run: the plan path, the number of queue
items added (being prepared vs fact questions) and the next command | an earlier queue item the agent changed is put
back, a new item other than a fact question stays ready: false, a queue that no longer parses is restored | a reply
is saved as the plan when the agent could not write the file | a turn limit says how to rerun | a changed protected
file is reported and fails the run | the real subprocess path drops the nested-session variables.

Run: python3 -m pytest tests -q   (or: python3 -m unittest discover tests)
Works in the template repository and in an installed CEO project. Temp folders and a stub `claude` only.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import first_plan as fp  # noqa: E402

TODAY = date(2026, 10, 22)
QUEUE = """# Owner action queue (comments stay)
- id: register_report_service_20261022
  due: '2026-10-22'
  owner: human
  status: open
  action: local
  ready: true
  title: Register the report daemon service
"""
NEW_ITEMS = """- id: first_plan_parent_survey_20261022
  due: "2026-10-29"
  owner: human
  status: open
  action: external
  ready: false               # the CEO drafts the survey first
  title: "Send the parent survey"
  prep:
    - "to prepare first: survey draft"
- id: first_plan_school_list_20261022
  due: "2026-10-24"
  owner: human
  status: open
  action: facts
  ready: true
  title: "Which schools are in scope?"
"""
PLAN = "# First plan\n\n## 1. Where the company stands\nPreparing.\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "co"
        work = self.root / "work"
        for d in ("config", ".claude/agents", "advisory_drafts/reviews", "work/billing"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        (self.root / "CLAUDE.md").write_text("# Pickup Co CEO Agent\n\n## Company overview\n\n- Pickup Co: pickups\n")
        (self.root / "HANDOFF.md").write_text("# HANDOFF\n")
        (work / "HANDOFF.md").write_text("# work handoff\n")
        (work / "billing" / "rates.yaml").write_text("rate: 1\n")
        for r in ("cto", "cfo"):
            (self.root / ".claude" / "agents" / f"{r}.md").write_text(f"---\nname: {r}\n---\n")
        self.stub = self.tmp / "bin" / "claude"
        self.stub.parent.mkdir()
        self.stub.write_text("#!/bin/sh\nexit 0\n")
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.answers(language="English")
        (self.root / "config" / "report_daemon.yaml").write_text(
            yaml.safe_dump({"timezone": "Asia/Seoul", "claude_cmd": str(self.stub)}))
        (self.root / "config" / "logic_agents.yaml").write_text(yaml.safe_dump(
            {"permissions_status": "proposed", "agents": {"work": {"path": str(work), "handoff": "HANDOFF.md",
                                                                  "mode": "headless"}}}))
        self.deny = [f"Edit(/{work}/billing/**)", "Bash(launchctl *pickup-bot*)"]
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": self.deny}}))
        self.queue_path = self.root / "config" / "owner_action_queue.yaml"
        self.queue_path.write_text(QUEUE)
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, **kw):
        (self.root / "config" / "install_answers.yaml").write_text(
            yaml.safe_dump({"company": "Pickup Co", "owner": "Ji-woo", **kw}, allow_unicode=True))

    def runner(self, *steps, reply="saved advisory_drafts/2026-10-22-first-plan.md, 2 items", rc=0, extra=None):
        """A stub run: each step edits the folder like the agent would; returns the claude JSON."""
        def run(cmd, stdin, cwd, timeout, progress=None):
            self.calls.append({"cmd": cmd, "stdin": stdin, "cwd": cwd, "timeout": timeout})
            for step in steps:
                step()
            body = {"result": reply, "is_error": rc != 0, "num_turns": 12, "subtype": "success", **(extra or {})}
            return rc, json.dumps(body), "", 1.0
        return run

    def write_plan(self, name="2026-10-22-first-plan.md"):
        return lambda: (self.root / "advisory_drafts" / name).write_text(PLAN)

    def append_items(self, text=NEW_ITEMS):
        return lambda: self.queue_path.write_text(self.queue_path.read_text() + text)

    def run_plan(self, runner, **kw) -> tuple:
        out = io.StringIO()
        rc = fp.run(self.root, runner=runner, today=TODAY, out=out, **kw)
        return rc, out.getvalue()

    def items(self) -> list:
        return yaml.safe_load(self.queue_path.read_text())


class Command(Base):
    def test_writes_only_the_plan_and_the_queue_and_denies_the_protected_zone(self):
        rc, out = self.run_plan(self.runner(self.write_plan(), self.append_items()))
        self.assertEqual(rc, 0, out)
        cmd = self.calls[0]["cmd"]
        self.assertEqual(cmd[:4], [str(self.stub), "-p", "--output-format", "json"])
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
        base = str(self.root)  # Edit(/ + /abs = Edit(//abs: absolute
        self.assertEqual(allowed, ["Read", "Glob", "Grep",
                                   f"Edit(/{base}/advisory_drafts/**)", f"Write(/{base}/advisory_drafts/**)",
                                   f"Edit(/{base}/config/owner_action_queue.yaml)",
                                   f"Write(/{base}/config/owner_action_queue.yaml)"])
        self.assertNotIn(f"Edit(/{base}/**)", cmd)  # not the whole folder: CLAUDE.md, the registry, settings stay
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--max-turns")]
        self.assertEqual(denied, self.deny)
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "40")
        for flag in ("--permission-mode", "--continue", "--resume", "--dangerously-skip-permissions"):
            self.assertNotIn(flag, cmd)
        self.assertEqual(self.calls[0]["cwd"], str(self.root))
        prompt = self.calls[0]["stdin"]
        for part in ("Pickup Co", "Write everything you produce in English", "advisory_drafts/2026-10-22-first-plan.md",
                     "config/owner_action_queue.yaml", "first_plan_<short_name>_20261022", "by 2026-11-05",
                     "Do not invent numbers", "finish within 10 minutes", "cto, cfo", f"work: {self.root}/work/HANDOFF.md"):
            self.assertIn(part, prompt)
        self.assertNotIn("{{", prompt)

    def test_no_turn_limit_and_a_cli_without_the_flag(self):
        self.run_plan(self.runner(self.write_plan()), max_turns=0)
        self.assertNotIn("--max-turns", self.calls[0]["cmd"])
        self.calls.clear()
        good = self.runner(self.write_plan("2026-10-22-first-plan-2.md"))
        first = [True]

        def runner(cmd, stdin, cwd, timeout, progress=None):
            if first.pop() if first else False:
                self.calls.append({"cmd": cmd})
                return 1, "", "error: unknown option '--max-turns'", 0.1
            return good(cmd, stdin, cwd, timeout, progress)
        rc, out = self.run_plan(runner)
        self.assertEqual(rc, 0, out)
        self.assertIn("--max-turns", self.calls[0]["cmd"])
        self.assertNotIn("--max-turns", self.calls[1]["cmd"])
        self.assertIn("does not know --max-turns", out)

    def test_dry_run_prints_and_calls_nothing(self):
        rc, out = self.run_plan(self.runner(), dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])
        self.assertIn("--allowedTools", out)
        self.assertIn("Write everything you produce in English", out)
        self.assertFalse(list((self.root / "advisory_drafts").glob("*.md")))

    def test_not_an_installation(self):
        (self.root / "config" / "report_daemon.yaml").unlink()
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(self.run_plan(self.runner())[0], 2)


class AfterTheRun(Base):
    def test_summary_names_the_file_the_items_and_the_next_command(self):
        rc, out = self.run_plan(self.runner(self.write_plan(), self.append_items()))
        self.assertEqual(rc, 0, out)
        self.assertIn("saved:    advisory_drafts/2026-10-22-first-plan.md (written by the CEO agent)", out)
        self.assertIn("queue:    2 item(s) added (1 being prepared: ready: false; 1 fact question(s))", out)
        self.assertIn("- Send the parent survey (2026-10-29, external, ready: false)", out)
        self.assertIn("protected zone: unchanged (1 file(s) compared)", out)
        self.assertIn("; 12 turns", out)
        self.assertIn("next:     python3 scripts/report_daemon.py --once --dry-run", out)
        self.assertIn("It takes about 5-15 minutes and uses your own Claude subscription. Almost no messages while it "
                      "runs is normal.", out)
        self.assertIn("# the CEO drafts the survey first", self.queue_path.read_text())  # the agent's text kept as is
        self.assertTrue(list((self.root / "state" / "first_plan").glob("*.json")))

    def test_earlier_items_are_put_back_and_unprepared_items_stay_not_ready(self):
        def edit():
            text = self.queue_path.read_text().replace("ready: true\n  title: Register", "ready: false\n  title: Register")
            self.queue_path.write_text(text + NEW_ITEMS.replace("ready: false               # the CEO drafts the "
                                                                "survey first", "ready: true"))
        rc, out = self.run_plan(self.runner(self.write_plan(), edit))
        self.assertEqual(rc, 0, out)
        items = {i["id"]: i for i in self.items()}
        self.assertIs(items["register_report_service_20261022"]["ready"], True)  # restored
        self.assertIs(items["first_plan_parent_survey_20261022"]["ready"], False)  # nothing prepared yet
        self.assertIs(items["first_plan_school_list_20261022"]["ready"], True)  # a question is ready to answer
        self.assertIn("# Owner action queue (comments stay)", self.queue_path.read_text())
        self.assertIn("earlier item(s) register_report_service_20261022", out)
        self.assertTrue(list(self.queue_path.parent.glob("owner_action_queue.yaml.bak-first-plan-*")))
        self.assertIn("queue:    2 item(s) added", out)

    def test_a_rewritten_queue_keeps_the_earlier_comments(self):
        def rewrite():
            self.queue_path.write_text(QUEUE.split("\n", 1)[1] + NEW_ITEMS)  # the header comment dropped
        rc, out = self.run_plan(self.runner(self.write_plan(), rewrite))
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.queue_path.read_text().startswith("# Owner action queue (comments stay)\n"))
        self.assertEqual(len(self.items()), 3)
        self.assertIn("rewritten instead of appended", out)

    def test_an_empty_queue_filled_by_the_agent_is_an_append(self):
        self.queue_path.write_text("# header\n\n[]\n")
        rc, out = self.run_plan(self.runner(self.write_plan(),
                                            lambda: self.queue_path.write_text("# header\n\n" + NEW_ITEMS)))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("redone", out)
        self.assertIn("# the CEO drafts the survey first", self.queue_path.read_text())

    def test_a_queue_that_no_longer_parses_is_restored(self):
        rc, out = self.run_plan(self.runner(self.write_plan(), self.append_items("- id: [broken\n")))
        self.assertEqual(self.queue_path.read_text(), QUEUE)
        self.assertIn("the earlier queue was restored", out)

    def test_the_reply_becomes_the_plan_when_the_agent_could_not_write_it(self):
        long_reply = "## 1. Where the company stands\n" + "Preparing a pickup service. " * 20
        rc, out = self.run_plan(self.runner(reply=long_reply))
        self.assertEqual(rc, 0, out)
        self.assertIn("its reply was saved there", out)
        self.assertIn("Preparing a pickup service.", (self.root / "advisory_drafts" / "2026-10-22-first-plan.md").read_text())
        self.assertIn("queue:    no item added", out)

    def test_another_name_is_moved_where_the_reports_look(self):
        rc, out = self.run_plan(self.runner(self.write_plan("first_plan.md")))
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.root / "advisory_drafts" / "2026-10-22-first-plan.md").is_file())
        self.assertIn("named it first_plan.md", out)

    def test_turn_limit_says_how_to_rerun(self):
        rc, out = self.run_plan(self.runner(reply="", rc=1, extra={"subtype": "error_max_turns"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("NO PLAN", out)
        self.assertIn("python3 scripts/first_plan.py --max-turns 80", out)

    def test_a_changed_protected_file_is_reported_and_fails(self):
        def touch():
            p = self.root / "work" / "billing" / "rates.yaml"
            p.write_text("rate: 2 (changed)\n")
        rc, out = self.run_plan(self.runner(self.write_plan(), touch))
        self.assertEqual(rc, 1, out)
        self.assertIn("PROTECTED ZONE CHANGED", out)
        self.assertIn("rates.yaml", out)

    def test_korean_messages(self):
        self.answers(language="Korean")
        rc, out = self.run_plan(self.runner(self.write_plan(), self.append_items()))
        self.assertEqual(rc, 0, out)
        self.assertIn("약 5~15분 걸리고 본인의 Claude 구독 사용량을 씁니다. 진행 중 메시지가 거의 없어도 정상입니다.", out)
        self.assertIn("큐 추가:  2건 (준비 중 ready:false 1건, 사실 확인 질문 1건)", out)
        self.assertIn("Write everything you produce in Korean", self.calls[0]["stdin"])


class RealSubprocess(Base):
    def test_stub_cli_gets_the_prompt_on_stdin_without_nested_session_variables(self):
        log = self.tmp / "argv.log"
        self.stub.write_text(f"""#!/bin/sh
printf '%s\\n' "$@" > {log}
env > {self.tmp}/env.log
cat > {self.tmp}/stdin.log
printf '{PLAN}' > {self.root}/advisory_drafts/2026-10-22-first-plan.md
printf '%s' '{{"result": "done", "is_error": false, "num_turns": 3}}'
""")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}):
            rc = fp.run(self.root, today=TODAY, out=out)
        self.assertEqual(rc, 0, out.getvalue())
        argv = log.read_text().splitlines()
        self.assertIn("--allowedTools", argv)
        self.assertIn("Pickup Co", (self.tmp / "stdin.log").read_text())
        env = (self.tmp / "env.log").read_text()
        self.assertNotIn("CLAUDECODE=", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT=", env)
        self.assertIn("written by the CEO agent", out.getvalue())


if __name__ == "__main__":
    unittest.main()
