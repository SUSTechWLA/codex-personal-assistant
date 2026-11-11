"""Work evidence and suggestions are isolated from the task ledger and other days."""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime
import importlib.util
import io
import json
from pathlib import Path
import re
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
        self.assertIn("尚无已核实的今日成果，不代表没有工作", rendered)
        self.assertTrue(any("codex_activity" in gap for gap in value["coverage_gaps"]))
        self.assertTrue(any("gmail" in gap for gap in value["coverage_gaps"]))
        self.assertFalse((self.root / "data" / "journal.sqlite3").exists())

    def test_all_rendered_source_text_is_escaped(self):
        hostile = '<b>x</b>\n# injected | cell'
        item = fact(title=hostile, outcome=hostile, evidence=hostile,
                    source_ref="codex://synthetic/<script>|ref")
        advice = recommendation(title=hostile, why=hostile, small_action=hostile,
                                source_ref="codex://synthetic/<script>|advice")
        value = envelope(entries=[item], recommendations=[advice])
        value["coverage"][0]["scope"] = hostile
        self.log(value)
        rendered = daily_review.render_review(self.review())
        self.assertNotIn("<b>", rendered)
        self.assertNotIn("\n# injected", rendered)
        self.assertNotIn("[run](", rendered)
        self.assertIn("&lt;b&gt;", rendered)
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
        self.assertIn("明日计划（建议）", daily_review.render_review(value))

    def test_learning_is_limited_to_two_prioritized_suggestions(self):
        self.log(envelope(recommendations=[recommendation(id=f"learn:{i}", priority=i) for i in range(1, 6)]))
        value = self.review()
        self.assertEqual([5, 4], [item["priority"] for item in value["learning"]])
        rendered = daily_review.render_review(value)
        self.assertNotIn("学习建议", rendered)
        self.assertNotIn("练习路径边界检查", rendered)

    def test_compact_report_limits_sections_without_padding_empty_problems(self):
        achievements = [
            "修复合成接口的超时重试边界，断网恢复后能够继续处理请求。",
            "完善合成控制输入的异常处理，非法参数能够在执行前被明确拒绝。",
            "补齐合成环境的回归基准配置，相同输入现在能获得稳定测试结果。",
            "不应展示第四项成果。",
        ]
        issues = ["联调环境仍缺少合成设备接口权限，需确认访问范围后继续。",
                  "测试数据的采样频率尚未统一，需确认基准设置后才能比较。", "不应展示第三项问题。"]
        actions = ["先补充断网恢复用例并核对重试次数，完成后提交接口回归结果。",
                   "与合成设备维护人确认接口权限范围，再安排一次输入输出联调。",
                   "核对测试数据采样设置并固定合成基准，随后补齐结果比较记录。", "不应展示第四项计划。"]
        entries = [fact(f"work:done:{i}", outcome=text) for i, text in enumerate(achievements)]
        entries += [fact(f"work:blocked:{i}", status="blocked", outcome=text) for i, text in enumerate(issues)]
        entries.append(fact("work:in-progress", status="in_progress", outcome="普通进行中无需重复堆入问题。"))
        self.log(envelope(entries=entries, recommendations=[recommendation(f"plan:{i}", category="next_day", small_action=text)
                                                            for i, text in enumerate(actions)],
                          coverage=[*envelope()["coverage"], {"source": "google_calendar", "status": "partial",
                                    "scope": "合成日历已读取限定窗口。", "source_ref": "calendar/synthetic"}]))
        rendered = daily_review.render_review(self.review())
        self.assertEqual(8, len(re.findall(r"^\d+\. ", rendered, re.MULTILINE)))
        self.assertLessEqual(len(rendered), 430)
        self.assertGreaterEqual(len(rendered), 250)
        for text in achievements[:3] + issues[:2] + actions[:3]:
            self.assertIn(text, rendered)
        self.assertNotIn("不应展示", rendered)
        self.assertNotIn("普通进行中", rendered)
        self.assertNotIn("来源覆盖", rendered)
        self.assertNotIn("注：", rendered)
        # Use a separate empty day to check that absence never produces an issue section.
        empty = daily_review.build_review(self.root, date(2026, 11, 9), self.now, assistant.status(self.root))
        self.assertNotIn("问题/待办", daily_review.render_review(empty))

    def test_compact_report_keeps_private_evidence_out_of_markdown_but_in_json(self):
        secret = "PRIVATE_EVIDENCE_TRACE"
        entry = fact(id="work:internal-id", title="合成边界修复", evidence=secret,
                     source_ref="codex://thread/private-reference", task_id="task:private-id",
                     outcome="完成合成边界修复。日志保存在 D:\\Private Files\\raw.txt，内部 ID work:internal-id。")
        advice = recommendation(id="advice:internal-id", category="next_day", why="PRIVATE_LEARNING_CONTEXT",
                                source_ref="https://invalid.example/private-source",
                                small_action="补齐合成接口回归。参数位于 /home/private/raw.txt，联系 hidden@example.invalid。")
        self.log(envelope(entries=[entry], recommendations=[advice]))
        before = self.journal_snapshot()
        result = daily_review.write_review(self.root, self.day, self.now, assistant.status(self.root))
        rendered = Path(result["report"]).read_text(encoding="utf-8")
        for token in (secret, "PRIVATE_LEARNING_CONTEXT", "private-reference", "private-source", "work:internal-id", "task:private-id",
                      "advice:internal-id", "Private Files", "raw.txt", "/home/private", "hidden@example.invalid", "codex://", "http"):
            self.assertNotIn(token, rendered)
        self.assertIn("完成合成边界修复", rendered)
        self.assertIn("补齐合成接口回归", rendered)
        machine = json.loads(Path(result["review_json"]).read_text(encoding="utf-8"))
        self.assertEqual(secret, machine["entries"][0]["evidence"])
        self.assertEqual(entry["source_ref"], machine["entries"][0]["source_ref"])
        self.assertEqual("PRIVATE_LEARNING_CONTEXT", machine["next_day"][0]["why"])
        self.assertEqual(before, self.journal_snapshot())

    def test_inline_paths_and_record_ids_are_removed_even_in_short_summaries(self):
        self.log(envelope(entries=[fact(id="work:private", outcome="完成修复 work:private；D:/private/a.py", title="合成接口修复")],
                          recommendations=[recommendation(category="next_day", small_action="检查 /tmp/private.log，再补齐回归。")]))
        rendered = daily_review.render_review(self.review())
        self.assertNotIn("work:private", rendered)
        self.assertNotIn("D:/", rendered)
        self.assertNotIn("/tmp/", rendered)
        self.assertNotIn("private.log", rendered)

    def test_short_ids_do_not_change_result_counts_or_words(self):
        self.log(envelope(entries=[fact(id="1", outcome="完成12项合成检查，第1轮回归通过。")], recommendations=[]))
        rendered = daily_review.render_review(self.review())
        self.assertIn("完成12项合成检查，第1轮回归通过", rendered)

    def test_routine_delivery_is_not_user_output_but_verified_tool_releases_remain(self):
        delivery_coverage = {"source": "assistant_delivery", "status": "ok", "scope": "合成自投递记录。", "source_ref": "local/synthetic-delivery"}
        entries = [fact("assistant:morning-delivery", source="assistant_delivery", completion_basis="artifact", outcome="已发送自动晨间简报。"),
                   fact("assistant:tool-release", source="assistant_delivery", completion_basis="commit", outcome="发布合成助理代码版本，支持失败后恢复。"),
                   fact("assistant:tool-tests", source="assistant_delivery", completion_basis="test", outcome="完成合成助理发布回归，断网场景通过。"),
                   fact("assistant:message-wait", source="assistant_delivery", status="blocked", outcome="例行消息投递等待确认。", completion_basis="artifact")]
        self.log(envelope(entries=entries, coverage=[*envelope()["coverage"], delivery_coverage], recommendations=[]))
        result = daily_review.write_review(self.root, self.day, self.now, assistant.status(self.root))
        rendered = Path(result["report"]).read_text(encoding="utf-8")
        self.assertNotIn("自动晨间简报", rendered)
        self.assertNotIn("例行消息", rendered)
        self.assertNotIn("问题/待办", rendered)
        self.assertIn("发布合成助理代码版本", rendered)
        self.assertIn("完成合成助理发布回归", rendered)
        self.assertEqual(3, result["completed"])  # The existing raw machine count retains its meaning.
        self.assertEqual(2, result["brief_completed"])
        self.assertEqual(4, len(json.loads(Path(result["review_json"]).read_text(encoding="utf-8"))["entries"]))

    def test_overlong_wording_uses_supplied_short_title_or_whole_clause(self):
        long = "过长的原始技术细节" * 80
        self.log(envelope(entries=[fact("work:short-title", title="完成合成输入校验", outcome=long),
                                  fact("work:clause", title=long, outcome="完成合成边界回归，" + long),
                                  fact("work:no-summary", title=long, outcome=long)], recommendations=[]))
        rendered = daily_review.render_review(self.review())
        self.assertIn("完成合成输入校验", rendered)
        self.assertIn("完成合成边界回归", rendered)
        self.assertNotIn("过长的原始技术细节", rendered)
        self.assertNotIn("...", rendered)
        self.assertNotIn("…", rendered)
        self.assertIn("具体结果待简述", rendered)

    def test_only_decision_relevant_coverage_gaps_get_one_short_note(self):
        self.log(envelope(recommendations=[]))
        self.assertNotIn("注：", daily_review.render_review(self.review()))  # Known partial work read, and Gmail absent.
        self.log(envelope(recommendations=[recommendation(category="next_day")]))
        rendered = daily_review.render_review(self.review())
        self.assertEqual(1, rendered.count("注："))
        self.assertIn("尚未核对日历", rendered)
        self.log(envelope(coverage=[{"source": "google_calendar", "status": "partial", "scope": "合成限定日历窗口。",
                                    "source_ref": "calendar/synthetic"}], recommendations=[recommendation(category="next_day")]))
        self.assertNotIn("注：", daily_review.render_review(self.review()))

    def test_plan_preview_has_real_schedule_and_coverage_without_creating_documents(self):
        self.config["required_sources"] = ["gmail", "codex_activity"]
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        assistant.ingest(self.root, {"sources": [{"id": "gmail", "status": "ok", "observed_at": "2026-11-10T08:00:00+08:00", "scope": "合成消息"}],
            "tasks": [{"id": "task:preview", "title": "完成合成接口回归", "source": "gmail", "source_ref": "gmail/thread/synthetic",
                       "evidence": "请测试用户检查合成接口。", "owner": "self", "status": "todo", "confidence": 0.95, "importance": 3, "estimate_minutes": 30}]})
        before = assistant.status(self.root)
        files_before = {str(path.relative_to(self.root)) for path in self.root.rglob("*")}
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, assistant.main(["--root", str(self.root), "plan", "--date", "2026-11-10", "--now", "2026-11-10T09:00:00+08:00", "--preview"]))
        result = json.loads(out.getvalue())
        self.assertTrue(result["preview"])
        self.assertEqual("task:preview", result["blocks"][0]["task_id"])
        self.assertEqual("2026-11-10T09:00:00+08:00", result["blocks"][0]["start"])
        self.assertEqual(30, result["scheduled_minutes"])
        self.assertTrue(result["coverage"]["warnings"])
        self.assertEqual("gmail", result["coverage"]["sources"][0]["id"])
        self.assertEqual(files_before, {str(path.relative_to(self.root)) for path in self.root.rglob("*")})
        self.assertEqual(before, assistant.status(self.root))
        self.assertFalse((self.root / "reports").exists())
        self.assertFalse((self.root / "plans").exists())

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
        rendered = daily_review.render_review(value)
        self.assertNotIn("尚无今日交付证据", rendered)
        self.assertNotIn("他人任务", rendered)
        self.assertIn("尚无已核实的今日成果", rendered)

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
