"""Regression tests for scripts/report_daemon.py.

Pinned behaviour: the template queue renders exactly the docs/03 example | report on time, catch-up
(delay labelled), missed (one notice), once per slot | model failure still sends the top block |
input ages and STALE / MISSING | triggers on time, catch-up, missed once, dedupe on (id, trigger_at),
re-schedule by changing trigger_at | naive trigger_at rejected, never guessed | "fired" written before
running, "interrupted" after a restart, no silent re-run | one retry | a report collision defers a trigger,
which then runs even past catch-up | trigger_agent via logic_agent.py, tools off by default |
split by UTF-16 length, never truncated | Telegram secrets from the environment only | dry run sends
and records nothing | sleep/wake catch-up through the loop.

Run: python3 -m pytest tests -q   (or: python3 -m unittest discover tests)
Fake clock, stub runner (the real `claude` is never invoked), stub HTTP (nothing is sent to Telegram).
One test runs a stub shell script to check that a timeout kills the child.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import report_daemon as rd  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
TOKEN = "123456:SECRET-token-value-abcdefghijkl"
ENV = {"REPORT_TELEGRAM_TOKEN": TOKEN, "REPORT_TELEGRAM_CHAT_ID": "42"}


def kst(*a) -> datetime:
    return datetime(*a, tzinfo=KST).astimezone(timezone.utc)


def ok(text: str, cost: float = 0.01) -> tuple:
    return 0, json.dumps({"result": text, "is_error": False, "total_cost_usd": cost}), "", 5


def structured(**kw) -> tuple:
    body = {"headline": "quiet", "notable": [], "ceo_view": "", "stale": []}
    body.update(kw)
    return ok(json.dumps(body))


TIMEOUT = (124, "", "timeout 600s\n", 600000)
FAIL = (1, json.dumps({"result": "API Error: overloaded", "is_error": True}), "", 5)


class Clock:
    def __init__(self, t: datetime):
        self.t = t
        self.sleeps = []

    def __call__(self) -> datetime:
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += timedelta(seconds=s)


class Runner:
    """Stub for run_cmd: records calls; a response may be a tuple or a callable (to raise)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, cmd, stdin_text, cwd, timeout, env_drop=()):
        self.calls.append({"cmd": list(cmd), "stdin": stdin_text, "cwd": cwd, "timeout": timeout,
                           "env_drop": tuple(env_drop)})
        r = self.responses.pop(0) if self.responses else structured()
        return r() if callable(r) else r


class Http:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts = []

    def __call__(self, url, data, timeout):
        self.posts.append({"url": url, **data})
        return self.responses.pop(0) if self.responses else (200, '{"ok": true}')

    def texts(self) -> list:
        return [p["text"] for p in self.posts]


def trig(iid: str, at: str, **kw) -> dict:
    return {"id": iid, "due": "2026-01-15", "owner": "ceo", "title": f"task {iid}", "status": "open",
            "trigger_at": at, "trigger_prompt": f"run {iid}", **kw}


