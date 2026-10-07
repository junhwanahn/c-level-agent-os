"""Regression tests for scripts/logic_agent.py.

Pinned behaviour: mode refusal | allowlist only when confirmed, denylist always |
never --permission-mode / --continue | session resume and rotation |
fresh retry when the resumed session is gone | session and call records.

Run: python3 -m pytest tests -q   (or: python3 -m unittest discover tests)
Uses a temp directory and a stub CLI only; the real `claude` is never invoked.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import logic_agent as la  # noqa: E402

STUB = r"""#!/bin/bash
# stub claude: prompt on stdin. With --resume and STUB_FAIL_RESUME=1 -> "session not found".
args="$*"
prompt=$(cat)
sid="sid-fresh"
if [[ "$args" == *"--resume"* ]]; then
  if [[ "$STUB_FAIL_RESUME" == "1" ]]; then echo "No conversation found with session ID" >&2; exit 1; fi
  sid=$(echo "$args" | sed -E 's/.*--resume ([^ ]+).*/\1/')
fi
printf '{"result":"ok:%s","session_id":"%s","total_cost_usd":0.01,"duration_ms":5}' "${prompt//\"/}" "$sid"
"""


def _registry(tmp: Path, status: str = "proposed") -> Path:
    for d in ("proj_a", "proj_b", "proj_c"):
        (tmp / d).mkdir(exist_ok=True)
    reg = {
        "permissions_status": status,
        "defaults": {"model": "opus", "timeout_sec": 30, "rotate_mb": 1,
                     "allowed_tools": ["Bash(git status*)"], "denied_tools": ["Bash(git reset *)"]},
        "agents": {
            "strategy_a": {"path": str(tmp / "proj_a"), "role": "research", "handoff": "H.md",
                           "mode": "headless", "allowed_tools": ["Edit(//x/**)"],
                           "denied_tools": ["Edit(//x/critical/**)"]},
            "daily_desk": {"path": str(tmp / "proj_b"), "role": "daily", "mode": "live"},
            "research_lab": {"path": str(tmp / "proj_c"), "role": "lab", "mode": "in_session"},
        },
    }
    p = tmp / "reg.yaml"
    p.write_text(yaml.safe_dump(reg))
    return p


class ResolveAndCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_mode_refusal_and_force(self):
        reg = la.load_registry(_registry(self.tmp))
        for name in ("daily_desk", "research_lab", "unknown"):
            with self.assertRaises(la.LogicAgentError):
                la.resolve(name, reg)
        self.assertEqual(la.resolve("daily_desk", reg, force_mode=True)["name"], "daily_desk")

    def test_allowed_only_when_confirmed_denied_always(self):
        spec = la.resolve("strategy_a", la.load_registry(_registry(self.tmp, "proposed")))
        self.assertEqual(spec["allowed_tools"], [])
        self.assertEqual(spec["denied_tools"], ["Bash(git reset *)", "Edit(//x/critical/**)"])
        spec_c = la.resolve("strategy_a", la.load_registry(_registry(self.tmp, "confirmed")))
        self.assertEqual(spec_c["allowed_tools"], ["Bash(git status*)", "Edit(//x/**)"])

    def test_command_never_bypasses_permissions(self):
        spec = la.resolve("strategy_a", la.load_registry(_registry(self.tmp)))
        cmd = la.build_cmd(spec, None, "opus", None, claude_cmd="claude")
        for flag in ("--permission-mode", "--continue", "--dangerously-skip-permissions", "--allowedTools", "--effort"):
            self.assertNotIn(flag, cmd)
        self.assertIn("--disallowedTools", cmd)
        cmd2 = la.build_cmd(spec, "abc", "opus", "high", extra_allowed=["Bash(python3 *)"], claude_cmd="claude")
        self.assertEqual(cmd2[cmd2.index("--resume") + 1], "abc")
        self.assertEqual(cmd2[cmd2.index("--allowedTools") + 1], "Bash(python3 *)")
        self.assertIn("H.md", cmd2[cmd2.index("--append-system-prompt") + 1])
        self.assertIn("rotated", la.build_preamble(spec, rotated=True))


class SessionsAndCalls(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = _registry(self.tmp)
        self.stub = self.tmp / "claude_stub.sh"
        self.stub.write_text(STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.sessions = self.tmp / "state" / "sessions.json"
        self.log = self.tmp / "state" / "calls.jsonl"
        self.patch = mock.patch.object(la, "CLAUDE_PROJECTS_DIR", self.tmp / "projects")
        self.patch.start()
        os.environ.pop("STUB_FAIL_RESUME", None)

    def tearDown(self):
        self.patch.stop()
        os.environ.pop("STUB_FAIL_RESUME", None)

    def _transcript(self, sid: str, size: int):
        p = la.transcript_path(str(self.tmp / "proj_a"), sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)

    def _call(self, prompt="hello", **kw):
        return la.call("strategy_a", prompt, registry=self.reg, sessions_path=self.sessions,
                       calls_log_path=self.log, claude_cmd=str(self.stub), **kw)

    def test_decide_session_rotation(self):
        spec = la.resolve("strategy_a", la.load_registry(self.reg))
        st = {"strategy_a": {"session_id": "s1"}}
        self.assertEqual(la.decide_session(spec, {}), (None, "first"))
        self.assertEqual(la.decide_session(spec, st), (None, "transcript_missing"))
        self._transcript("s1", 10)
        self.assertEqual(la.decide_session(spec, st)[0], "s1")
        self._transcript("s1", 1_500_000)  # rotate_mb = 1
        sid, why = la.decide_session(spec, st)
        self.assertIsNone(sid)
        self.assertTrue(why.startswith("rotate"))

    def test_first_call_records_then_resumes(self):
        r1 = self._call()
        self.assertFalse(r1["is_error"])
        self.assertEqual((r1["result"], r1["session_reason"]), ("ok:hello", "first"))
        self._transcript("sid-fresh", 10)
        r2 = self._call("again")
        self.assertTrue(r2["resumed"])
        self.assertEqual(json.loads(self.sessions.read_text())["strategy_a"]["calls"], 2)
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[-1]["permissions_confirmed"])

    def test_missing_resumed_session_falls_back_to_fresh(self):
        self._call()
        self._transcript("sid-fresh", 10)
        os.environ["STUB_FAIL_RESUME"] = "1"
        r = self._call("x")
        self.assertFalse(r["is_error"])
        self.assertTrue(r["rotated"])
        self.assertIn("resume failed", r["session_reason"])


if __name__ == "__main__":
    unittest.main()
