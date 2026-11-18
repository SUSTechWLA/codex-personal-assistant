"""Weekly reviews use dated facts, stable work identities and one daily artifact."""
from contextlib import closing, redirect_stdout
from datetime import date, datetime
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

import assistant
import daily_review


class WeeklyReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        assistant.init(self.root)
        self.config = assistant.load_config(self.root)
        self.config["required_sources"] = ["codex_activity"]
        self.save_config()

    def tearDown(self):
        self.tmp.cleanup()

    def save_config(self):
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def log(self, day, *, entries=None, recommendations=None, coverage_status="ok", observed_at=None):
        value = {"date": day, "observed_at": observed_at or day + "T21:00:00+08:00",
                 "coverage": [{"source": "codex_activity", "status": coverage_status,
                               "scope": "已核对合成工作记录。", "source_ref": "codex://synthetic"}],
                 "entries": entries or []}
        if recommendations is not None:
            value["recommendations"] = recommendations
        daily_review.log_work(self.root, value, self.config)

    def fact(self, day, identifier, *, status="completed", task_id=None, **overrides):
        item = {"id": identifier, "title": "合成接口回归", "status": status,
                "occurred_at": day + "T17:00:00+08:00", "source": "codex_activity",
                "source_ref": "codex://synthetic/shared-thread", "evidence": "PRIVATE_SYNTHETIC_EVIDENCE",
                "outcome": "合成接口回归通过。", "completion_basis": "test"}
        if task_id:
            item["task_id"] = task_id
        item.update(overrides)
        return item

    def advice(self, identifier, category="next_week", **overrides):
        item = {"id": identifier, "category": category, "title": "补齐合成边界回归",
                "why": "本周合成输入出现了待覆盖边界。", "small_action": "补齐合成边界回归并核对结果。",
                "source_ref": "codex://synthetic/recommendation", "priority": 3}
        item.update(overrides)
        return item

    def review(self, day="2026-11-13", *, now=None, force=True, tasks=None):
        ledger = assistant.status(self.root)
        if tasks is not None:
            ledger["tasks"] = tasks
        return daily_review.build_review(self.root, date.fromisoformat(day),
                                         datetime.fromisoformat(now or day + "T21:00:00+08:00"), ledger, weekly=force)

    def test_weekly_is_opt_in_and_only_automatic_on_configured_day(self):
        self.assertNotIn("weekly", self.review(force=False))
        self.config["output"]["weekly_enabled"] = True
        self.save_config()
        self.assertIn("weekly", self.review(force=False))
        self.assertNotIn("weekly", self.review("2026-11-12", force=False))
        self.config["output"]["weekly_day"] = 4
        self.save_config()
        self.assertIn("weekly", self.review("2026-11-12", force=False))
        self.assertNotIn("weekly", self.review(force=False))

    def test_manual_midweek_range_excludes_previous_future_and_unconfigured_days(self):
        for day in ("2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-14"):
            self.log(day, entries=[self.fact(day, "fact:" + day)])
        week = self.review("2026-11-11")["weekly"]
        self.assertEqual("2026-11-09", week["start_date"])
        self.assertEqual("2026-11-11", week["end_date"])
        self.assertEqual(["2026-11-09", "2026-11-10", "2026-11-11"], week["workdays"])
        self.assertEqual(week["workdays"], [item["work_date"] for item in week["entries"]])
        self.assertEqual([], week["missing_workdays"])
        self.assertFalse(any(gap["date"] > "2026-11-11" for gap in week["coverage_gaps"]))

    def test_week_boundaries_cross_month_and_year_in_local_timezone(self):
        for report_day, monday, excluded in (("2027-01-01", "2026-12-28", "2026-12-27"),
                                             ("2026-05-01", "2026-04-27", "2026-04-26")):
            with self.subTest(report_day=report_day):
                self.log(excluded, entries=[self.fact(excluded, "excluded:" + excluded)])
                self.log(monday, entries=[self.fact(monday, "included:" + monday,
                                                   occurred_at=monday + "T00:30:00+08:00")])
                week = self.review(report_day)["weekly"]
                self.assertEqual(monday, week["start_date"])
                self.assertEqual(["included:" + monday], [item["id"] for item in week["entries"]])
                self.assertEqual(5, len(week["workdays"]))
        # A UTC Sunday observation belongs to local Monday, not the prior week.
        self.log("2026-11-09", entries=[self.fact("2026-11-09", "utc:monday", occurred_at="2026-11-08T16:30:00Z")])
        self.assertEqual(["utc:monday"], [item["id"] for item in self.review()["weekly"]["entries"]])

    def test_workday_config_filters_both_facts_and_coverage_gaps(self):
        self.config["workdays"] = [1, 3, 5]
        self.save_config()
        self.log("2026-11-09")
        self.log("2026-11-10", entries=[self.fact("2026-11-10", "excluded:tuesday")])
        self.log("2026-11-13", coverage_status="partial")
        week = self.review()["weekly"]
        self.assertEqual(["2026-11-09", "2026-11-11", "2026-11-13"], week["workdays"])
        self.assertEqual([], week["entries"])
        self.assertEqual(["2026-11-11"], week["missing_workdays"])
        self.assertEqual({"2026-11-11", "2026-11-13"}, {item["date"] for item in week["coverage_gaps"]})
        rendered = daily_review.render_review(self.review())
        self.assertIn("11/11工作活动未采集", rendered)
        self.assertNotIn("本周没有工作", rendered)

    def test_stable_updates_count_same_work_once_and_latest_state_wins(self):
        for day, status in (("2026-11-09", "in_progress"), ("2026-11-10", "completed"), ("2026-11-11", "completed")):
            self.log(day, entries=[self.fact(day, "entry:" + day, task_id="task:shared", status=status)])
        week = self.review()["weekly"]
        self.assertEqual(3, len(week["entries"]))
        self.assertEqual(1, len(week["work_items"]))
        self.assertEqual(1, len(week["achievements"]))
        self.assertEqual("entry:2026-11-11", week["achievements"][0]["id"])
        for status in ("in_progress", "blocked", "cancelled"):
            with self.subTest(status=status):
                self.log("2026-11-13", entries=[self.fact("2026-11-13", "entry:friday", task_id="task:shared", status=status)])
                week = self.review()["weekly"]
                self.assertEqual([], week["achievements"])
                self.assertEqual(1 if status == "blocked" else 0, len(week["risks"]))
                self.assertEqual(4, len(week["entries"]))

    def test_same_date_correction_and_repeated_logging_never_duplicate_completion(self):
        item = self.fact("2026-11-10", "stable:id")
        self.log("2026-11-10", entries=[item])
        self.log("2026-11-10", entries=[item])
        self.assertEqual(1, len(self.review()["weekly"]["achievements"]))
        item["status"] = "cancelled"
        self.log("2026-11-10", entries=[item], observed_at="2026-11-13T20:00:00+08:00")
        self.assertEqual([], self.review()["weekly"]["achievements"])
        self.assertEqual(1, len(self.review()["weekly"]["entries"]))

    def test_same_day_latest_task_correction_also_removes_obsolete_today_claim(self):
        self.log("2026-11-13", entries=[
            self.fact("2026-11-13", "early:claim", task_id="task:reopened", occurred_at="2026-11-13T16:00:00+08:00",
                      outcome="旧的已完成声明必须撤回。"),
            self.fact("2026-11-13", "late:correction", task_id="task:reopened", occurred_at="2026-11-13T20:00:00+08:00",
                      status="blocked", outcome="后续核实发现边界尚未通过。")])
        rendered = daily_review.render_review(self.review())
        self.assertNotIn("旧的已完成声明必须撤回", rendered)
        self.assertIn("后续核实发现边界尚未通过", rendered)

    def test_independent_deliveries_in_same_chat_are_not_merged_by_title_or_source(self):
        self.log("2026-11-10", entries=[self.fact("2026-11-10", "one"), self.fact("2026-11-10", "two")])
        self.assertEqual(2, len(self.review()["weekly"]["achievements"]))

    def test_current_task_snapshot_never_fabricates_weekly_achievement(self):
        tasks = [{"id": identifier, "title": "合成任务" + identifier, "status": status, "owner": owner,
                  "source_ref": "synthetic/task"}
                 for identifier, status, owner in (("done", "done", "self"), ("todo", "todo", "self"),
                                                    ("waiting", "waiting", "self"), ("cancelled", "cancelled", "self"),
                                                    ("other", "todo", "another_user"))]
        week = self.review(tasks=tasks)["weekly"]
        self.assertEqual([], week["achievements"])
        self.assertEqual([], week["entries"])
        self.assertEqual({"todo", "waiting"}, {item["id"] for item in week["next_week"]})
        self.log("2026-11-10", entries=[self.fact("2026-11-10", "blocked:done", task_id="done", status="blocked")])
        self.assertEqual([], self.review(tasks=tasks)["weekly"]["risks"])

    def test_next_week_uses_only_current_explicit_horizon_not_old_daily_suggestions(self):
        self.log("2026-11-09", recommendations=[self.advice("old:week")])
        self.log("2026-11-13", recommendations=[self.advice("week:now"), self.advice("tomorrow", "next_day"),
                                               self.advice("focus:now", "focus"), self.advice("risk:now", "risk")])
        week = self.review()["weekly"]
        self.assertEqual({"week:now", "focus:now"}, {item["id"] for item in week["next_week"]})
        self.assertEqual(["risk:now"], [item["id"] for item in week["risks"]])
        self.log("2026-11-13", recommendations=[], observed_at="2026-11-13T21:05:00+08:00")
        self.assertEqual([], self.review(now="2026-11-13T21:10:00+08:00")["weekly"]["next_week"])

    def test_unavailable_workdays_are_disclosed_but_partial_days_not_mislabeled_unread(self):
        for day in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"):
            self.log(day, coverage_status="unavailable" if day == "2026-11-11" else "partial")
        self.assertEqual(["2026-11-11"], self.review()["weekly"]["missing_workdays"])
        self.assertEqual(5, len(self.review()["weekly"]["coverage_gaps"]))

    def test_future_observations_and_same_day_future_facts_are_not_in_today_line(self):
        self.log("2026-11-13", entries=[self.fact("2026-11-13", "future:fact", occurred_at="2026-11-13T22:00:00+08:00",
                                                outcome="未来结果不能提前出现。")], observed_at="2026-11-13T23:00:00+08:00")
        review = self.review(now="2026-11-13T21:00:00+08:00")
        self.assertEqual([], review["weekly"]["entries"])
        self.assertNotIn("未来结果不能提前出现", daily_review.render_review(review))

    def test_weekly_report_never_reads_daily_summary_prose(self):
        destination = self.root / "reports"
        destination.mkdir()
        (destination / "2026-11-09-review.md").write_text("伪造日报声称完成全部项目。", encoding="utf-8")
        self.log("2026-11-10", entries=[self.fact("2026-11-10", "real:fact")])
        rendered = daily_review.render_review(self.review())
        self.assertIn("合成接口回归通过", rendered)
        self.assertNotIn("伪造日报", rendered)

    def test_single_daily_artifact_preserves_private_evidence_and_no_weekly_ics(self):
        self.config["output"]["weekly_enabled"] = True
        self.save_config()
        self.log("2026-11-13", entries=[self.fact("2026-11-13", "private:id")], recommendations=[self.advice("week:one")])
        with closing(sqlite3.connect(self.root / "data" / "journal.sqlite3")) as db:
            before = db.execute("SELECT * FROM history ORDER BY sequence").fetchall()
        for _ in range(2):
            result = daily_review.write_review(self.root, date(2026, 11, 13),
                                               datetime.fromisoformat("2026-11-13T21:00:00+08:00"), assistant.status(self.root))
        self.assertEqual({"2026-11-13-review.md", "2026-11-13-review.json"}, {p.name for p in (self.root / "reports").iterdir()})
        self.assertFalse((self.root / "plans").exists())
        with closing(sqlite3.connect(self.root / "data" / "journal.sqlite3")) as db:
            self.assertEqual(before, db.execute("SELECT * FROM history ORDER BY sequence").fetchall())
        rendered = Path(result["report"]).read_text(encoding="utf-8")
        self.assertIn("今日：合成接口回归通过", rendered)
        self.assertIn("本周成果", rendered)
        self.assertIn("下周重点（建议）", rendered)
        self.assertNotIn("明日计划", rendered)
        self.assertNotIn("private:id", rendered)
        self.assertNotIn("PRIVATE_SYNTHETIC_EVIDENCE", rendered)
        machine = json.loads(Path(result["review_json"]).read_text(encoding="utf-8"))
        self.assertEqual("PRIVATE_SYNTHETIC_EVIDENCE", machine["weekly"]["entries"][0]["evidence"])

    def test_section_limits_and_character_budget_keep_summary_concise(self):
        for day in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"):
            self.log(day)
        entries = [self.fact("2026-11-13", f"done:{i}", outcome=f"第{i}项合成回归完成，输入边界检查已通过并有核实记录。") for i in range(5)]
        entries += [self.fact("2026-11-13", f"blocked:{i}", status="blocked", outcome=f"第{i}项合成联调阻塞，仍需确认设备接口权限后继续。") for i in range(4)]
        advice = [self.advice(f"next:{i}", small_action=f"先确认第{i}项合成设备接口范围，再安排边界回归和结果核对。") for i in range(5)]
        self.log("2026-11-13", entries=entries, recommendations=advice)
        for maximum in (400, 600):
            self.config["output"]["weekly_max_chars"] = maximum
            self.save_config()
            rendered = daily_review.render_review(self.review())
            self.assertLessEqual(len(rendered), maximum)
            sections = rendered.split("**")
            for title, maximum_items in (("本周成果", 3), ("问题/风险", 2), ("下周重点", 3)):
                content = next(sections[i + 1] for i, part in enumerate(sections[:-1]) if part.startswith(title))
                self.assertLessEqual(sum(line[:1].isdigit() for line in content.splitlines()), maximum_items)

    def test_cli_explicit_weekly_keeps_configuration_disabled_and_same_output_path(self):
        before = (self.root / "config.json").read_bytes()
        with redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(0, assistant.main(["--root", str(self.root), "daily-review", "--date", "2026-11-11",
                                                "--now", "2026-11-11T21:00:00+08:00", "--weekly"]))
        result = json.loads(stream.getvalue())
        self.assertEqual("2026-11-09", result["weekly"]["start_date"])
        self.assertEqual("2026-11-11-review.md", Path(result["report"]).name)
        self.assertEqual(before, (self.root / "config.json").read_bytes())

    def test_weekly_configuration_rejects_ambiguous_switch_day_and_budget(self):
        for field, value in (("weekly_enabled", "false"), ("weekly_day", True), ("weekly_day", 8),
                             ("weekly_max_chars", None), ("weekly_max_chars", "600"), ("weekly_max_chars", True)):
            with self.subTest(field=field, value=value):
                prior = self.config["output"][field]
                self.config["output"][field] = value
                self.save_config()
                with self.assertRaises(assistant.ValidationError):
                    assistant.load_config(self.root)
                self.config["output"][field] = prior
        self.config["workdays"] = [1, 2, 3, 4]
        self.config["output"]["weekly_enabled"] = True
        self.save_config()
        with self.assertRaises(assistant.ValidationError):
            assistant.load_config(self.root)


if __name__ == "__main__":
    unittest.main()
