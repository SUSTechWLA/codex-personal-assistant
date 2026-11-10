"""Behavioral tests use only temporary local ledgers, never the user's data."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "assistant.py"
spec = importlib.util.spec_from_file_location("assistant", MODULE)
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)


def source(id="gmail", status="ok"):
    return {"id": id, "status": status, "observed_at": "2026-11-10T08:00:00+08:00", "scope": "最近七日，已翻页至末尾"}


def task(id="task:1", **overrides):
    value = {"id": id, "title": "完成项目方案", "source": "gmail", "source_ref": "gmail/thread/1", "evidence": "请测试用户今天完成方案。", "owner": "self", "status": "todo", "confidence": 0.95, "importance": 5, "estimate_minutes": 60, "due_at": "2026-11-10T18:00:00+08:00", "goal_id": None}
    value.update(overrides)
    return value


def event(id="event:1", **overrides):
    value = {"id": id, "title": "项目评审", "source": "google_calendar", "start": "2026-11-10T10:00:00+08:00", "end": "2026-11-10T11:00:00+08:00"}
    value.update(overrides)
    return value


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        assistant.init(self.root)
        # Exercise a configured evening schedule and an explicit owner alias;
        # these preferences belong to the fixture, not the shared defaults.
        config = assistant.load_config(self.root)
        config["profile"] = {"name": "测试用户", "aliases": ["self", "测试用户"]}
        config["work_windows"] = [["09:00", "12:00"], ["13:00", "18:00"], ["19:00", "21:00"]]
        (self.root / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, tasks=None, events=None, **extra):
        envelope = {"sources": [source(), source("google_calendar")], "tasks": tasks or [], "events": events or [], **extra}
        return assistant.ingest(self.root, envelope)

    def plan(self, now="2026-11-10T09:00:00+08:00", day="2026-11-10"):
        return assistant.build_plan(assistant.status(self.root), date.fromisoformat(day), assistant.parse_dt(now))

    def test_invalid_batch_rolls_back_all_changes(self):
        self.seed([task()])
        before = assistant.status(self.root)
        with self.assertRaises(assistant.ValidationError):
            self.seed([task(title="不应写入"), task("task:bad", due_at="2026-11-10T10:00:00")])
        self.assertEqual(before, assistant.status(self.root))
        with self.assertRaises(assistant.ValidationError):
            self.seed([task(title="不应写入", goal_id="missing")])
        self.assertEqual(before, assistant.status(self.root))

    def test_idempotent_ingest_and_terminal_status_survives_refresh(self):
        self.seed([task()])
        before = assistant.status(self.root)["history_count"]
        changed = self.seed([task()])
        self.assertEqual(0, sum(changed["changed"].values()))
        self.assertEqual(before, assistant.status(self.root)["history_count"])
        assistant.update_status(self.root, "task:1", "done")
        self.seed([task(evidence="再次读取同一条旧消息")])
        self.assertEqual("done", assistant.status(self.root)["tasks"][0]["status"])
        self.assertFalse(self.plan()["blocks"])
        self.seed([task(reopen=True)])
        self.assertEqual("todo", assistant.status(self.root)["tasks"][0]["status"])

    def test_scheduling_avoids_past_meetings_meals_and_capacity_overload(self):
        self.seed([task(estimate_minutes=1000)], [event(), event("event:2", start="2026-11-10T11:30:00+08:00", end="2026-11-10T13:30:00+08:00")])
        plan = self.plan(now="2026-11-10T10:12:12+08:00")
        self.assertLessEqual(plan["scheduled_minutes"], int(plan["free_minutes"] * .7))
        self.assertTrue(plan["remaining"])
        busy = [(assistant.parse_dt(e["start"]), assistant.parse_dt(e["end"])) for e in plan["events"]]
        busy += [(assistant.parse_dt("2026-11-10T12:00:00+08:00"), assistant.parse_dt("2026-11-10T13:00:00+08:00")), (assistant.parse_dt("2026-11-10T18:00:00+08:00"), assistant.parse_dt("2026-11-10T19:00:00+08:00"))]
        for block in plan["blocks"]:
            self.assertGreaterEqual(block["start"], assistant.parse_dt("2026-11-10T10:12:12+08:00"))
            self.assertTrue(all(block["end"] <= begin or block["start"] >= end for begin, end in busy))
        for a, b in zip(plan["blocks"], plan["blocks"][1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_uncertain_owner_low_confidence_waiting_and_completed_are_not_scheduled(self):
        self.seed([task("unknown", owner=None), task("other", owner="同事"), task("low", confidence=.5), task("waiting", status="waiting"), task("done", status="done"), task("cancelled", status="cancelled"), task("ok", owner="测试用户")])
        plan = self.plan()
        self.assertEqual({"ok"}, {b["task_id"] for b in plan["blocks"]})
        self.assertEqual(3, len(plan["uncertain"]))
        self.assertEqual(1, len(plan["waiting"]))

    def test_weekends_and_past_dates_have_no_scheduled_blocks(self):
        self.seed([task()])
        self.assertFalse(self.plan(day="2026-11-14")["blocks"])
        self.assertFalse(self.plan(day="2026-11-09")["blocks"])

    def test_utc_deadline_is_compared_as_instant_and_reported_locally(self):
        self.seed([task(due_at="2026-11-10T01:30:00Z")])
        plan = self.plan()
        self.assertEqual(1, len(plan["risks"]))
        self.assertIn("2026\\-11\\-10T09:30\\+08:00", assistant.render_markdown(plan))

    def test_cancelled_and_transparent_events_do_not_block(self):
        self.seed([task()], [event(status="cancelled"), event("event:2", transparency="transparent")])
        plan = self.plan()
        self.assertFalse(plan["events"])
        self.assertEqual("09:00", plan["blocks"][0]["start"].strftime("%H:%M"))

    def test_complete_snapshot_removes_disappeared_events_only_in_its_scope(self):
        self.seed(events=[event(), event("event:future", start="2026-11-20T10:00:00+08:00", end="2026-11-20T11:00:00+08:00")])
        self.seed(calendar_snapshots=[{"source": "google_calendar", "start": "2026-11-10T00:00:00+08:00", "end": "2026-11-11T00:00:00+08:00", "complete": True}])
        self.assertEqual(["event:future"], [e["id"] for e in assistant.status(self.root)["events"]])
        with self.assertRaises(assistant.ValidationError):
            self.seed(calendar_snapshots=[{"source": "google_calendar", "start": "2026-11-10T00:00:00+08:00", "end": "2026-11-11T00:00:00+08:00", "complete": False}])

    def test_partial_missing_and_stale_sources_are_visible(self):
        self.seed(sources=[source(status="partial"), source("google_calendar")])
        plan = self.plan()
        self.assertTrue(any("gmail" in w and "部分覆盖" in w for w in plan["source_warnings"]))
        self.assertTrue(any("feishu_mail" in w and "尚未读取" in w for w in plan["source_warnings"]))
        stale_plan = self.plan(now="2026-11-12T09:00:00+08:00", day="2026-11-12")
        self.assertTrue(any("google_calendar" in w and "已过期" in w for w in stale_plan["source_warnings"]))

    def test_ics_is_escaped_folded_and_contains_only_draft_blocks(self):
        title = "测试 <script>alert('x')</script> | " + "中" * 70 + ";末尾,反斜线\\"
        self.seed([task(title=title)], [event()])
        plan = self.plan()
        content = assistant.render_ics(plan)
        self.assertIn("\\;末尾\\,反斜线\\\\", content.replace("\r\n ", ""))
        self.assertEqual(len(plan["blocks"]), content.count("BEGIN:VEVENT"))
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in content.split("\r\n")))
        self.assertNotIn("项目评审", content)
        rendered = assistant.render_markdown(plan)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        with self.assertRaises(assistant.ValidationError):
            self.seed([task(title="ok\r\nBEGIN:VEVENT")])

    def test_user_goals_affect_priority_and_are_validated(self):
        self.seed([task("linked", goal_id="robot-brain", due_at=None, importance=3), task("other", due_at=None, importance=3)], goals=[{"id": "robot-brain", "title": "机器人大脑开发", "status": "active", "evidence": "用户已明确"}])
        self.assertEqual("linked", self.plan()["eligible"][0]["id"])
        with self.assertRaises(assistant.ValidationError):
            self.seed(goals=[{"id": "robot-brain", "title": "开发", "status": "invented"}])

    def test_plan_artifacts_are_written_and_parseable_without_network(self):
        self.seed([task()])
        output = assistant.write_plan(self.root, date(2026, 11, 10), assistant.parse_dt("2026-11-10T09:00:00+08:00"))
        self.assertEqual(60, output["scheduled_minutes"])
        self.assertTrue(Path(output["report"]).read_text(encoding="utf-8").startswith("# 2026-11-10"))
        self.assertTrue(Path(output["calendar_draft"]).read_bytes().startswith(b"BEGIN:VCALENDAR\r\n"))


if __name__ == "__main__":
    unittest.main()