BASE_QUEUE = [
    {"id": "send_reply", "due": "2026-01-15", "owner": "human", "title": "Send reply to partner X",
     "status": "open", "action": "external", "ready": True},
    {"id": "weekly", "due": "2026-01-16", "owner": "ceo", "title": "Weekly review", "status": "open"},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # macOS: /var -> /private/var
        (self.tmp / "config").mkdir()
        shutil.copy(ROOT / "templates" / "report_prompt.md", self.tmp / "config" / "report_prompt.md")
        self.queue(BASE_QUEUE)
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.clock = Clock(kst(2026, 1, 15, 7, 0))
        self.runner = Runner()
        self.http = Http()
        self.out = io.StringIO()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue(self, items=None, raw: str = ""):
        p = self.tmp / "config" / "owner_action_queue.yaml"
        p.write_text(raw or yaml.safe_dump(items, sort_keys=False))

    def cfg(self, **over) -> dict:
        c = {"timezone": "Asia/Seoul", "report_times": ["08:00", "20:00"], "project_dir": str(self.tmp),
             "claude_cmd": "claude", "retry_delay_seconds": 30, **over}
        p = self.tmp / "config" / "report_daemon.yaml"
        p.write_text(yaml.safe_dump(c))
        return rd.load_config(p)

    def daemon(self, cfg=None, **kw) -> rd.Daemon:
        return rd.Daemon(cfg or self.cfg(), now_fn=self.clock, sleep_fn=self.clock.sleep, runner=self.runner,
                         post=self.http, out=self.out, **kw)

    def started(self, cfg=None, **kw) -> rd.Daemon:
        d = self.daemon(cfg, **kw)
        d.startup()  # installed at the current clock time
        return d

    def at(self, *a):
        self.clock.t = kst(*a)

    def rows(self, name: str) -> list:
        p = self.tmp / "state" / "report_daemon" / name
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


# ---------------------------------------------------------------- top block (docs/03)
class TopBlock(unittest.TestCase):
    def test_template_queue_renders_docs_example(self):
        items, warn = rd.load_queue(ROOT / "templates" / "owner_action_queue.yaml")
        self.assertIsNone(warn)
        doc = (ROOT / "docs" / "03-owner-action-queue.md").read_text()
        example = doc.split("## Example output", 1)[1].split("```")[1].strip("\n")
        top = rd.render_top_block(items, date(2026, 1, 15), datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc),
                                  {"tz": timezone.utc, "max_executed": 8})
        self.assertEqual(top, example)

    def test_overdue_bad_due_cap_and_owner_done_stay_visible(self):
        items = [{"id": f"d{i}", "title": f"done {i}", "owner": "ceo", "status": "done", "done_at": "2026-01-15"}
                 for i in range(10)]
        items += [{"id": "h", "title": "owner signed", "owner": "human", "status": "done", "done_at": "2026-01-14"},
                  {"id": "late", "title": "late item", "owner": "ceo", "status": "open", "due": "2026-01-12"},
                  {"id": "bad", "title": "bad date", "owner": "cto", "status": "open", "due": "soon"},
                  {"id": "nr", "title": "not ready", "owner": "human", "status": "open", "due": "2026-01-16",
                   "ready": False, "action": "external"}]
        top = rd.render_top_block(items, date(2026, 1, 15), kst(2026, 1, 15, 9, 0), {"tz": KST, "max_executed": 8})
        self.assertIn("> Decided and executed by CEO (24h) 11", top)
        self.assertIn("- and 3 more", top)
        self.assertIn("> Due soon or overdue (D-2) 3", top)
        self.assertIn("- [D+3] ceo - late item (2026-01-12)", top)
        self.assertIn("- [bad due] cto - bad date ('soon')", top)
        self.assertIn("- [D-1] human - not ready (2026-01-16)", top)
        self.assertIn("> Owner actions 0 (actions 0, fact checks 0)", top)

    def test_scheduled_section_lists_upcoming_and_invalid(self):
        items = [trig("soon", "2026-01-15T12:00:00+09:00", trigger_agent="ops_bot"),
                 trig("far", "2026-01-20T12:00:00Z"), trig("naive", "2026-01-15T12:00:00")]
        top = rd.render_top_block(items, date(2026, 1, 15), kst(2026, 1, 15, 9, 0), {"tz": KST})
        self.assertIn("> Scheduled triggers (next 24h) 1", top)
        self.assertIn("- 2026-01-15 12:00 KST - soon - agent ops_bot", top)
        self.assertIn("[invalid: no timezone (add Z or +hh:mm)] naive", top)
        self.assertNotIn("KST - far", top)


