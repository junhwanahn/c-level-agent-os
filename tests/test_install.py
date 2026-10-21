"""Tests for scripts/install.py and scripts/doctor.py.

Pinned behaviour: an answers-file install is COMPLETE and doctor passes | a placeholder path or a project
whose protected zone was not answered leaves the install NOT COMPLETE | an answers file with a protected path
that does not exist and is not under allow_missing writes no file at all, and doctor fails on such a path, so
re-run keep, --doctor and doctor.py agree | without projects there is no box and no confirmation |
live services become launchctl/systemctl deny rules | Enter alone means delivery none | the service commands
are printed whatever the STATUS | Telegram placeholders in the registered copy fail doctor and are never sent |
`none` counts only when it is written | the template's example queue items never reach the
installed queue | the files the charter refers to are created | protected paths become absolute Edit(//...)
deny rules for the agent and the CEO session | every question has a default or an Enter that leaves it
(an all-Enter run ends), the first screen states the question count, and errors use the question language |
a re-run asks keep / overwrite / stop; keep changes nothing; the summary is read back from the files and a
kept file that disagrees with the answers is NOT COMPLETE | --reconfigure delivery changes the config, the
service file and the queue together | --render-service works in a manual layout | doctor fails when claude is
not signed in, and --check-login makes its one model call only after a yes | pasteable commands carry no
inline comment and no <placeholder> | the installed copy runs its own tests | docs/02 and the policy agree.

Run: python3 -m pytest tests -q   (or: python3 -m unittest discover tests)
Temp directories and a stub `claude` only; no model is called and nothing outside the temp dirs is written.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402
import install  # noqa: E402

STUB = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/calls.log"
if [ "$1" = "--help" ]; then
  echo "Usage: claude [options] [command] [prompt]"
  echo "  -p, --print   --output-format <format>   --model <model>   --append-system-prompt <prompt>"
  echo "  -r, --resume [value]   --allowedTools, --allowed-tools <tools...>   --disallowedTools <tools...>"
  echo "  --tools <tools...>   --effort <level>"
  echo "Commands:"
  __HELP_AUTH__
  exit 0
fi
if [ "$1" = "auth" ]; then
  __AUTH__
fi
cat > "$(dirname "$0")/stdin.log"
printf '%s' '{"result": "{\\"headline\\": \\"quiet day\\", \\"notable\\": [], \\"ceo_view\\": \\"\\", \\"stale\\": []}", "is_error": false}'
"""
AUTH_IN = """echo '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty"}'; exit 0"""
AUTH_OUT = """echo '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'; exit 1"""
AUTH_OLD = """echo "error: unknown command 'auth'" >&2; exit 1"""


def write_stub(path: Path, auth: str = AUTH_IN) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = 'echo "  auth   Manage authentication"' if auth != AUTH_OLD else 'echo "  doctor   Check health"'
    path.write_text(STUB.replace("__AUTH__", auth).replace("__HELP_AUTH__", listed))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@unittest.skipUnless((ROOT / "templates" / "CLAUDE.md.ceo").is_file(), "template repository only")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.dest = self.tmp / "ceo"
        self.shop = self.tmp / "projects" / "shop"
        self.notes = self.tmp / "projects" / "notes"
        for d in (self.shop / "billing", self.shop / "config", self.notes):
            d.mkdir(parents=True)
        (self.shop / "config" / "pricing.yaml").write_text("price: 1\n")
        (self.shop / "HANDOFF.md").write_text("# handoff\n")
        (self.notes / "HANDOFF.md").write_text("# handoff\n")
        self.stub = write_stub(self.tmp / "bin" / "claude")
        self.home = self.tmp / "home"  # a fake HOME: the registered service copy is looked up there
        self.home.mkdir()
        # keep the user site-packages (PyYAML installed with --user) visible to child pythons under the fake HOME
        p = mock.patch.dict(os.environ, {"HOME": str(self.home), "PYTHONUSERBASE": site.getuserbase()})
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, **over) -> dict:
        a = {"company": "Saebyeok Data Lab", "owner": "Mina Seo", "timezone": "Asia/Seoul",
             "report_times": ["08:00", "20:00"], "messenger": "telegram", "claude_cmd": str(self.stub),
             "git_init": False, "service": "launchd",
             "projects": [{"name": "shop", "description": "web shop", "path": str(self.shop),
                           "protected": ["billing/**", "config/pricing.yaml"]}]}
        a.update(over)
        return a

    def run_main(self, args: list, inp=None) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = install.main(args + ["--dest", str(self.dest), "--lang", "en"],
                              **({"inp": inp} if inp else {}))
        return rc, out.getvalue()

    def run_install(self, answers: dict, *extra) -> tuple:
        f = self.tmp / "answers.yaml"
        f.write_text(yaml.safe_dump(answers, sort_keys=False))
        return self.run_main(["--answers", str(f), *extra])

    def doctor(self, **kw) -> tuple:
        out = io.StringIO()
        rep = doctor.run(self.dest, out=out, **kw)
        return rep, out.getvalue()

    def read(self, rel: str) -> str:
        return (self.dest / rel).read_text()

    def plist(self) -> Path:
        return self.dest / "service" / "com.saebyeok-data-lab.report-daemon.plist"

    def queue_ids(self, status=None) -> list:
        items = yaml.safe_load(self.read("config/owner_action_queue.yaml")) or []
        return [i["id"] for i in items if status is None or i.get("status") == status]


def interactive(test: Base, replies: list, lang: str = "en", limit: int = 200) -> tuple:
    """Run the interactive installer with scripted replies ('' = Enter). Fails instead of looping forever."""
    it = iter(replies)
    asked = []

    def inp(prompt):
        asked.append(prompt)
        if len(asked) > limit:
            raise AssertionError(f"more than {limit} prompts: the questions loop. Last: {asked[-3:]}")
        return next(it, "")
    out = io.StringIO()
    with mock.patch.object(install.doctor, "find_claude", return_value=(str(test.stub), None)), \
            contextlib.redirect_stdout(out):
        rc = install.main(["--dest", str(test.dest), "--lang", lang], inp=inp)
    return rc, out.getvalue(), asked


