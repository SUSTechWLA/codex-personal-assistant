"""Regression checks from independent review; all state stays in temp roots."""
from datetime import date
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "assistant_review", Path(__file__).resolve().parents[1] / "assistant.py"
)
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)


def source(status="ok"):
    return {
        "id": "feishu_calendar", "status": status,
        "observed_at": "2026-11-10T09:00:00+08:00",
        "scope": "2026-11-10T00:00:00+08:00 至 2026-11-11T00:00:00+08:00，已读取全部页",
    }


def task(task_id, **overrides):
    record = {
        "id": task_id, "title": task_id, "source": "feishu_calendar",
        "source_ref": "https://example.test/evidence/" + task_id,
        "evidence": "明确要求本人完成此项工作", "owner": "self",
        "status": "todo", "confidence": .99, "importance": 5,
        "estimate_minutes": 60,
    }
    record.update(overrides)
    return record


def snapshot():
    return {
        "source": "feishu_calendar", "start": "2026-11-10T00:00:00+08:00",
        "end": "2026-11-11T00:00:00+08:00", "complete": True,
    }


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        assistant.init(self.root)
        assistant.ingest(self.root, {"sources": [source()]})

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return assistant.build_plan(
            assistant.status(self.root), date(2026, 11, 10),
            assistant.parse_dt("2026-11-10T09:00:00+08:00"),
        )

    def test_failed_partial_or_unverified_snapshot_preserves_all_state(self):
        assistant.ingest(self.root, {
            "tasks": [task("keep")],
            "events": [{"id": "meeting", "title": "已有会议", "source": "feishu_calendar",
                        "start": "2026-11-10T15:00:00+08:00", "end": "2026-11-10T16:00:00+08:00"}],
        })
        before = assistant.status(self.root)
        for coverage in ([], [source("partial")], [source("unavailable")]):
            with self.subTest(coverage=coverage):
                with self.assertRaises(assistant.ValidationError):
                    assistant.ingest(self.root, {
                        "sources": coverage, "tasks": [task("keep", title="不得写入")],
                        "calendar_snapshots": [snapshot()],
                    })
                self.assertEqual(before, assistant.status(self.root))

    def test_confirmed_current_empty_snapshot_replaces_only_covered_window(self):
        assistant.ingest(self.root, {"events": [
            {"id": "today", "title": "今日会议", "source": "feishu_calendar",
             "start": "2026-11-10T15:00:00+08:00", "end": "2026-11-10T16:00:00+08:00"},
            {"id": "future", "title": "以后会议", "source": "feishu_calendar",
             "start": "2026-11-12T15:00:00+08:00", "end": "2026-11-12T16:00:00+08:00"},
        ]})
        result = assistant.ingest(self.root, {
            "sources": [source()], "calendar_snapshots": [snapshot()],
        })
        self.assertEqual(1, result["removed_calendar_events"])
        self.assertEqual(["future"], [e["id"] for e in assistant.status(self.root)["events"]])

    def test_early_deadline_is_met_before_long_important_later_task(self):
        assistant.ingest(self.root, {"tasks": [
            task("A", estimate_minutes=240, due_at="2026-11-10T18:00:00+08:00"),
            task("B", importance=4, estimate_minutes=30, due_at="2026-11-10T10:00:00+08:00"),
        ]})
        plan = self.plan()
        self.assertEqual("B", plan["blocks"][0]["task_id"])
        for item in plan["eligible"]:
            finished = max(b["end"] for b in plan["blocks"] if b["task_id"] == item["id"])
            self.assertLessEqual(finished, assistant.parse_dt(item["due_at"]))
        self.assertFalse(plan["risks"])
        self.assertFalse(plan["remaining"])

    def test_impossible_deadline_does_not_starve_feasible_deadline(self):
        assistant.ingest(self.root, {"tasks": [
            task("impossible", estimate_minutes=240, due_at="2026-11-10T09:30:00+08:00"),
            task("feasible", importance=3, estimate_minutes=30, due_at="2026-11-10T02:00:00Z"),
        ]})
        plan = self.plan()
        self.assertEqual("feasible", plan["blocks"][0]["task_id"])
        self.assertEqual({"impossible"}, {item["id"] for item, _ in plan["risks"]})
        self.assertLessEqual(plan["scheduled_minutes"], plan["free_minutes"] * .7)

    def test_without_today_deadlines_goal_and_importance_priority_is_preserved(self):
        assistant.ingest(self.root, {
            "goals": [{"id": "brain", "title": "确认的机器人大脑目标", "status": "active", "evidence": "用户确认"}],
            "tasks": [
                task("plain", importance=3),
                task("linked", importance=3, goal_id="brain"),
                task("important", importance=5),
            ],
        })
        self.assertEqual(["important", "linked", "plain"], [t["id"] for t in self.plan()["eligible"]])


if __name__ == "__main__":
    unittest.main()