class Parsing(unittest.TestCase):
    def test_parse_trigger_at(self):
        utc = timezone.utc
        self.assertEqual(rd.parse_trigger_at("2026-01-16T09:05:00Z")[0], datetime(2026, 1, 16, 9, 5, tzinfo=utc))
        self.assertEqual(rd.parse_trigger_at("2026-01-16T18:05+09:00")[0], datetime(2026, 1, 16, 9, 5, tzinfo=utc))
        self.assertEqual(rd.parse_trigger_at("2026-01-16T18:05:00+0900")[0], datetime(2026, 1, 16, 9, 5, tzinfo=utc))
        for bad in ("2026-01-16T09:05:00", "2026-01-16", "tomorrow 9am", "", None):
            dt, err = rd.parse_trigger_at(bad)
            self.assertIsNone(dt, bad)
            self.assertTrue(err, bad)
        self.assertIn("no timezone", rd.parse_trigger_at("2026-01-16T09:05:00")[1])

    def test_unquoted_yaml_timestamps_stay_strings(self):
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "q.yaml"
        p.write_text("- id: a\n  status: open\n  due: 2026-01-15\n  trigger_at: 2026-01-15T09:05:00\n"
                     "- id: b\n  status: open\n  trigger_at: 2026-01-15T09:05:00Z\n")
        items, warn = rd.load_queue(p)
        self.assertIsNone(warn)
        self.assertEqual(items[0]["trigger_at"], "2026-01-15T09:05:00")  # not silently turned into a datetime
        self.assertEqual(items[0]["due"], "2026-01-15")
        self.assertIn("no timezone", rd.parse_trigger_at(items[0]["trigger_at"])[1])
        self.assertIsNone(rd.parse_trigger_at(items[1]["trigger_at"])[1])
        shutil.rmtree(tmp)

    def test_broken_queue_gives_warning_not_exception(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "q.yaml").write_text("- id: [unclosed\n")
        items, warn = rd.load_queue(tmp / "q.yaml")
        self.assertEqual(items, [])
        self.assertIn("cannot be parsed", warn)
        self.assertIn("not found", rd.load_queue(tmp / "missing.yaml")[1])
        shutil.rmtree(tmp)


class Config(Base):
    def _raw(self, text: str):
        p = self.tmp / "config" / "report_daemon.yaml"
        p.write_text(text)
        return p

    def test_timezone_required_and_validated(self):
        with self.assertRaises(rd.ConfigError) as e:
            rd.load_config(self._raw("report_times: ['08:00']\n"))
        self.assertIn("timezone is required", str(e.exception))
        with self.assertRaises(rd.ConfigError) as e:
            rd.load_config(self._raw("timezone: Mars/Base\nreport_times: ['08:00']\n"))
        self.assertIn("not a valid IANA name", str(e.exception))

    def test_unquoted_report_time_is_refused(self):
        with self.assertRaises(rd.ConfigError) as e:
            rd.load_config(self._raw("timezone: UTC\nreport_times: [08:00, 20:00]\n"))
        self.assertIn("1200", str(e.exception))

    def test_secret_in_yaml_is_refused(self):
        with self.assertRaises(rd.ConfigError) as e:
            rd.load_config(self._raw(f"timezone: UTC\ntelegram:\n  token: '{TOKEN}'\n"))
        self.assertIn("environment variables only", str(e.exception))

    def test_check_config_flags_naive_trigger_and_missing_env(self):
        self.queue(raw="- id: t\n  due: '2026-01-15'\n  owner: ceo\n  title: x\n  status: open\n"
                       "  trigger_at: 2026-01-15T09:05:00\n  trigger_prompt: go\n")
        self.cfg(claude_cmd=sys.executable)
        with mock.patch.dict(os.environ, {}, clear=True):
            res = rd.check_config(self.tmp / "config" / "report_daemon.yaml")
        self.assertTrue(any(lv == "warn" and "no timezone" in m for lv, m in res))
        self.assertTrue(any(lv == "error" and "REPORT_TELEGRAM_TOKEN" in m for lv, m in res))
        self.assertFalse(any(TOKEN in m for _, m in res))