class Complete(Base):
    def test_answers_install_is_complete_and_doctor_passes(self):
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        self.assertIn("STATUS: COMPLETE", out)
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        charter = self.read("CLAUDE.md")
        self.assertNotIn("{{", charter)
        self.assertNotIn("<!--", charter)
        self.assertIn("# Saebyeok Data Lab CEO Agent", charter)
        self.assertIn(f"- `{self.shop}/billing/**`", charter)

    def test_protected_paths_become_absolute_deny_rules_for_agent_and_ceo(self):
        self.run_install(self.answers())
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))
        shop = reg["agents"]["shop"]
        expected = [f"Edit(/{self.shop}/billing/**)", f"Edit(/{self.shop}/config/pricing.yaml)"]
        self.assertEqual(shop["denied_tools"], expected)
        self.assertTrue(all(r.startswith("Edit(//") for r in expected))
        self.assertEqual(reg["permissions_status"], "proposed")
        deny = json.loads(self.read(".claude/settings.json"))["permissions"]["deny"]
        self.assertEqual(deny, expected)
        self.assertNotIn("Write(", self.read("config/logic_agents.yaml").split("\nagents:", 1)[1])

    def test_referenced_files_are_created(self):
        self.run_install(self.answers(owner_instruction="Do everything but the last click."))
        for rel in ("config/autonomy_policy.md", "config/access_control.yaml", "HANDOFF.md", "config/report_prompt.md",
                    "config/report_daemon.yaml", "scripts/logic_agent.py", "scripts/report_daemon.py",
                    "scripts/doctor.py", "scripts/install.py", "templates/service/com.example.report-daemon.plist",
                    "tests/test_logic_agent.py", "tests/test_report_daemon.py"):
            self.assertTrue((self.dest / rel).is_file(), rel)
        self.assertTrue((self.dest / "advisory_drafts" / "reviews").is_dir())
        self.assertIn('"Do everything but the last click."', self.read("config/autonomy_policy.md"))
        self.assertIn('"Do everything but the last click."', self.read("HANDOFF.md"))
        access = yaml.safe_load(self.read("config/access_control.yaml"))
        self.assertEqual(access["projects"]["shop"]["path"], str(self.shop))
        self.assertIn("state/", self.read(".gitignore"))
        cfg = yaml.safe_load(self.read("config/report_daemon.yaml"))
        self.assertEqual((cfg["timezone"], cfg["report_times"], cfg["claude_cmd"]),
                         ("Asia/Seoul", ["08:00", "20:00"], str(self.stub)))
        self.assertTrue(self.plist().is_file())

    def test_quick_start_dry_run_and_installed_tests_work(self):
        self.run_install(self.answers())
        r = subprocess.run([sys.executable, "scripts/report_daemon.py", "--once", "--dry-run"], cwd=self.dest,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("> Owner actions", r.stdout)
        self.assertIn("> Summary: quiet day", r.stdout)
        self.assertFalse((self.dest / "state" / "report_daemon" / "reports.jsonl").exists())
        r = subprocess.run([sys.executable, "scripts/logic_agent.py", "shop", "-p", "status", "--dry-run"],
                           cwd=self.dest, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("WARNING", r.stdout)
        if importlib.util.find_spec("pytest"):
            r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                               cwd=self.dest, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])


class Incomplete(Base):
    def test_placeholder_path_left_means_not_complete_and_doctor_fails(self):
        a = self.answers()
        a["projects"][0]["protected"] = ["//ABSOLUTE/PATH/TO/shop/live/**"]
        rc, out = self.run_install(a)
        self.assertEqual(rc, 1)
        self.assertIn("STATUS: NOT COMPLETE", out)
        self.assertNotIn("STATUS: COMPLETE", out)
        self.assertNotIn("\nnext:", out)  # no next steps while it is not complete ...
        self.assertIn("You can register the report service now:", out)  # ... except the service lines (B-2)
        rep, _ = self.doctor()
        self.assertTrue(any("ABSOLUTE/PATH" in m or "placeholder" in m for m in rep.failures), rep.failures)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(doctor.main(["--root", str(self.dest)]), 1)

    def test_unanswered_protected_zone_keeps_the_placeholder(self):
        a = self.answers()
        del a["projects"][0]["protected"]
        rc, out = self.run_install(a)
        self.assertEqual(rc, 1)
        self.assertIn("protected zone not answered", out)
        self.assertIn("ABSOLUTE/PATH", self.read("config/logic_agents.yaml"))
        rep, _ = self.doctor()
        self.assertTrue(rep.failures)

    def test_doctor_fails_on_a_manual_copy_of_the_templates(self):
        (self.dest / "config").mkdir(parents=True)
        shutil.copy(ROOT / "templates" / "logic_agents.yaml", self.dest / "config" / "logic_agents.yaml")
        shutil.copy(ROOT / "templates" / "owner_action_queue.yaml", self.dest / "config" / "owner_action_queue.yaml")
        shutil.copy(ROOT / "templates" / "CLAUDE.md.ceo", self.dest / "CLAUDE.md")
        rep, _ = self.doctor()
        text = "\n".join(rep.failures)
        self.assertIn("placeholder", text)
        self.assertIn("template example item", text)
        self.assertIn("missing: HANDOFF.md", text)


