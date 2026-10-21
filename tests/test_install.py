"""Tests for scripts/install.py and scripts/doctor.py.

Pinned behaviour: an answers-file install is COMPLETE and doctor passes | a placeholder path, or a
project whose protected zone was not answered, leaves the install NOT COMPLETE and doctor failing |
the template's example queue items never reach the installed queue (and are cut from an existing one) |
the files the charter refers to are created | an empty protected zone needs an explicit confirmation and
is shown in a box | protected paths become absolute Edit(//...) deny rules for the agent and the CEO
session | interactive answers refuse placeholders | re-running keeps edited files unless --force |
the installed copy runs its own tests and the Quick start dry run | docs/02 and the policy template agree.

Run: python3 -m pytest tests -q   (or: python3 -m unittest discover tests)
Temp directories and a stub `claude` only; no model is called and nothing outside the temp dirs is written.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
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
if [ "$1" = "--help" ]; then
  echo "Usage: claude [options] [command] [prompt]"
  echo "  -p, --print   --output-format <format>   --model <model>   --append-system-prompt <prompt>"
  echo "  -r, --resume [value]   --allowedTools, --allowed-tools <tools...>   --disallowedTools <tools...>"
  echo "  --tools <tools...>   --effort <level>"
  exit 0
fi
cat > /dev/null
printf '%s' '{"result": "{\\"headline\\": \\"quiet day\\", \\"notable\\": [], \\"ceo_view\\": \\"\\", \\"stale\\": []}", "is_error": false}'
"""


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
        self.stub = self.tmp / "bin" / "claude"
        self.stub.parent.mkdir()
        self.stub.write_text(STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, **over) -> dict:
        a = {"company": "Saebyeok Data Lab", "owner": "Mina Seo", "timezone": "Asia/Seoul",
             "report_times": ["08:00", "20:00"], "messenger": "telegram", "claude_cmd": str(self.stub),
             "git_init": False,
             "projects": [{"name": "shop", "description": "web shop", "path": str(self.shop),
                           "protected": ["billing/**", "config/pricing.yaml"]}]}
        a.update(over)
        return a

    def run_install(self, answers: dict, *extra) -> tuple:
        f = self.tmp / "answers.yaml"
        f.write_text(yaml.safe_dump(answers, sort_keys=False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = install.main(["--answers", str(f), "--dest", str(self.dest), *extra])
        return rc, out.getvalue()

    def doctor(self) -> tuple:
        out = io.StringIO()
        rep = doctor.run(self.dest, out=out)
        return rep, out.getvalue()

    def read(self, rel: str) -> str:
        return (self.dest / rel).read_text()


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
                    "scripts/doctor.py", "tests/test_logic_agent.py", "tests/test_report_daemon.py"):
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
        self.assertTrue(list((self.dest / "service").glob("*")) or install.sys.platform not in ("darwin", "linux"))

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

    def test_no_projects_needs_confirmation(self):
        rc, _ = self.run_install(self.answers(projects=[]))
        self.assertEqual(rc, 1)
        rc, out = self.run_install(self.answers(projects=[], no_protected_zone=True), "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn("NO PROTECTED ZONE: no project registered yet", out)


class Queue(Base):
    def test_examples_never_reach_the_installed_queue(self):
        self.run_install(self.answers())
        items = yaml.safe_load(self.read("config/owner_action_queue.yaml"))
        ids = {i["id"] for i in items}
        self.assertFalse(ids & doctor.TEMPLATE_EXAMPLE_IDS)
        self.assertFalse(any(i.get("example") for i in items))
        self.assertTrue(all(i["owner"] == "human" and i["ready"] is True for i in items))  # real setup steps

    def test_examples_are_cut_from_an_existing_queue(self):
        (self.dest / "config").mkdir(parents=True)
        q = self.dest / "config" / "owner_action_queue.yaml"
        own = ("- id: my_item\n  due: '2026-10-30'\n  owner: ceo\n  title: mine\n  status: open\n")
        q.write_text((ROOT / "templates" / "owner_action_queue.yaml").read_text() + "\n" + own)
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        items = yaml.safe_load(q.read_text())
        self.assertEqual([i["id"] for i in items], ["my_item"])
        self.assertTrue(list(q.parent.glob("owner_action_queue.yaml.bak-*")))
        self.assertIn("# Owner action queue", q.read_text())  # comments kept


class Rerun(Base):
    def test_rerun_keeps_edited_files_unless_forced(self):
        self.run_install(self.answers())
        rc, out = self.run_install(self.answers())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("replaced", out)
        (self.dest / "CLAUDE.md").write_text(self.read("CLAUDE.md") + "\nmy own rule\n")
        rc, out = self.run_install(self.answers())
        self.assertIn("kept existing CLAUDE.md", out)
        self.assertIn("my own rule", self.read("CLAUDE.md"))
        rc, out = self.run_install(self.answers(), "--force")
        self.assertNotIn("my own rule", self.read("CLAUDE.md"))
        self.assertTrue(list(self.dest.glob("CLAUDE.md.bak-*")))
        handoff = self.dest / "HANDOFF.md"
        handoff.write_text("# my log\n")
        self.run_install(self.answers(), "--force")
        self.assertEqual(handoff.read_text(), "# my log\n")  # the owner's record is never replaced

    def test_install_into_the_clone_itself(self):
        clone = self.tmp / "clone"
        top = {".git", "state", "service", "config", ".claude", "HANDOFF.md", "CLAUDE.md", "advisory_drafts"}
        shutil.copytree(ROOT, clone, ignore=lambda d, names: [n for n in names if n in ("__pycache__", ".pytest_cache")
                                                              or (Path(d) == ROOT and n in top)])
        f = self.tmp / "a.yaml"
        f.write_text(yaml.safe_dump(self.answers()))
        r = subprocess.run([sys.executable, "scripts/install.py", "--answers", str(f)], cwd=clone,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertEqual((clone / "scripts" / "logic_agent.py").read_text(),
                         (ROOT / "scripts" / "logic_agent.py").read_text())
        self.assertTrue((clone / "CLAUDE.md").is_file())


class Interactive(Base):
    def run_interactive(self, replies: list) -> tuple:
        it = iter(replies)
        asked = []

        def inp(prompt):
            asked.append(prompt)
            return next(it)
        out = io.StringIO()
        with mock.patch.object(install.doctor, "find_claude", return_value=(str(self.stub), None)), \
                contextlib.redirect_stdout(out):
            rc = install.main(["--dest", str(self.dest), "--lang", "en"], inp=inp)
        return rc, out.getvalue(), asked

    def test_questions_refuse_placeholders_and_fill_everything(self):
        rc, out, asked = self.run_interactive([
            "Test Co", "", "Owner One", "",          # company, one line, owner, language
            "Mars/Base", "UTC",                      # invalid time zone is asked again
            "8:00", "none", "cto, cfo", "",          # report times, messenger, roles, instruction
            "",                                      # add a project? (default yes)
            "shop", "web shop",
            "//ABSOLUTE/PATH/TO/shop", str(self.shop),   # placeholder folder refused
            "", "",                                  # mode 1 (headless), handoff default
            "//ABSOLUTE/PATH/TO/shop/live/**",       # placeholder protected path refused
            "billing", "",                           # one protected path, then done
            "n",                                     # no more projects
            "",                                      # live services
            "revenue = ~/shop/revenue.csv", "",      # one data source
        ])
        self.assertEqual(rc, 0, out)
        self.assertIn("template placeholder", out)
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

    def test_empty_protected_zone_requires_typing_none(self):
        rc, out, asked = self.run_interactive([
            "Test Co", "", "Owner One", "", "UTC", "", "telegram", "", "",
            "", "notes", "notes app", str(self.notes), "2", "",
            "",            # no protected path
            "",            # Enter = do not confirm: asked for paths again
            "",            # still none
            "none",        # confirmed
            "n", "", "",
        ])
        self.assertEqual(rc, 0, out)
        self.assertEqual(sum("type none to confirm" in p for p in asked), 2)
        self.assertIn("NO PROTECTED ZONE: notes", out)


@unittest.skipUnless((ROOT / "docs").is_dir(), "template repository only")
class Docs(unittest.TestCase):
    def test_policy_template_matches_docs_02(self):
        doc = (ROOT / "docs" / "02-autonomy-policy.md").read_text()
        block = doc.split("```markdown\n", 1)[1].split("\n```\n", 1)[0] + "\n"
        self.assertEqual(block, (ROOT / "templates" / "autonomy_policy.md").read_text())


if __name__ == "__main__":
    unittest.main()