# ---------------------------------------------------------------- periodic report
class Reports(Base):
    def test_on_time_once_tools_off_in_project_dir(self):
        d = self.started()
        self.at(2026, 1, 15, 8, 0, 30)
        d.tick()
        self.assertEqual(len(self.http.posts), 1)
        text = self.http.texts()[0]
        self.assertTrue(text.startswith("[Report 2026-01-15 08:00 KST] on time"))
        self.assertLess(text.index("> Owner actions 1"), text.index("> Summary: quiet"))
        call = self.runner.calls[0]
        self.assertEqual(call["cwd"], str(self.tmp))
        self.assertEqual(call["cmd"][call["cmd"].index("--tools") + 1], "")
        for flag in ("--permission-mode", "--continue", "--resume", "--dangerously-skip-permissions"):
            self.assertNotIn(flag, call["cmd"])
        self.assertIn("REPORT_TELEGRAM_TOKEN", call["env_drop"])
        self.assertIn("Send reply to partner X", call["stdin"])  # top block reaches the prompt
        self.at(2026, 1, 15, 8, 1, 30)
        d.tick()
        self.assertEqual(len(self.http.posts), 1)
        self.assertEqual([r["event"] for r in self.rows("reports.jsonl")], ["started", "sent"])

    def test_catchup_labels_delay(self):
        d = self.started()
        self.at(2026, 1, 15, 9, 10)
        d.tick()
        self.assertTrue(self.http.texts()[0].startswith("[Report 2026-01-15 08:00 KST] delayed 70 min"))

    def test_missed_beyond_catchup_notified_once(self):
        d = self.started()
        self.at(2026, 1, 15, 9, 45)
        d.tick()
        d.tick()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.http.posts), 1)
        self.assertIn("MISSED", self.http.texts()[0])
        self.assertIn("90 min catch-up", self.http.texts()[0])

    def test_slots_before_install_are_not_reported_missed(self):
        self.at(2026, 1, 15, 15, 0)
        d = self.started()
        d.tick()
        self.assertEqual(self.http.posts, [])

    def test_model_failure_still_sends_top_block(self):
        for response, why in ((TIMEOUT, "timeout after 600s"), (FAIL, "API Error: overloaded")):
            self.runner = Runner(response)
            self.http = Http()
            shutil.rmtree(self.tmp / "state", ignore_errors=True)
            self.at(2026, 1, 15, 7, 0)
            d = self.started()
            self.at(2026, 1, 15, 8, 1)
            d.tick()
            text = self.http.texts()[0]
            self.assertIn("> Owner actions 1", text)
            self.assertIn("- Send reply to partner X - 2026-01-15 (D-0) - external", text)
            self.assertIn(f"Model block unavailable ({why})", text)
            last = self.rows("reports.jsonl")[-1]
            self.assertEqual((last["event"], last["model_ok"]), ("sent", False))

    def test_inputs_carry_age_stale_and_missing(self):
        status = self.tmp / "cto.md"
        status.write_text("all checks green")
        old = self.clock().timestamp() - 30 * 3600
        os.utime(status, (old, old))
        cfg = self.cfg(inputs=[{"path": "cto.md", "label": "cto"}, {"path": "cfo.md", "label": "cfo"}])
        d = self.started(cfg)
        self.at(2026, 1, 15, 8, 0)
        d.tick()
        text = self.http.texts()[0]
        self.assertIn("cto 31h STALE", text)  # 30 h before 07:00, report at 08:00
        self.assertIn("cfo MISSING", text)
        self.assertRegex(text.splitlines()[1], r"^Inputs: queue \S+ · cto 31h STALE · cfo MISSING$")
        prompt = self.runner.calls[0]["stdin"]
        self.assertIn("### cto", prompt)
        self.assertIn("STALE", prompt)
        self.assertIn("all checks green", prompt)

    def test_unstructured_reply_shown_as_is(self):
        self.runner = Runner(ok("Plain words, no JSON."))
        d = self.started()
        self.at(2026, 1, 15, 8, 0)
        d.tick()
        self.assertIn("> CEO notes (unstructured reply, shown as is)\nPlain words, no JSON.", self.http.texts()[0])

    def test_undelivered_report_is_noted_in_the_next_one(self):
        self.http = Http((500, '{"ok":false,"description":"Internal"}'), (500, '{"ok":false,"description":"Internal"}'))
        d = self.started()
        self.at(2026, 1, 15, 8, 0)
        d.tick()
        self.assertEqual(self.rows("reports.jsonl")[-1]["event"], "send_failed")
        self.at(2026, 1, 15, 20, 0)
        d.tick()
        self.assertIn("previous report (2026-01-15 08:00 Asia/Seoul) was not delivered: HTTP 500",
                      self.http.texts()[-1])

    def test_sleep_and_wake_catch_up_through_the_loop(self):
        self.at(2026, 1, 15, 7, 58, 30)
        naps = iter([60, 70 * 60])  # second sleep = laptop lid closed for 70 min

        def sleep(_s):
            self.clock.t += timedelta(seconds=next(naps))

        d = rd.Daemon(self.cfg(), now_fn=self.clock, sleep_fn=sleep, runner=self.runner, post=self.http, out=self.out)
        d.run_forever(max_ticks=3)
        self.assertEqual(len(self.http.posts), 1)
        self.assertTrue(self.http.texts()[0].startswith("[Report 2026-01-15 08:00 KST] delayed 69 min"))