class AnswersGuard(Base):
    """--answers is the path the agent skill uses: it must refuse what the questions refuse."""

    def test_missing_protected_path_writes_no_file(self):
        a = self.answers()
        a["projects"][0]["protected"] = ["billing/**", "paymnets"]  # a typo of a real folder
        rc, out = self.run_install(a)
        self.assertEqual(rc, 2, out)
        self.assertIn("STOPPED: no file was written", out)
        self.assertIn(f"project shop: {self.shop}/paymnets", out)
        self.assertIn("allow_missing", out)
        self.assertIn("STATUS: NOT COMPLETE", out)
        self.assertNotIn("== doctor ==", out)
        self.assertFalse(self.dest.exists())  # not even the folder

    def test_missing_protected_path_leaves_an_existing_install_untouched(self):
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        before = {p: p.read_bytes() for p in self.dest.rglob("*") if p.is_file()}
        a = self.answers()
        a["projects"][0]["protected"] = ["paymnets"]
        rc, out = self.run_install(a, "--force")
        self.assertEqual(rc, 2, out)
        self.assertEqual({p: p.read_bytes() for p in self.dest.rglob("*") if p.is_file()}, before)

    def test_allow_missing_accepts_a_path_that_comes_later(self):
        a = self.answers()
        a["projects"][0]["protected"] = ["billing/**", "payouts"]
        a["projects"][0]["allow_missing"] = ["payouts"]
        rc, out = self.run_install(a)
        self.assertEqual(rc, 0, out)
        self.assertIn("payouts  (does not exist yet; you said it comes later)", out)
        self.assertEqual(yaml.safe_load(self.read("config/install_answers.yaml"))["projects"][0]["allow_missing"],
                         ["payouts"])
        shop = yaml.safe_load(self.read("config/logic_agents.yaml"))["agents"]["shop"]
        self.assertEqual(shop["allow_missing"], [f"Edit(/{self.shop}/payouts)", f"Edit(/{self.shop}/payouts/**)"])
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        self.assertTrue(any(lv == "warn" and "comes later" in m for lv, m in rep.rows), text)

    def test_a_typo_in_the_files_fails_every_entrance_alike(self):
        """C3/C4: a registry that names a missing path without allow_missing (an older install, a hand edit)."""
        a = self.answers()
        a["projects"][0]["protected"] = ["billing/**", "paymnets"]
        a["projects"][0]["allow_missing"] = ["paymnets"]
        self.assertEqual(self.run_install(a)[0], 0)
        reg = self.dest / "config" / "logic_agents.yaml"
        data = yaml.safe_load(reg.read_text())
        del data["agents"]["shop"]["allow_missing"]
        reg.write_text(yaml.safe_dump(data, sort_keys=False))
        rep, text = self.doctor()
        self.assertTrue(any("protected path does not exist" in m and "paymnets" in m for m in rep.failures), text)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(doctor.main(["--root", str(self.dest)]), 1)
        rc, _ = self.run_main(["--doctor"])
        self.assertEqual(rc, 1)
        rc, out, _ = interactive(self, ["1"])  # re-run, 1 = keep
        self.assertEqual(rc, 1, out)
        self.assertIn("STATUS: NOT COMPLETE", out)
        self.assertIn("paymnets  (DOES NOT EXIST: a typo? fix it)", out)

    def test_none_counts_only_when_written(self):
        for value in (None, [], ""):
            a = self.answers()
            a["projects"][0]["protected"] = value
            rc, out = self.run_install(a, "--force")
            self.assertEqual(rc, 1, f"protected={value!r} must not complete")
            self.assertIn("protected zone not answered", out)
        a = self.answers()
        a["projects"][0]["protected"] = "none"
        rc, out = self.run_install(a, "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("NO PROTECTED ZONE: shop", out)
        self.assertEqual(yaml.safe_load(self.read("config/logic_agents.yaml"))["agents"]["shop"]["protected_zone"],
                         "none")


class ProtectedZone(Base):
    def test_empty_zone_needs_explicit_confirmation_and_is_boxed(self):
        a = self.answers()
        a["projects"].append({"name": "notes", "path": str(self.notes), "protected": []})
        rc, out = self.run_install(a)
        self.assertEqual(rc, 1, "empty without confirmation must not complete")
        a["projects"][1]["no_protected_zone"] = True
        rc, out = self.run_install(a, "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("NO PROTECTED ZONE: notes", out)
        self.assertIn("!!!!", out)
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))
        self.assertEqual(reg["agents"]["notes"]["protected_zone"], "none")
        rep, text = self.doctor()
        self.assertTrue(any(lv == "warn" and "NO PROTECTED ZONE" in m for lv, m in rep.rows), text)

    def test_no_projects_is_one_quiet_line(self):
        """Owner decision (2026-10-21): without projects there is nothing to protect yet; no box, no warning."""
        rc, out = self.run_install(self.answers(projects=[]))  # no confirmation key needed
        self.assertEqual(rc, 0, out)
        self.assertNotIn("NO PROTECTED ZONE", out)
        self.assertNotIn("!!!!", out)
        self.assertIn("projects: none yet. No project, so the protected-zone setup is skipped. When you add a "
                      "project later, the installer asks then.", out)
        self.assertIn("none yet: no project is registered", self.read("CLAUDE.md"))
        self.assertNotIn("owner confirmed", self.read("CLAUDE.md"))
        rep, text = self.doctor()
        self.assertTrue(any(lv == "info" and "no project agents registered" in m for lv, m in rep.rows), text)
        self.assertFalse(any(lv == "warn" and "no project agents" in m for lv, m in rep.rows), text)
        self.assertIn("ℹ no project agents registered", text)

    def test_box_keeps_the_english_token_in_korean(self):
        a = self.answers()
        a["projects"][0]["protected"] = "none"
        f = self.tmp / "a.yaml"
        f.write_text(yaml.safe_dump(a))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.main(["--answers", str(f), "--dest", str(self.dest), "--lang", "ko"])
        self.assertIn("NO PROTECTED ZONE: shop (보호 구역 없음)", out.getvalue())
        self.assertIn("STATUS: COMPLETE (설치 완료)", out.getvalue())

    def test_no_projects_in_korean_is_the_quiet_line(self):
        f = self.tmp / "a.yaml"
        f.write_text(yaml.safe_dump(self.answers(projects=[])))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.main(["--answers", str(f), "--dest", str(self.dest), "--lang", "ko"])
        self.assertIn("프로젝트가 없어 보호 구역 설정은 건너뜁니다. 나중에 프로젝트를 추가하면 그때 묻습니다.", out.getvalue())
        self.assertNotIn("보호 구역 없음", out.getvalue())


class Queue(Base):
    def test_examples_never_reach_the_installed_queue(self):
        self.run_install(self.answers())
        items = yaml.safe_load(self.read("config/owner_action_queue.yaml"))
        ids = {i["id"] for i in items}
        self.assertFalse(ids & doctor.TEMPLATE_EXAMPLE_IDS)
        self.assertFalse(any(i.get("example") for i in items))
        self.assertTrue(all(i["owner"] == "human" and i["ready"] is True for i in items))  # real setup steps

    def test_examples_are_cut_from_an_existing_queue_and_setup_steps_added(self):
        (self.dest / "config").mkdir(parents=True)
        q = self.dest / "config" / "owner_action_queue.yaml"
        own = ("- id: my_item\n  due: '2026-10-30'\n  owner: ceo\n  title: mine\n  status: open\n")
        q.write_text((ROOT / "templates" / "owner_action_queue.yaml").read_text() + "\n" + own)
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        ids = self.queue_ids()
        self.assertEqual(ids[0], "my_item")
        self.assertFalse(set(ids) & doctor.TEMPLATE_EXAMPLE_IDS)
        self.assertTrue(any(i.startswith("register_report_service_") for i in ids), ids)
        self.assertTrue(list(q.parent.glob("owner_action_queue.yaml.bak-*")))
        self.assertIn("# Owner action queue", q.read_text())  # comments kept
        rc, out = self.run_install(self.answers())  # a re-run does not add the same steps twice
        self.assertEqual(self.queue_ids(), ids)


