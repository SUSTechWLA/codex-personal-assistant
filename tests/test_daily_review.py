"""Work evidence and suggestions are isolated from the task ledger and other days."""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_assistant", PROJECT / "assistant.py")
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)
import daily_review


def fact(id="work:2026-11-10:one", **overrides):
    item = {"id": id, "title": "修复合成项目的路径处理", "status": "completed",
            "occurred_at": "2026-11-10T17:00:00+08:00", "source": "codex_activity",
            "source_ref": "codex://thread/synthetic/turn/4", "evidence": "核查合成产物与测试输出，三个针对性检查通过。",
            "outcome": "修复已保存在合成产物中，三个针对性检查通过。", "completion_basis": "test"}
    item.update(overrides)
    return item


def recommendation(id="advice:one", **overrides):
    item = {"id": id, "category": "learning", "title": "练习路径边界检查",
            "why": "今天的路径处理缺陷显示需要补充输入边界判断。", "small_action": "用 15 分钟复现一个边界输入并验证修复。",
            "source_ref": "codex://thread/synthetic/turn/4", "priority": 3}
    item.update(overrides)
    return item


def envelope(**overrides):
    value = {"date": "2026-11-10", "observed_at": "2026-11-10T21:00:00+08:00",
             "coverage": [{"source": "codex_activity", "status": "partial", "scope": "本日合成项目一个聊天，核对最后四轮与选定产物。",
                           "source_ref": "codex://thread/synthetic"}], "entries": [fact()], "recommendations": [recommendation()]}
    value.update(overrides)
    return value


class DailyReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        assistant.init(self.root)
        self.config = assistant.load_config(self.root)
        self.config["required_sources"] = ["gmail"]
        self.config["profile"] = {"name": "测试用户", "aliases": ["self", "测试用户"]}
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.day = date(2026, 11, 10)
        self.now = datetime.fromisoformat("2026-11-10T21:00:00+08:00")

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, value=None):
        return daily_review.log_work(self.root, envelope() if value is None else value, self.config)

    def review(self, day=None):
        return daily_review.build_review(self.root, day or self.day, self.now, assistant.status(self.root))

    def journal_snapshot(self):
        with closing(sqlite3.connect(self.root / "data" / "journal.sqlite3")) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
                    for table in ("entries", "coverage", "recommendations", "recommendation_state", "history")}

    def test_upsert_is_idempotent_and_keeps_change_history(self):
        self.assertEqual(1, self.log()["changed"]["entries"])
        before = self.journal_snapshot()
        self.assertEqual({"entries": 0, "coverage": 0, "recommendations": 0}, self.log()["changed"])
        self.assertEqual(before, self.journal_snapshot())
        self.log(envelope(entries=[fact(outcome="补充核查后确认三个测试通过。")]))
        self.assertEqual(1, len(self.review()["entries"]))
        self.assertIn("补充核查", self.review()["entries"][0]["outcome"])
        self.assertEqual(1, len(self.journal_snapshot()["history"]) - len(before["history"]))

    def test_work_dates_are_isolated_and_id_cannot_migrate(self):
        self.log()
        tomorrow = envelope(date="2026-11-11", observed_at="2026-11-11T21:00:00+08:00",
                            entries=[fact(occurred_at="2026-11-11T17:00:00+08:00")])
        before = self.journal_snapshot()
        with self.assertRaisesRegex(ValueError, "another date"):
            self.log(tomorrow)
        self.assertEqual(before, self.journal_snapshot())
        tomorrow["entries"][0]["id"] = "work:2026-11-11:one"
        self.log(tomorrow)
        self.assertEqual(["work:2026-11-10:one"], [item["id"] for item in self.review()["entries"]])
        next_day = daily_review.build_review(self.root, date(2026, 11, 11), datetime.fromisoformat("2026-11-11T21:00:00+08:00"), assistant.status(self.root))
        self.assertEqual(["work:2026-11-11:one"], [item["id"] for item in next_day["entries"]])

    def test_complete_requires_evidence_source_and_explicit_delivery_basis(self):
        for field in ("evidence", "source_ref", "outcome", "completion_basis"):
            value = envelope()
            del value["entries"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.log(value)
        with self.assertRaises(ValueError):
            self.log(envelope(entries=[fact(completion_basis="thread_completed")]))
        self.assertFalse((self.root / "data" / "journal.sqlite3").exists())

    def test_temporal_validation_uses_local_day_and_rejects_naive_or_future_facts(self):
        for changes in ({"occurred_at": "2026-11-10T17:00:00"},
                        {"occurred_at": "2026-11-11T00:30:00+08:00"},
                        {"occurred_at": "2026-11-10T22:00:00+08:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.log(envelope(entries=[fact(**changes)]))
        with self.assertRaises(ValueError):
            self.log(envelope(observed_at="2026-11-10T21:00:00"))
        # UTC's previous date is still this work date in Shanghai.
        self.log(envelope(entries=[fact(occurred_at="2026-11-09T20:00:00Z")]))
        self.assertEqual(1, len(self.review()["entries"]))

    def test_no_facts_does_not_claim_no_work_or_create_a_database(self):
        value = self.review()
        rendered = daily_review.render_review(value)
        self.assertIn("未采集到本日工作证据，不代表今天没有工作", rendered)
        self.assertTrue(any("codex_activity" in gap for gap in value["coverage_gaps"]))
        self.assertTrue(any("gmail" in gap for gap in value["coverage_gaps"]))
        self.assertFalse((self.root / "data" / "journal.sqlite3").exists())

    def test_all_rendered_source_text_is_escaped(self):
        hostile = '<script>alert("x")</script> [run](https://invalid.example)\n# injected | cell'
        item = fact(title=hostile, outcome=hostile, evidence=hostile,
                    source_ref="codex://synthetic/<script>|ref")
        advice = recommendation(title=hostile, why=hostile, small_action=hostile,
                                source_ref="codex://synthetic/<script>|advice")
        value = envelope(entries=[item], recommendations=[advice])
        value["coverage"][0]["scope"] = hostile
        self.log(value)
        rendered = daily_review.render_review(self.review())
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("\n# injected", rendered)
        self.assertNotIn("[run](", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn(r"\| cell", rendered)

    def test_recommendations_replace_only_when_explicit_and_never_change_tasks(self):
        assistant.ingest(self.root, {"sources": [{"id": "gmail", "status": "ok", "observed_at": self.now.isoformat(), "scope": "合成消息"}],
            "tasks": [{"id": "task:one", "title": "合成待办", "source": "gmail", "source_ref": "gmail/thread/synthetic", "evidence": "请测试用户检查合成输入。",
                       "owner": "self", "status": "todo", "confidence": 0.95, "importance": 3, "estimate_minutes": 30}]})
        before = assistant.status(self.root)
        self.log()
        without_advice = envelope(entries=[fact(outcome="已补充人工核验。")])
        del without_advice["recommendations"]
        self.log(without_advice)
        self.assertEqual(1, len(self.review()["recommendations"]))
        self.log(envelope(recommendations=[]))
        self.assertEqual([], self.review()["recommendations"])
        self.assertEqual(before, assistant.status(self.root))

    def test_next_day_is_limited_to_three_prioritized_suggestions(self):
        suggestions = [recommendation(id=f"advice:{number}", category="next_day", priority=number) for number in range(1, 6)]
        self.log(envelope(recommendations=suggestions))
        value = self.review()
        self.assertEqual([5, 4, 3], [item["priority"] for item in value["next_day"]])
        self.assertEqual(5, len(value["recommendations"]))
        self.assertIn("建议需要本人决定", daily_review.render_review(value))

    def test_learning_is_limited_to_two_prioritized_suggestions(self):
        self.log(envelope(recommendations=[recommendation(id=f"learn:{i}", priority=i) for i in range(1, 6)]))
        value = self.review()
        self.assertEqual([5, 4], [item["priority"] for item in value["learning"]])
        self.assertEqual(2, daily_review.render_review(value).count("**建议："))

    def test_failed_batch_rolls_back_after_a_sql_write(self):
        self.log()
        with closing(sqlite3.connect(self.root / "data" / "journal.sqlite3")) as db:
            db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON entries WHEN NEW.id = 'work:reject' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            db.commit()
        before = self.journal_snapshot()
        value = envelope(observed_at="2026-11-10T21:10:00+08:00", entries=[fact(outcome="不应留下这次修改。"), fact("work:reject")], recommendations=[])
        value["coverage"][0]["scope"] = "不应留下这次覆盖变更。"
        with self.assertRaises(sqlite3.IntegrityError):
            self.log(value)
        self.assertEqual(before, self.journal_snapshot())

    def test_each_entry_needs_same_date_source_coverage(self):
        with self.assertRaisesRegex(ValueError, "coverage for this date"):
            self.log(envelope(coverage=[]))
        self.log(envelope(entries=[]))
        self.log(envelope(coverage=[]))  # Previously saved same-date coverage is sufficient.
        self.assertEqual(1, len(self.review()["entries"]))

    def test_duplicate_unknown_and_overspecified_records_are_rejected_atomically(self):
        self.log()
        before = self.journal_snapshot()
        for changes in ({"entries": [fact(), fact()]},
                        {"recommendations": [recommendation(), recommendation()]},
                        {"extra": "not a fact"},
                        {"entries": [fact(thread_completed=True)]},
                        {"recommendations": [recommendation(priority=True)]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.log(envelope(**changes))
            self.assertEqual(before, self.journal_snapshot())

    def test_task_done_is_context_not_proof_and_other_owners_are_excluded(self):
        ledger = assistant.status(self.root)
        ledger["tasks"] = [{"id": "mine", "title": "尚无今日交付证据", "status": "done", "owner": "self", "source_ref": "gmail/synthetic"},
                           {"id": "someone_else", "title": "他人任务", "status": "todo", "owner": "other", "source_ref": "gmail/other"}]
        value = daily_review.build_review(self.root, self.day, self.now, ledger)
        self.assertEqual([], value["entries"])
        self.assertEqual(["mine"], [item["id"] for item in value["task_context"]])
        self.assertIn("done 状态不等于本日交付", daily_review.render_review(value))

    def test_report_outputs_do_not_replace_plan_or_ics(self):
        self.log()
        plan = self.root / "reports" / "2026-11-10.md"
        plan.parent.mkdir()
        plan.write_text("合成晨间计划", encoding="utf-8")
        result = daily_review.write_review(self.root, self.day, self.now, assistant.status(self.root))
        self.assertEqual("合成晨间计划", plan.read_text(encoding="utf-8"))
        self.assertEqual("2026-11-10-review.md", Path(result["report"]).name)
        machine = json.loads(Path(result["review_json"]).read_text(encoding="utf-8"))
        self.assertEqual("2026-11-10", machine["date"])
        self.assertEqual(1, result["completed"])
        self.assertEqual([], list(plan.parent.glob(".review-*.tmp")))

    def test_failed_report_staging_preserves_existing_reports_and_cleans_temp(self):
        self.log()
        first = daily_review.write_review(self.root, self.day, self.now, assistant.status(self.root))
        before = {key: Path(first[key]).read_bytes() for key in ("report", "review_json")}
        with patch.object(daily_review.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                daily_review.write_review(self.root, self.day, self.now, assistant.status(self.root))
        self.assertEqual(before, {key: Path(first[key]).read_bytes() for key in before})
        self.assertEqual([], list((self.root / "reports").glob(".review-*.tmp")))

    def test_old_observation_does_not_replace_newer_work_facts(self):
        self.log()
        value = envelope(observed_at="2026-11-10T18:00:00+08:00", entries=[fact(status="in_progress", outcome="旧进度")])
        del value["recommendations"]
        self.log(value)
        self.assertEqual("completed", self.review()["entries"][0]["status"])

    def test_old_recommendations_cannot_replace_or_resurrect_newer_suggestions(self):
        self.log()
        old = envelope(observed_at="2026-11-10T18:00:00+08:00", recommendations=[])
        self.log(old)
        self.assertEqual(1, len(self.review()["recommendations"]))
        self.log(envelope(observed_at="2026-11-10T21:10:00+08:00", recommendations=[]))
        self.log(envelope())
        self.assertEqual([], self.review()["recommendations"])

    def test_cli_import_and_report_in_isolation_and_error_is_readable(self):
        source = self.root / "synthetic.json"
        source.write_text(json.dumps(envelope()), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, assistant.main(["--root", str(self.root), "log-work", str(source)]))
        self.assertEqual(1, json.loads(out.getvalue())["changed"]["entries"])
        with redirect_stdout(io.StringIO()) as rendered:
            self.assertEqual(0, assistant.main(["--root", str(self.root), "daily-review", "--date", "2026-11-10", "--now", self.now.isoformat()]))
        self.assertTrue(Path(json.loads(rendered.getvalue())["report"]).exists())
        source.write_text(json.dumps(envelope(entries=[fact(evidence="")])), encoding="utf-8")
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(2, assistant.main(["--root", str(self.root), "log-work", str(source)]))
        self.assertIn("evidence", json.loads(error.getvalue())["error"])

    def test_future_review_date_and_naive_generation_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.review(date(2026, 11, 11))
        with self.assertRaisesRegex(ValueError, "timezone"):
            daily_review.build_review(self.root, self.day, datetime(2026, 11, 10), assistant.status(self.root))


if __name__ == "__main__":
    unittest.main()