# ---------------------------------------------------------------- scheduled triggers
class Triggers(Base):
    def setUp(self):
        super().setUp()
        self.queue(BASE_QUEUE + [trig("t1", "2026-01-15T10:00:00+09:00")])
        self.at(2026, 1, 15, 9, 40)  # installed after the 08:00 report and its catch-up window

    def test_on_time_runs_once_tools_off(self):
        d = self.started()
        self.at(2026, 1, 15, 10, 2)
        d.tick()
        d.tick()
        self.assertEqual(len(self.runner.calls), 1)
        call = self.runner.calls[0]
        self.assertEqual(call["stdin"], "run t1")
        self.assertEqual(call["cmd"][call["cmd"].index("--tools") + 1], "")
        self.assertEqual([r["event"] for r in self.rows("triggers.jsonl")], ["fired", "done"])
        self.assertTrue(self.http.texts()[0].startswith("[Scheduled] t1 - on time - trigger 2026-01-15 10:00 KST - ok"))

    def test_early_window_and_not_yet(self):
        d = self.started()
        self.at(2026, 1, 15, 9, 50)
        d.tick()
        self.assertEqual(self.runner.calls, [])
        self.at(2026, 1, 15, 9, 56)
        d.tick()
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn("- on time -", self.http.texts()[0])

    def test_catchup_labels_delay(self):
        d = self.started()
        self.at(2026, 1, 15, 11, 0)
        d.tick()
        self.assertIn("t1 - delayed 60 min (catch-up)", self.http.texts()[0])

    def test_missed_notified_once_never_run(self):
        d = self.started()
        self.at(2026, 1, 15, 12, 0)
        d.tick()
        d.tick()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.http.posts), 1)
        self.assertIn("[Scheduled] t1 - MISSED", self.http.texts()[0])
        self.assertEqual(self.rows("triggers.jsonl")[-1]["event"], "missed")

    def test_dedupe_and_reschedule_by_changing_trigger_at(self):
        d = self.started()
        self.at(2026, 1, 15, 10, 1)
        d.tick()
        self.queue(BASE_QUEUE + [trig("t1", "2026-01-15T01:00:00Z")])  # same instant, other notation
        d.tick()
        self.assertEqual(len(self.runner.calls), 1)
        self.queue(BASE_QUEUE + [trig("t1", "2026-01-15T11:00:00+09:00")])  # new time = re-scheduled
        self.at(2026, 1, 15, 11, 1)
        d.tick()
        self.assertEqual(len(self.runner.calls), 2)

    def test_naive_trigger_rejected_once(self):
        self.queue(raw=yaml.safe_dump(BASE_QUEUE) +
                   "- id: naive\n  due: '2026-01-15'\n  owner: ceo\n  title: x\n  status: open\n"
                   "  trigger_at: 2026-01-15T10:00:00\n  trigger_prompt: go\n")
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        d.tick()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.http.posts), 1)
        self.assertIn("[Scheduled] naive - INVALID trigger_at '2026-01-15T10:00:00': no timezone", self.http.texts()[0])

    def test_missing_prompt_is_invalid_until_fixed(self):
        self.queue(BASE_QUEUE + [{**trig("np", "2026-01-15T10:00:00+09:00"), "trigger_prompt": ""}])
        d = self.started()
        d.tick()
        self.assertIn("trigger_prompt is missing", self.http.texts()[0])
        self.queue(BASE_QUEUE + [trig("np", "2026-01-15T10:00:00+09:00")])  # owner adds the prompt
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        self.assertEqual([c["stdin"] for c in self.runner.calls], ["run np"])

    def test_unexpected_runner_exception_is_a_reported_failure(self):
        def boom():
            raise RuntimeError("disk full")

        self.runner = Runner(boom, boom)
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        self.assertEqual([r["event"] for r in self.rows("triggers.jsonl")], ["fired", "retry", "done"])
        self.assertIn("FAILED after 2 attempts: RuntimeError: disk full", self.http.texts()[0])

    def test_fired_is_written_before_running_and_restart_reports_interrupted(self):
        def killed():
            raise SystemExit(143)

        self.runner = Runner(killed)
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        with self.assertRaises(SystemExit):
            d.tick()
        self.assertEqual([r["event"] for r in self.rows("triggers.jsonl")], ["fired"])
        self.runner = Runner()
        d2 = self.started()
        d2.tick()
        self.assertEqual(self.runner.calls, [])  # no silent re-run
        self.assertEqual(self.rows("triggers.jsonl")[-1]["event"], "interrupted")
        self.assertIn("[Scheduled] t1 - INTERRUPTED", self.http.texts()[-1])

    def test_interrupted_report_is_noticed_after_restart(self):
        def killed():
            raise SystemExit(143)

        self.queue(BASE_QUEUE)
        self.runner = Runner(killed)
        self.at(2026, 1, 15, 7, 0)
        d = self.started()
        self.at(2026, 1, 15, 8, 0)
        with self.assertRaises(SystemExit):
            d.tick()
        self.started()
        self.assertIn("[Report 2026-01-15 08:00 Asia/Seoul] INTERRUPTED", self.http.texts()[-1])

    def test_one_retry_then_success(self):
        self.runner = Runner(FAIL, ok("second try ok"))
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        self.assertEqual(len(self.runner.calls), 2)
        self.assertIn(30, self.clock.sleeps)
        self.assertEqual([r["event"] for r in self.rows("triggers.jsonl")], ["fired", "retry", "done"])
        self.assertTrue(self.rows("triggers.jsonl")[-1]["ok"])
        self.assertIn("second try ok", self.http.texts()[0])

    def test_one_retry_then_failure_reported(self):
        self.runner = Runner(FAIL, TIMEOUT)
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        d.tick()
        self.assertEqual(len(self.runner.calls), 2)
        self.assertIn("FAILED after 2 attempts: timeout after 600s", self.http.texts()[0])
        self.assertFalse(self.rows("triggers.jsonl")[-1]["ok"])

    def test_report_collision_defers_trigger_then_report_first(self):
        self.queue(BASE_QUEUE + [trig("t0", "2026-01-15T07:58:00+09:00")])
        self.at(2026, 1, 15, 7, 0)
        d = self.started()
        self.at(2026, 1, 15, 7, 57)
        d.tick()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.rows("triggers.jsonl")[-1]["event"], "deferred")
        self.at(2026, 1, 15, 8, 0, 20)
        d.tick()
        self.assertEqual([c["stdin"] for c in self.runner.calls][-1], "run t0")
        texts = self.http.texts()
        self.assertTrue(texts[0].startswith("[Report 2026-01-15 08:00 KST] on time"))
        self.assertIn("[Scheduled] t0 - deferred for the report", texts[1])

    def test_deferred_trigger_runs_even_past_catchup(self):
        self.queue(BASE_QUEUE + [trig("t0", "2026-01-15T07:58:00+09:00")])
        self.at(2026, 1, 15, 7, 0)
        d = self.started()
        self.at(2026, 1, 15, 7, 57)
        d.tick()
        self.at(2026, 1, 15, 10, 30)  # laptop slept through the report and the catch-up window
        d.tick()
        texts = self.http.texts()
        self.assertIn("[Report 2026-01-15 08:00 KST] MISSED", texts[0])
        self.assertIn("[Scheduled] t0 - deferred for the report, delayed 152 min", texts[1])
        self.assertNotIn("missed", [r["event"] for r in self.rows("triggers.jsonl")])

    def test_long_trigger_run_does_not_turn_the_report_or_next_trigger_into_missed(self):
        self.queue(BASE_QUEUE + [trig("long", "2026-01-15T07:50:00+09:00"),
                                 trig("next", "2026-01-15T08:30:00+09:00")])

        def slow():
            self.clock.t += timedelta(hours=2)  # e.g. an agent run near agent_timeout_s, plus nothing else
            return ok("long done")

        self.runner = Runner(slow, structured(), ok("next done"))
        self.at(2026, 1, 15, 7, 0)
        d = self.started()
        self.at(2026, 1, 15, 7, 50)
        d.tick()  # runs "long" until 09:50; the 08:00 report is now due, so "next" is deferred behind it
        self.at(2026, 1, 15, 9, 51)
        d.tick()
        texts = self.http.texts()
        self.assertEqual(len(texts), 3)
        self.assertIn("[Scheduled] long - on time", texts[0])
        self.assertIn("[Report 2026-01-15 08:00 KST] delayed 111 min (daemon busy with trigger long)", texts[1])
        self.assertIn("[Scheduled] next - deferred for the report", texts[2])
        self.assertFalse(any("MISSED" in t for t in texts))

    def test_trigger_agent_goes_through_logic_agent_tools_off(self):
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts" / "logic_agent.py").write_text("# stub path; the runner is stubbed\n")
        self.queue(BASE_QUEUE + [trig("a1", "2026-01-15T10:00:00+09:00", trigger_agent="ops_bot"),
                                 trig("a2", "2026-01-15T10:00:00+09:00", trigger_agent="ops_bot", trigger_tools=True),
                                 trig("c1", "2026-01-15T10:00:00+09:00", trigger_tools=True)])
        agent_reply = (0, json.dumps({"result": "agent ok", "is_error": False, "cost_usd": 0.1}), "", 5)
        self.runner = Runner(agent_reply, agent_reply, ok("ceo ok"))
        d = self.started()
        self.at(2026, 1, 15, 10, 0)
        d.tick()
        a1, a2, c1 = (c["cmd"] for c in self.runner.calls)
        self.assertEqual(a1[1:], [str(self.tmp / "scripts" / "logic_agent.py"), "ops_bot", "--json", "--no-tools"])
        self.assertNotIn("--no-tools", a2)
        self.assertNotIn("--tools", c1)
        self.assertEqual(self.runner.calls[0]["stdin"], "run a1")  # no CEO context injected
        self.assertIn("[Scheduled] a1 - agent ops_bot - on time", self.http.texts()[0])