class Questions(Base):
    """B1: every question has a default or an Enter that leaves it."""

    def test_all_enter_ends_with_defaults_and_the_stated_question_count(self):
        rc, out, asked = interactive(self, [])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(asked), len(install.BASE_QUESTIONS), asked)
        n, m = len(install.BASE_QUESTIONS), -(-len(install.BASE_QUESTIONS) * install.SECONDS_PER_QUESTION // 60)
        self.assertIn(f"{n} questions ({len(install.PROJECT_QUESTIONS)} more for each project folder you add), "
                      f"about {m} minutes", out)
        self.assertIn("Ctrl+C", out)
        self.assertTrue(any("[y/N]" in p for p in asked if "project folder" in p))  # adding a project defaults to no
        self.assertIn("No project, so the protected-zone setup is skipped.", out)
        self.assertNotIn("NO PROTECTED ZONE", out)
        self.assertNotIn("!!!!", out)
        # K6 / owner decision: Enter alone = delivery none, so the first report arrives as a file without a bot
        self.assertTrue(any(p.startswith("How should reports reach you?") and p.rstrip().endswith("[none]:")
                            for p in asked), asked)
        self.assertEqual(yaml.safe_load(self.read("config/report_daemon.yaml"))["delivery"], "none")
        self.assertFalse([i for i in self.queue_ids() if i.startswith("setup_telegram_")])
        self.assertIn("via none (saved as files under state/report_daemon/reports/, not sent)", out)

    def test_empty_folder_leaves_the_project_and_name_defaults_to_the_folder(self):
        rc, out, asked = interactive(self, [
            "", "", "", "", "", "", "", "", "",      # company .. owner instruction: defaults
            "y", "",                                 # add a project, then Enter on its folder = back
            "y", str(self.shop),                     # again, with a folder
            "", "", "", "",                          # name = folder name, no description, mode 1, handoff
            "billing", "",                           # one protected path
            "", "", "",                              # no more projects, no live services, no data source
        ])
        self.assertEqual(rc, 0, out)
        self.assertIn("No project added.", out)
        self.assertTrue(any(p.strip().startswith("Short name") and p.rstrip().endswith("[shop]:") for p in asked), asked)
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))
        self.assertEqual(list(reg["agents"]), ["shop"])

    def test_protected_zone_question_enter_leaves_none_confirms_a_path_adds(self):
        rc, out, asked = interactive(self, [
            "", "", "", "", "", "", "", "", "",
            "y", str(self.notes), "", "", "", "",
            "", "",                                  # no protected path, Enter at the question = not added
            "y", str(self.notes), "", "", "", "",
            "", "none",                              # explicit none
            "y", str(self.shop), "", "", "", "",
            "", "billing", "",                       # a path typed at that question counts
            "", "", "",
        ])
        self.assertEqual(rc, 0, out)
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))["agents"]
        self.assertEqual(sorted(reg), ["notes", "shop"])
        self.assertEqual(reg["notes"]["protected_zone"], "none")
        self.assertEqual(reg["shop"]["denied_tools"], [f"Edit(/{self.shop}/billing/**)"])
        self.assertIn("NO PROTECTED ZONE: notes", out)

    def test_refuses_placeholders_and_fills_everything(self):
        rc, out, asked = interactive(self, [
            "Test Co", "", "Owner One", "",          # company, one line, owner, language
            "Mars/Base", "UTC",                      # invalid time zone is asked again
            "8:00", "none", "cto, cfo", "",          # report times, messenger, roles, instruction
            "y",                                     # add a project
            "//ABSOLUTE/PATH/TO/shop", str(self.shop) + " ",   # placeholder refused; dragged path (trailing space)
            "", "web shop", "", "",                  # name = folder, description, mode 1, handoff default
            "//ABSOLUTE/PATH/TO/shop/live/**",       # placeholder protected path refused
            "billing", "",                           # one protected path, then done
            "n",                                     # no more projects
            "",                                      # live services
            "revenue = ~/shop/revenue.csv", "",      # one data source
        ])
        self.assertEqual(rc, 0, out)
        self.assertIn("template placeholder", out)
        self.assertIn("(Ctrl+C to stop)", out)
        charter = self.read("CLAUDE.md")
        self.assertIn("# Test Co CEO Agent", charter)
        self.assertIn("- revenue: `~/shop/revenue.csv`", charter)
        self.assertIn("| CTO |", charter)
        self.assertNotIn("| CRO |", charter)
        self.assertEqual(sorted(p.name for p in (self.dest / ".claude" / "agents").glob("*.md")), ["cfo.md", "cto.md"])
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))
        self.assertEqual(reg["agents"]["shop"]["denied_tools"], [f"Edit(/{self.shop}/billing/**)"])
        cfg = yaml.safe_load(self.read("config/report_daemon.yaml"))
        self.assertEqual((cfg["timezone"], cfg["report_times"], cfg["delivery"]), ("UTC", ["08:00"], "none"))

    def test_missing_protected_path_defaults_to_no(self):
        rc, out, asked = interactive(self, [
            "", "", "", "", "", "", "", "", "",
            "y", str(self.shop), "", "", "", "",
            "paymnets", "",                          # typo: asked, Enter = no
            "billing", "",
            "", "", "",
        ])
        self.assertEqual(rc, 0, out)
        self.assertTrue(any("Not found" in p and "[y/N]" in p for p in asked))
        self.assertEqual(yaml.safe_load(self.read("config/logic_agents.yaml"))["agents"]["shop"]["denied_tools"],
                         [f"Edit(/{self.shop}/billing/**)"])

    def test_a_confirmed_missing_path_is_recorded_and_doctor_only_warns(self):
        rc, out, asked = interactive(self, [
            "", "", "", "", "", "", "", "", "",
            "y", str(self.shop), "", "", "", "",
            "payouts", "y", "",                      # not there yet: the owner confirms it comes later
            "", "", "",
        ])
        self.assertEqual(rc, 0, out)
        shop = yaml.safe_load(self.read("config/logic_agents.yaml"))["agents"]["shop"]
        self.assertEqual(shop["allow_missing"], [f"Edit(/{self.shop}/payouts)", f"Edit(/{self.shop}/payouts/**)"])
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)

    def test_errors_follow_the_question_language(self):
        rc, out, asked = interactive(self, ["", "", "", "", "KST", ""], lang="ko")
        self.assertEqual(rc, 0, out)
        self.assertIn("'KST' 는 IANA 타임존 이름이 아니다", out)
        self.assertIn("(멈추려면 Ctrl+C)", out)
        self.assertIn("질문 12개", out)

    def test_ctrl_c_writes_nothing(self):
        calls = []

        def inp(prompt):
            calls.append(prompt)
            if len(calls) == 3:
                raise KeyboardInterrupt
            return ""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = install.main(["--dest", str(self.dest), "--lang", "en"], inp=inp)
        self.assertEqual(rc, 130)
        self.assertIn("No installation file was written", out.getvalue())
        self.assertFalse((self.dest / "CLAUDE.md").exists())


class Reinstall(Base):
    """B2: a re-run never claims what the files do not say."""

    def install_none(self):
        rc, out = self.run_install(self.answers(messenger="none"))
        self.assertEqual(rc, 0, out)

    def snapshot(self) -> dict:
        return {p: p.read_bytes() for p in self.dest.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_keep_changes_nothing_and_shows_the_files(self):
        self.install_none()
        before = self.snapshot()
        rc, out, asked = interactive(self, ["1"])
        self.assertEqual(rc, 0, out)
        self.assertIn("already has an installation", asked[0])
        self.assertEqual(self.snapshot(), before)
        self.assertIn("nothing changed", out)
        self.assertIn("via none", out)
        self.assertIn("protected  shop:", out)

    def test_stop_changes_nothing(self):
        self.install_none()
        before = self.snapshot()
        rc, out, _ = interactive(self, ["3"])
        self.assertEqual(rc, 0)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.snapshot(), before)

    def test_overwrite_switches_delivery_config_service_and_queue_together(self):
        self.install_none()
        self.assertNotIn("REPORT_TELEGRAM_TOKEN", self.plist().read_text())
        rc, out, asked = interactive(self, [
            "2",                                     # overwrite
            "", "", "", "", "", "", "telegram",      # last answers are the defaults; change the messenger
            "", "",                                  # roles, instruction
            "", "",                                  # keep the last projects, no more
            "", "",                                  # live services, data sources
        ])
        self.assertEqual(rc, 0, out)
        self.assertEqual(yaml.safe_load(self.read("config/report_daemon.yaml"))["delivery"], "telegram")
        self.assertIn("REPORT_TELEGRAM_TOKEN", self.plist().read_text())
        self.assertTrue(any(i.startswith("setup_telegram_") for i in self.queue_ids("open")))
        self.assertTrue(list((self.dest / "config").glob("report_daemon.yaml.bak-*")))
        self.assertIn("via telegram", out)
        items = yaml.safe_load(self.read("config/owner_action_queue.yaml"))
        open_reg = [i for i in items if i["id"].startswith("register_report_service_") and i["status"] == "open"]
        self.assertEqual(len(open_reg), 1)  # the stale one (no Telegram step) is dropped, the new one has it
        self.assertTrue(any(ln.startswith("open -e") for ln in open_reg[0]["prep"]), open_reg[0]["prep"])

    def test_answers_rerun_with_a_kept_differing_file_is_not_complete(self):
        self.install_none()
        rc, out = self.run_install(self.answers(messenger="telegram"))
        self.assertEqual(rc, 1, out)
        self.assertIn("STATUS: NOT COMPLETE", out)
        self.assertIn("config/report_daemon.yaml says delivery: none; you answered telegram", out)
        self.assertIn("via none", out)  # the summary shows the file, not the answer
        self.assertNotIn("via telegram", out)
        self.assertNotIn("\nnext:", out)
        rc, out = self.run_install(self.answers(messenger="telegram"), "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("via telegram", out)

    def test_old_template_copies_point_to_force_first(self):
        (self.dest / "config").mkdir(parents=True)
        for name in ("logic_agents.yaml", "access_control.yaml", "report_daemon.yaml"):
            shutil.copy(ROOT / "templates" / name, self.dest / "config" / name)
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 1)
        status = out.split("STATUS: NOT COMPLETE", 1)[1].splitlines()
        self.assertIn("old template copies", status[1])
        self.assertIn("--force", status[1])
        self.assertIn("projects: strategy_a", out)  # read from the kept registry, not from the answers
        self.assertNotIn("projects: shop", out)


class Reconfigure(Base):
    """B3: --reconfigure delivery changes the config, the service file and the queue together."""

    def test_delivery_none_to_telegram_and_back_keeps_other_edits(self):
        rc, out = self.run_install(self.answers(messenger="none"))
        self.assertEqual(rc, 0, out)
        cfg = self.dest / "config" / "report_daemon.yaml"
        cfg.write_text(cfg.read_text().replace("stale_hours: 26", "stale_hours: 30"))
        rc, out = self.run_main(["--reconfigure", "delivery", "--to", "telegram"])
        self.assertEqual(rc, 0, out)
        data = yaml.safe_load(cfg.read_text())
        self.assertEqual((data["delivery"], data["stale_hours"]), ("telegram", 30))
        self.assertIn("REPORT_TELEGRAM_TOKEN", self.plist().read_text())
        tg = [i for i in self.queue_ids("open") if i.startswith("setup_telegram_")]
        self.assertEqual(len(tg), 1)
        self.assertEqual(yaml.safe_load(self.read("config/install_answers.yaml"))["messenger"], "telegram")
        self.assertIn("delivery: none -> telegram", out)
        self.assertIn("open -e ~/Library/LaunchAgents/com.saebyeok-data-lab.report-daemon.plist", out)
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        rc, out = self.run_main(["--reconfigure", "delivery", "--to", "none"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(yaml.safe_load(cfg.read_text())["delivery"], "none")
        self.assertNotIn("REPORT_TELEGRAM_TOKEN", self.plist().read_text())
        self.assertIn(tg[0], self.queue_ids("dropped"))
        self.assertIn("saved as files under", out)

    def test_delivery_asks_when_interactive(self):
        self.run_install(self.answers(messenger="none"))
        rc, out = self.run_main(["--reconfigure", "delivery"], inp=lambda prompt: "telegram")
        self.assertEqual(rc, 0, out)
        self.assertEqual(yaml.safe_load(self.read("config/report_daemon.yaml"))["delivery"], "telegram")

    def test_registered_copy_without_telegram_fails_until_re_registered(self):
        self.run_install(self.answers(messenger="none"))
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        shutil.copy(self.plist(), agents / self.plist().name)  # registered while delivery was none
        rc, out = self.run_main(["--reconfigure", "delivery", "--to", "telegram"])
        self.assertEqual(rc, 1, out)
        self.assertIn("has no Telegram variables, so no report is sent", out)
        self.assertIn("launchctl bootout gui/$(id -u)/com.saebyeok-data-lab.report-daemon", out)
        self.assertTrue(any(i.startswith("reregister_report_service_") for i in self.queue_ids("open")))
        filled = self.plist().read_text().replace("REPLACE_WITH_BOT_TOKEN", "123:abc").replace(
            "REPLACE_WITH_CHAT_ID", "42")
        (agents / self.plist().name).write_text(filled)  # the owner re-registered and filled it
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        self.assertTrue(any(lv == "ok" and "service registered" in m for lv, m in rep.rows), text)

    def test_schedule_from_an_answers_file(self):
        self.run_install(self.answers())
        f = self.tmp / "s.yaml"
        f.write_text(yaml.safe_dump({"timezone": "Europe/Berlin", "report_times": ["7:30"]}))
        rc, out = self.run_main(["--reconfigure", "schedule", "--answers", str(f)])
        self.assertEqual(rc, 0, out)
        cfg = yaml.safe_load(self.read("config/report_daemon.yaml"))
        self.assertEqual((cfg["timezone"], cfg["report_times"]), ("Europe/Berlin", ["07:30"]))
        self.assertIn("reports:  07:30 Europe/Berlin", out)

    def test_needs_an_installation_and_a_value(self):
        rc, _ = self.run_main(["--reconfigure", "delivery", "--to", "telegram"])
        self.assertEqual(rc, 2)
        self.run_install(self.answers())
        rc, _ = self.run_main(["--reconfigure", "delivery", "--to", "slack"])
        self.assertEqual(rc, 2)


class RenderService(Base):
    def test_render_service_in_a_manual_layout(self):
        man = self.tmp / "manual-co"
        (man / "scripts").mkdir(parents=True)
        (man / "config").mkdir()
        for s in ("install.py", "doctor.py", "report_daemon.py", "logic_agent.py"):
            shutil.copy(ROOT / "scripts" / s, man / "scripts" / s)
        shutil.copytree(ROOT / "templates", man / "templates")
        cfg = (ROOT / "templates" / "report_daemon.yaml").read_text()
        cfg = install.set_yaml_value(cfg, "claude_cmd", json.dumps(str(self.stub)))
        (man / "config" / "report_daemon.yaml").write_text(install.set_yaml_value(cfg, "delivery", "none"))
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run([sys.executable, "scripts/install.py", "--render-service", "--lang", "en"], cwd=man,
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        if sys.platform == "darwin":
            f = man / "service" / "com.manual-co.report-daemon.plist"
            self.assertIn("plutil -lint ~/Library/LaunchAgents/com.manual-co.report-daemon.plist", r.stdout)
        else:
            f = man / "service" / "report-daemon.service"
        self.assertTrue(f.is_file(), r.stdout)
        self.assertNotIn("ABSOLUTE/PATH", f.read_text())
        self.assertTrue((man / "state" / "report_daemon").is_dir())  # launchd does not create the log folder
        r = subprocess.run([sys.executable, "scripts/install.py", "--render-service", "--lang", "en"], cwd=man,
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertIn("already up to date", r.stdout)

    def test_render_service_rewrites_a_deleted_service_file(self):
        self.run_install(self.answers())
        self.plist().unlink()
        rc, out = self.run_main(["--render-service"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.plist().is_file())
        self.assertIn("REPORT_TELEGRAM_TOKEN", self.plist().read_text())


class Login(Base):
    """doctor checks the sign-in without a model call; --check-login calls once, only after a yes."""

    def test_not_signed_in_is_a_failure_and_the_summary_says_so(self):
        write_stub(self.stub, AUTH_OUT)
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 1, out)
        self.assertIn("✗ claude is installed but NOT signed in", out)
        self.assertIn("sign-in:  NOT signed in to Claude Code: the model part of each report works only after you "
                      "sign in", out)
        self.assertIn("/login", out)
        # B-2: the service lines are printed whatever the STATUS, below one line that says so
        tail = out.split("STATUS: NOT COMPLETE", 1)[1]
        self.assertIn("Fix the items above first. You can register the report service now:", tail)
        self.assertIn("2. register the report service", tail)
        self.assertIn("launchctl bootstrap gui/$(id -u)", tail)
        self.assertNotIn("\nnext:", out)

    def test_signed_in_is_ok_and_no_model_call_is_made(self):
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        self.assertIn("claude is signed in (claude.ai)", out)
        calls = (self.stub.parent / "calls.log").read_text().splitlines()
        self.assertFalse([c for c in calls if c.startswith("-p")], calls)

    def test_old_cli_without_auth_status_is_a_warning(self):
        write_stub(self.stub, AUTH_OLD)
        self.run_install(self.answers())
        rep, text = self.doctor()
        self.assertIsNone(rep.login)
        self.assertTrue(any(lv == "warn" and "--check-login" in m for lv, m in rep.rows), text)

    def test_check_login_does_not_ask_when_not_signed_in(self):
        """B-5: auth status already says no, so a test message would only fail: no question, no model call."""
        write_stub(self.stub, AUTH_OUT)
        self.run_install(self.answers())
        log = self.stub.parent / "calls.log"
        log.write_text("")

        def boom():
            raise AssertionError("asked although not signed in")
        rep, text = self.doctor(login=True, consent=boom)
        self.assertFalse([c for c in log.read_text().splitlines() if c.startswith("-p")])
        self.assertTrue(any(lv == "info" and "--check-login skipped" in m for lv, m in rep.rows), text)
        self.assertEqual(rep.login, False)

    def test_check_login_calls_once_only_after_yes(self):
        self.run_install(self.answers())
        log = self.stub.parent / "calls.log"
        log.write_text("")
        rep, text = self.doctor(login=True, consent=lambda: False)
        self.assertFalse([c for c in log.read_text().splitlines() if c.startswith("-p")])
        self.assertTrue(any("skipped" in m for _, m in rep.rows), text)
        rep, text = self.doctor(login=True, consent=lambda: True)
        model_calls = [c for c in log.read_text().splitlines() if c.startswith("-p")]
        self.assertEqual(len(model_calls), 1, model_calls)
        self.assertIn("--tools", model_calls[0])
        self.assertNotIn(doctor.LOGIN_PROMPT, model_calls[0])  # after the variadic --tools it would be a tool name
        self.assertEqual((self.stub.parent / "stdin.log").read_text(), doctor.LOGIN_PROMPT)
        self.assertTrue(any(lv == "ok" and "answered a test message" in m for lv, m in rep.rows), text)


class ServiceChecks(Base):
    def test_delivery_telegram_with_a_none_service_file_fails(self):
        self.run_install(self.answers(messenger="none"))
        cfg = self.dest / "config" / "report_daemon.yaml"
        cfg.write_text(install.set_yaml_value(cfg.read_text(), "delivery", "telegram"))
        rep, text = self.doctor()
        self.assertTrue(any("has no Telegram variables" in m and "--reconfigure delivery" in m
                            for m in rep.failures), text)

    def test_pasteable_commands_have_no_inline_comment_and_no_angle_placeholder(self):
        for messenger in ("telegram", "none"):
            rc, out = self.run_install(self.answers(messenger=messenger), "--force")
            self.assertEqual(rc, 0, out)
            block = out.split("\nnext:", 1)[1]
            cmds = [ln.strip() for ln in block.splitlines() if ln.startswith("       ")]
            self.assertTrue(cmds, block)
            for ln in cmds:
                self.assertNotIn("#", ln)  # zsh does not read # as a comment when pasted
                self.assertNotRegex(ln, r"<[a-z]+>")
            items = yaml.safe_load(self.read("config/owner_action_queue.yaml"))
            reg = next(i for i in items if i["id"].startswith("register_report_service_"))
            self.assertIn(f"cd {self.dest}", reg["prep"])


class TelegramValues(Base):
    """B-3: doctor reads the Telegram values where the service reads them; placeholders fail and are never sent."""

    def register(self, text=None) -> Path:
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        dst = agents / self.plist().name
        dst.write_text(text if text is not None else self.plist().read_text())
        return dst

    def test_placeholders_in_the_registered_copy_fail(self):
        self.run_install(self.answers())
        self.register()  # registered without replacing REPLACE_WITH_...
        rep, text = self.doctor()
        self.assertTrue(any("still the placeholders" in m and "no report is sent" in m for m in rep.failures), text)
        rc, out, _ = interactive(self, ["1"])  # keep: the same verdict
        self.assertEqual(rc, 1, out)

    def test_real_values_pass_and_no_shell_environment_is_asked_for(self):
        self.run_install(self.answers())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REPORT_TELEGRAM_TOKEN", None)
            rep, text = self.doctor()
        self.assertNotIn("in the environment", text)  # README: the values live in the service definition only
        self.register(self.plist().read_text().replace("REPLACE_WITH_BOT_TOKEN", "123456:" + "a" * 30)
                      .replace("REPLACE_WITH_CHAT_ID", "42"))
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        self.assertTrue(any(lv == "ok" and "Telegram values set in the registered copy" in m for lv, m in rep.rows))
        self.assertNotIn("123456:", text)  # never printed

    def test_systemd_env_file_is_checked(self):
        self.run_install(self.answers(service="systemd"))
        unit_dir = self.home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        shutil.copy(self.dest / "service" / "report-daemon.service", unit_dir / "report-daemon.service")
        rep, text = self.doctor()
        self.assertTrue(any("cannot be read" in m and "env is missing" in m for m in rep.failures), text)
        env = self.home / ".config" / "report-daemon" / "env"
        env.parent.mkdir(parents=True)
        env.write_text("REPORT_TELEGRAM_TOKEN=PASTE_TOKEN\nREPORT_TELEGRAM_CHAT_ID=PASTE_CHAT_ID\n")
        rep, text = self.doctor()
        self.assertTrue(any("still the placeholders" in m for m in rep.failures), text)
        env.write_text("REPORT_TELEGRAM_TOKEN=123456:" + "a" * 30 + "\nREPORT_TELEGRAM_CHAT_ID=42\n")
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)

    def test_summary_says_where_the_report_text_is_kept(self):
        rc, out = self.run_install(self.answers())
        self.assertIn("via telegram (each report is also saved under state/report_daemon/reports/)", out)


class LiveServices(Base):
    """B-4: each live service name becomes launchctl/systemctl deny rules for every agent and the CEO session."""

    def test_names_become_deny_rules_everywhere(self):
        rc, out = self.run_install(self.answers(live_services="com.myshop.worker, newsletter.service"))
        self.assertEqual(rc, 0, out)
        want = ["Bash(launchctl *com.myshop.worker*)", "Bash(systemctl *com.myshop.worker*)",
                "Bash(launchctl *newsletter*)", "Bash(systemctl *newsletter*)"]
        reg = yaml.safe_load(self.read("config/logic_agents.yaml"))
        self.assertTrue(all(r in reg["defaults"]["denied_tools"] for r in want), reg["defaults"]["denied_tools"])
        self.assertIn("Bash(ssh *sudo*)", reg["defaults"]["denied_tools"])  # the template's rules stay
        deny = json.loads(self.read(".claude/settings.json"))["permissions"]["deny"]
        self.assertTrue(all(r in deny for r in want), deny)
        self.assertIn("live:     com.myshop.worker, newsletter", out)
        self.assertIn("com.myshop.worker, newsletter.service (launchctl/systemctl commands naming them are denied",
                      self.read("CLAUDE.md"))
        r = subprocess.run([sys.executable, "scripts/logic_agent.py", "shop", "-p", "status", "--dry-run"],
                           cwd=self.dest, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Bash(launchctl *com.myshop.worker*)", r.stdout)
        rep, text = self.doctor()
        self.assertEqual(rep.failures, [], text)
        self.assertIn("protected paths and live services (.claude/settings.json, 6 rule(s))", text)

    def test_a_list_works_and_a_sentence_is_refused(self):
        rc, out = self.run_install(self.answers(live_services=["shop-api"]))
        self.assertEqual(rc, 0, out)
        self.assertIn("Bash(systemctl *shop-api*)", self.read("config/logic_agents.yaml"))
        rc, out = self.run_install(self.answers(live_services="뉴스레터 발송기"), "--force")
        self.assertEqual(rc, 1, out)
        self.assertIn("is not a service name", out)

    def test_interactive_refuses_a_non_name_and_takes_the_next(self):
        rc, out, asked = interactive(self, ["", "", "", "", "", "", "", "", "", "",
                                            "뉴스레터 발송기", "com.myshop.worker", ""])
        self.assertEqual(rc, 0, out)
        self.assertIn("is not a service name", out)
        self.assertIn("Bash(launchctl *com.myshop.worker*)", self.read("config/logic_agents.yaml"))

    def test_kept_registry_without_the_rules_is_a_difference(self):
        self.run_install(self.answers())
        rc, out = self.run_install(self.answers(live_services="com.myshop.worker"))
        self.assertEqual(rc, 1, out)
        self.assertIn("does not deny the live services you answered", out)


class SmallFixes(Base):
    """B-6, K1: cd line, python path in the plist comment, template file count, queue in the report language."""

    def test_render_service_starts_with_cd_even_in_the_folder(self):
        self.run_install(self.answers(messenger="none"))
        r = subprocess.run([sys.executable, "scripts/install.py", "--render-service", "--lang", "en"],
                           cwd=self.dest, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "HOME": str(self.home)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cmds = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("    ")]
        self.assertEqual(cmds[0], f"cd {self.dest}")

    def test_plist_comment_names_the_same_python_and_parses_as_xml(self):
        import plistlib
        self.run_install(self.answers())
        text = self.plist().read_text()
        self.assertIn(f"check with  {sys.executable} -c", text)
        self.assertIn(f"<string>{sys.executable}</string>", text)
        data = plistlib.loads(text.encode())  # strict XML: no "--" inside a comment
        self.assertEqual(data["ProgramArguments"][0], sys.executable)

    def test_first_install_into_the_clone_counts_template_files(self):
        clone = self.tmp / "clone"
        for d in ("scripts", "templates", "tests"):
            shutil.copytree(ROOT / d, clone / d, ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("requirements.txt", "requirements-dev.txt"):
            shutil.copy(ROOT / f, clone / f)
        a = self.tmp / "a.yaml"
        a.write_text(yaml.safe_dump(self.answers(projects=[])))
        r = subprocess.run([sys.executable, "scripts/install.py", "--answers", str(a), "--lang", "en"], cwd=clone,
                           capture_output=True, text=True, timeout=120, env={**os.environ, "HOME": str(self.home)})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
        self.assertRegex(r.stdout, r"created:  \d+ file\(s\); template files \d+ \(as they are\); unchanged 0;")

    def test_queue_steps_follow_the_report_language(self):
        self.run_install(self.answers(language="Korean"))
        items = {i["id"].rsplit("_", 1)[0]: i for i in yaml.safe_load(self.read("config/owner_action_queue.yaml"))}
        self.assertTrue(items["setup_telegram"]["prep"][0].startswith("텔레그램:"), items["setup_telegram"]["prep"])
        self.assertIn("허용 목록", items["confirm_allowlist"]["prep"][1])

    def test_pytest_line_only_with_verbose(self):
        self.run_install(self.answers())
        rep, text = self.doctor()
        self.assertNotIn("pytest", text)
        rep, text = self.doctor(verbose=True)
        self.assertIn("pytest", text)

    def test_help_shows_the_answers_format(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            install.main(["--help"])
        self.assertIn("ANSWERS (YAML", out.getvalue())
        self.assertIn("config/install_answers.yaml", out.getvalue())
        self.assertIn("allow_missing", out.getvalue())

    def test_default_delivery_is_none(self):
        a = self.answers()
        del a["messenger"]
        rc, out = self.run_install(a)
        self.assertEqual(rc, 0, out)
        self.assertEqual(yaml.safe_load(self.read("config/report_daemon.yaml"))["delivery"], "none")
        self.assertEqual(yaml.safe_load((ROOT / "templates" / "report_daemon.yaml").read_text())["delivery"], "none")


class ReviewFindings(Base):
    """Edge cases found in review: hand-edited files and odd defaults must not break or loop."""

    def test_drop_keeps_the_queue_valid_and_touches_only_the_item_status(self):
        text = ("# mine\n- id: setup_telegram_20261019\n  meta:\n    status: open\n  status: \"open\"\n"
                "- id: next_item\n  status: open  # keep me\n- id: setup_telegram_20261020\n"
                "  status: open  # note\n")
        out, dropped = install.queue_drop_open(text, "setup_telegram_")
        data = yaml.safe_load(out)
        self.assertEqual([i["status"] for i in data], ["dropped", "open", "dropped"])
        self.assertEqual(data[0]["meta"]["status"], "open")
        self.assertIn("status: dropped  # note", out)
        self.assertEqual(dropped, ["setup_telegram_20261019", "setup_telegram_20261020"])

    def test_comment_after_an_example_item_is_kept(self):
        text = ("- id: send_partner_reply_0115\n  example: true\n  status: open\n\n# --- my items below ---\n"
                "- id: mine\n  status: open\n")
        out, removed = install.remove_example_blocks(text)
        self.assertEqual(removed, ["send_partner_reply_0115"])
        self.assertIn("# --- my items below ---", out)
        self.assertEqual([i["id"] for i in yaml.safe_load(out)], ["mine"])

    def test_flow_style_queue_is_left_alone_with_a_note(self):
        (self.dest / "config").mkdir(parents=True)
        q = self.dest / "config" / "owner_action_queue.yaml"
        q.write_text("[{id: a, owner: ceo, title: x, status: open, due: '2026-10-30'}]\n")
        rc, out = self.run_install(self.answers())
        self.assertIn("queue: could not append", out)
        self.assertEqual([i["id"] for i in yaml.safe_load(q.read_text())], ["a"])

    def test_schedule_edits_block_and_multiline_lists_or_refuses(self):
        self.run_install(self.answers())
        cfg = self.dest / "config" / "report_daemon.yaml"
        base = cfg.read_text()
        f = self.tmp / "s.yaml"
        f.write_text(yaml.safe_dump({"report_times": ["09:00"]}))
        for form in ('report_times:\n  - "08:00"\n  - "20:00"\n', 'report_times: [\n  "08:00",\n  "20:00"]\n'):
            cfg.write_text(re.sub(r"(?m)^report_times:.*\n", lambda m: form, base, count=1))
            rc, out = self.run_main(["--reconfigure", "schedule", "--answers", str(f)])
            self.assertEqual(rc, 0, out)
            data = yaml.safe_load(cfg.read_text())
            self.assertEqual(data["report_times"], ["09:00"])
            self.assertEqual(data["poll_seconds"], 60)
        cfg.write_text(base + "broken: [\n")
        before = cfg.read_text()
        rc, _ = self.run_main(["--reconfigure", "schedule", "--answers", str(f)])
        self.assertEqual(rc, 2)
        self.assertEqual(cfg.read_text(), before)  # nothing written

    def test_null_or_empty_protected_entries_are_no_answer(self):
        for value in ([None], [""], ["  "]):
            a = self.answers()
            a["projects"][0]["protected"] = value
            rc, out = self.run_install(a, "--force")
            self.assertEqual(rc, 1, f"protected={value!r}")
            self.assertIn("protected zone not answered", out)

    def test_kept_registry_with_another_path_is_a_difference(self):
        self.run_install(self.answers())
        reg = self.dest / "config" / "logic_agents.yaml"
        reg.write_text(reg.read_text().replace(f'path: "{self.shop}"', f'path: "{self.notes}"', 1))
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 1, out)
        self.assertIn(f"shop points to {self.notes}; you answered {self.shop}", out)

    def test_an_invalid_default_still_lets_enter_through(self):
        self.run_install(self.answers(roles=["cto"]))
        prev = self.dest / "config" / "install_answers.yaml"
        rec = yaml.safe_load(prev.read_text())
        rec.update(timezone="Nope/X", roles=["xx"], messenger="email", report_times=["25:00"])
        prev.write_text(yaml.safe_dump(rec))
        with mock.patch.dict(os.environ, {"TZ": "Mars/Base"}):
            rc, out, asked = interactive(self, ["2"])
        self.assertEqual(rc, 0, out)
        cfg = yaml.safe_load(self.read("config/report_daemon.yaml"))
        self.assertEqual((cfg["timezone"], cfg["report_times"], cfg["delivery"]), ("UTC", ["08:00", "20:00"], "none"))

    def test_same_delivery_again_does_not_churn_the_queue(self):
        self.run_install(self.answers(messenger="none"))
        self.run_main(["--reconfigure", "delivery", "--to", "telegram"])
        ids = self.queue_ids()
        rc, out = self.run_main(["--reconfigure", "delivery", "--to", "telegram"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.queue_ids(), ids)

    def test_old_cli_is_never_asked_auth_status(self):
        write_stub(self.stub, AUTH_OLD)
        self.run_install(self.answers())
        calls = (self.stub.parent / "calls.log").read_text().splitlines()
        self.assertFalse([c for c in calls if c.startswith("auth")], calls)


@unittest.skipUnless((ROOT / "docs").is_dir(), "template repository only")
class Docs(unittest.TestCase):
    def test_policy_template_matches_docs_02(self):
        doc = (ROOT / "docs" / "02-autonomy-policy.md").read_text()
        block = doc.split("```markdown\n", 1)[1].split("\n```\n", 1)[0] + "\n"
        self.assertEqual(block, (ROOT / "templates" / "autonomy_policy.md").read_text())

    def test_readme_shell_blocks_paste_cleanly(self):
        """No inline # comment (an interactive zsh passes it on as arguments) and no <placeholder>."""
        for name in ("README.md", "README.ko.md"):
            text = (ROOT / name).read_text()
            for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
                for ln in block.splitlines():
                    self.assertNotRegex(ln, r"(^|\s)#", f"{name}: {ln}")
                    self.assertNotRegex(ln, r"<[A-Za-z_ ]+>", f"{name}: {ln}")

    def test_readmes_stay_in_one_language(self):
        hangul = re.compile(r"[가-힣]")
        en = (ROOT / "README.md").read_text().replace("[한국어 README](README.ko.md)", "")
        self.assertFalse(hangul.search(en), hangul.findall(en)[:5])
        self.assertIn("--reconfigure delivery", en)
        self.assertIn("--render-service", en)
        self.assertIn("--reconfigure delivery", (ROOT / "README.ko.md").read_text())

    def test_readme_question_count_matches_the_installer(self):
        n, k = len(install.BASE_QUESTIONS), len(install.PROJECT_QUESTIONS)
        m = -(-n * install.SECONDS_PER_QUESTION // 60)
        self.assertIn(f"{n} questions ({k} more for each project folder you add), about {m} minutes",
                      (ROOT / "README.md").read_text())
        self.assertIn(f"질문 {n}개(프로젝트 폴더를 추가하면 하나당 {k}개 더), 약 {m}분", (ROOT / "README.ko.md").read_text())

    def test_readmes_state_what_the_installer_does(self):
        """REPORT3 K1, K6, B-1..B-3, proposal 15: the README promises what the code does, in both languages."""
        en = (ROOT / "README.md").read_text()
        ko = (ROOT / "README.ko.md").read_text()
        for text, phrases in (
            (en, ["These four lines do not yet bring tomorrow's report by themselves",
                  "keeps the default `none`", "`config/install_answers.yaml`",
                  "`--answers` counts only the paths listed under `allow_missing` as checked",
                  "stops before it writes any file", "also when it says\n   `NOT COMPLETE`",
                  "(whatever the STATUS\nline says)", "Every report is also saved as a file under",
                  "Without any\nproject there is nothing to protect yet", "`launchctl` and `systemctl`"]),
            (ko, ["이 네 줄만으로는 내일 아침 보고가 오지 않는다", "기본값 `none`", "`config/install_answers.yaml`",
                  "`--answers` 는 `allow_missing` 에\n적은 경로만 확인한 것으로 본다", "파일을 하나도 쓰지 않고 멈춘다",
                  "`NOT COMPLETE` 여도 출력한다", "(STATUS 줄과 무관하게)", "언제나 `state/report_daemon/reports/`",
                  "프로젝트가 하나도 없으면 아직 보호할 것이 없으므로", "`launchctl`·`systemctl`"])):
            for ph in phrases:
                self.assertIn(ph, text)
        skill = (ROOT / ".agents" / "skills" / "c-level-agent-os" / "SKILL.md").read_text()
        self.assertIn("STOPPED: no file was written", skill)
        self.assertIn("counts only the paths under `allow_missing` as checked", skill)
        self.assertIn("Messenger [none]", skill)

    def test_pip_install_hides_the_upgrade_notice(self):
        out = io.StringIO()
        with mock.patch.object(install, "_has_yaml", return_value=False), contextlib.redirect_stdout(out):
            self.assertFalse(install.ensure_yaml(False, "en", None))
        self.assertIn("--disable-pip-version-check", out.getvalue())


if __name__ == "__main__":
    unittest.main()