# ---------------------------------------------------------------- delivery
class Delivery(Base):
    def test_split_by_utf16_never_truncates(self):
        lines = [f"{i:03d} 대표 액션 확인 😀 " + "x" * 40 for i in range(300)]
        text = "\n".join(lines + ["y" * 5000, "", "end"])
        chunks = rd.label_chunks(text, 4000)
        self.assertGreater(len(chunks), 3)
        for n, c in enumerate(chunks, 1):
            self.assertLessEqual(rd.utf16_len(c), 4000)
            self.assertTrue(c.startswith(f"({n}/{len(chunks)})\n"))
        body = "".join(c.split("\n", 1)[1] for c in chunks)
        self.assertEqual(body.replace("\n", ""), text.replace("\n", ""))

    def test_long_report_sent_in_chunks(self):
        self.queue([{"id": f"o{i}", "due": "2026-01-15", "owner": "human", "status": "open", "ready": True,
                     "action": "external", "title": f"Owner action {i} " + "z" * 80} for i in range(120)])
        d = self.started()
        self.at(2026, 1, 15, 8, 0)
        d.tick()
        self.assertGreater(len(self.http.posts), 1)
        for p in self.http.posts:
            self.assertLessEqual(rd.utf16_len(p["text"]), 4096)
        joined = "".join(self.http.texts())
        self.assertEqual(sum(f"Owner action {i} " in joined for i in range(120)), 120)
        self.assertEqual(self.rows("reports.jsonl")[-1]["chunks"], len(self.http.posts))

    def test_secrets_come_from_env_and_never_leak(self):
        unauthorized = (401, '{"ok":false,"error_code":401,"description":"Unauthorized"}')
        self.http = Http(unauthorized, unauthorized)
        d = self.daemon()
        ok_, err, _ = d.deliver("hello")
        self.assertFalse(ok_)
        self.assertEqual(self.http.posts[0]["url"], f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(self.http.posts[0]["chat_id"], "42")
        self.assertIn("HTTP 401: Unauthorized", err)
        self.assertNotIn(TOKEN, err)
        self.assertNotIn(TOKEN, (self.tmp / "state" / "report_daemon" / "daemon.log").read_text())
        child = rd._child_env(("REPORT_TELEGRAM_TOKEN", "REPORT_TELEGRAM_CHAT_ID"))
        self.assertNotIn("REPORT_TELEGRAM_TOKEN", child)

    def test_missing_env_prints_instead_of_sending(self):
        d = self.daemon()
        with mock.patch.dict(os.environ, {}, clear=True):
            ok_, err, _ = d.deliver("report body")
        self.assertFalse(ok_)
        self.assertIn("telegram not configured", err)
        self.assertEqual(self.http.posts, [])
        self.assertIn("report body", self.out.getvalue())

    def test_dry_run_once_sends_and_records_nothing(self):
        d = self.daemon(dry_run=True)
        self.assertTrue(d.run_report(None, "manual, dry run"))
        self.assertEqual(self.http.posts, [])
        self.assertIn("> Owner actions 1", self.out.getvalue())
        self.assertFalse((self.tmp / "state").exists())


class Subprocess(unittest.TestCase):
    def test_timeout_kills_the_child_and_returns_124(self):
        tmp = Path(tempfile.mkdtemp())
        stub = tmp / "slow.sh"
        stub.write_text("#!/bin/bash\nsleep 30\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        t0 = time.monotonic()
        rc, _out, err, _ms = rd.run_cmd([str(stub)], "", str(tmp), 1)
        self.assertEqual(rc, 124)
        self.assertIn("timeout 1s", err)
        self.assertLess(time.monotonic() - t0, 10)
        rc, _out, err, _ms = rd.run_cmd([str(tmp / "no-such-cli")], "", str(tmp), 1)
        self.assertEqual(rc, 127)
        self.assertIn("PATH", err)
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
