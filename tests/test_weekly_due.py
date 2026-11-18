"""Offline catch-up, pinned intents and delivery acknowledgement regressions."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import assistant
import daily_review
import weekly_review


def moment(value):
    return datetime.fromisoformat(value + "+08:00")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class WeeklyDueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        assistant.init(self.root)
        self.config = assistant.load_config(self.root)
        self.config["required_sources"] = ["codex_activity"]
        self.config["output"].update(weekly_enabled=True, weekly_start_date="2026-11-16")
        self.config["integrations"]["feishu"]["delivery"] = {
            "message_sender": "bot", "expected_owner_id": "ou_synthetic", "expected_bot_name": "Synthetic Assistant"}
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, now="2026-11-20T18:00:00"):
        return weekly_review.weekly_status(self.root, moment(now), self.config)

    def generate(self, now="2026-11-20T18:00:00"):
        current = moment(now)
        return daily_review.write_review(self.root, current.date(), current, assistant.status(self.root), weekly_due=True)

    def manifest(self, run):
        profile = self.config["integrations"]["feishu"]["profile"]
        url = "https://synthetic.feishu.cn/docx/weekly" + run["report_date"].replace("-", "")
        account = {"profile": profile, "owner_id": "ou_synthetic", "app_id": "cli_synthetic",
                   "documents": {run["document_key"]: {"state": "verified", "content_hash": run["content_hash"],
                                                       "token": "syntheticDocument", "url": url}},
                   "notifications": {run["notification_key"]: {"state": "sent", "message_id": "om_synthetic",
                                                                "sender": "bot", "owner_id": "ou_synthetic", "app_id": "cli_synthetic",
                                                                "content_hash": digest(run["notification_text"] + "\n\n" + url)}}}
        return {"schema_version": 1, "accounts": {digest(profile + "\0ou_synthetic"): account}}

    def write_manifest(self, value):
        path = self.root / "data" / "feishu-delivery" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def deliver(self, now="2026-11-20T18:05:00"):
        run = self.status(now)
        self.write_manifest(self.manifest(run))
        proof = self.status(now)["receipt"]
        return weekly_review.complete_week(self.root, run["week_start"], proof, moment(now), self.config)

    def test_start_date_prevents_enabling_midweek_from_chasing_previous_week(self):
        before = {path.relative_to(self.root) for path in self.root.rglob("*")}
        for now in ("2026-11-18T14:00:00", "2026-11-20T17:59:59"):
            self.assertFalse(self.status(now)["due"])
        self.assertEqual(before, {path.relative_to(self.root) for path in self.root.rglob("*")})
        self.assertFalse((self.root / "data" / "journal.sqlite3").exists())
        due = self.status("2026-11-20T18:00:00")
        self.assertTrue(due["due"])
        self.assertEqual("2026-11-16", due["week_start"])
        self.assertEqual("2026-11-20", due["report_date"])

    def test_weekend_and_next_week_catchup_keep_friday_range_keys_and_file(self):
        for now in ("2026-11-21T11:00:00", "2026-11-22T19:00:00", "2026-11-23T09:00:00"):
            due = self.status(now)
            self.assertEqual("2026-11-16", due["week_start"])
            self.assertEqual("2026-11-20", due["week_end"])
            self.assertEqual("brief:2026-11-20:review", due["document_key"])
        result = self.generate("2026-11-23T09:00:00")
        self.assertEqual("2026-11-20", result["date"])
        machine = json.loads(Path(result["review_json"]).read_text(encoding="utf-8"))
        self.assertEqual("2026-11-16", machine["weekly"]["start_date"])
        self.assertEqual("2026-11-20", machine["weekly"]["end_date"])
        self.assertEqual("2026-11-20-review.md", Path(result["report"]).name)
        self.assertFalse(any("2026-11-23" in path.name for path in (self.root / "reports").iterdir()))

    def test_generated_body_is_pinned_across_days_and_ordinary_review_retries(self):
        result = self.generate()
        original = Path(result["report"]).read_bytes()
        # New observations cannot silently change an unresolved publication body.
        daily_review.log_work(self.root, {
            "date": "2026-11-20", "observed_at": "2026-11-21T10:00:00+08:00",
            "coverage": [{"source": "codex_activity", "status": "ok", "scope": "合成成果回填。", "source_ref": "codex://synthetic"}],
            "entries": [{"id": "work:late", "title": "补核合成成果", "status": "completed",
                         "occurred_at": "2026-11-20T19:00:00+08:00", "source": "codex_activity", "source_ref": "codex://synthetic",
                         "evidence": "合成测试确认。", "outcome": "合成成果已回填确认。", "completion_basis": "test"}]}, self.config)
        second = self.generate("2026-11-23T09:00:00")
        self.assertTrue(second["reused"])
        self.assertEqual(result["weekly_delivery"]["content_hash"], second["weekly_delivery"]["content_hash"])
        daily_review.write_review(self.root, date(2026, 11, 20), moment("2026-11-23T09:00:00"), assistant.status(self.root))
        self.assertEqual(original, Path(result["report"]).read_bytes())

    def test_generation_is_not_delivery_and_fake_receipt_cannot_complete(self):
        self.generate()
        status = self.status()
        self.assertTrue(status["due"])
        self.assertEqual("generated", status["state"])
        with self.assertRaises(ValueError):
            weekly_review.complete_week(self.root, "2026-11-16", {"all_delivered": True}, moment("2026-11-20T18:01:00"), self.config)
        self.assertTrue(self.status()["due"])

    def test_real_manifest_recovers_crash_after_send_without_generating_or_sending_again(self):
        generated = self.generate()
        original = Path(generated["report"]).read_bytes()
        self.write_manifest(self.manifest(self.status()))
        recovered = self.status("2026-11-21T09:00:00")
        self.assertFalse(recovered["due"])
        self.assertTrue(recovered["needs_completion"])
        self.assertEqual("delivered_unacknowledged", recovered["state"])
        self.assertFalse(self.generate("2026-11-21T09:00:00")["weekly_due"])
        self.assertEqual(original, Path(generated["report"]).read_bytes())
        completed = weekly_review.complete_week(self.root, "2026-11-16", recovered["receipt"], moment("2026-11-21T09:01:00"), self.config)
        self.assertEqual("completed", completed["state"])
        self.assertFalse(self.status("2026-11-23T09:00:00")["due"])

    def test_multiple_missed_weeks_advance_oldest_first_without_losing_a_week(self):
        for expected in ("2026-11-20", "2026-11-27", "2026-12-04"):
            self.assertEqual(expected, self.status("2026-12-05T10:00:00")["report_date"])
            self.generate("2026-12-05T10:00:00")
            self.deliver("2026-12-05T10:05:00")
        self.assertFalse(self.status("2026-12-05T10:10:00")["due"])
        self.assertEqual(3, len(list((self.root / "reports").glob("*-review.md"))))

    def test_manifest_proof_rejects_identity_hash_state_or_message_mismatch(self):
        self.generate()
        run = self.status()
        valid = self.manifest(run)
        mutations = [
            lambda a: a["documents"][run["document_key"]].update(state="partial"),
            lambda a: a["documents"][run["document_key"]].update(content_hash="0" * 64),
            lambda a: a["documents"][run["document_key"]].update(token=None),
            lambda a: a["notifications"][run["notification_key"]].update(state="unknown"),
            lambda a: a["notifications"][run["notification_key"]].update(message_id=None),
            lambda a: a["notifications"][run["notification_key"]].update(owner_id="ou_other"),
            lambda a: a["notifications"][run["notification_key"]].update(app_id="cli_other"),
            lambda a: a["notifications"][run["notification_key"]].update(sender="user"),
            lambda a: a["notifications"][run["notification_key"]].update(content_hash=digest("ordinary daily reminder")),
            lambda a: a.update(profile="different-profile"),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                broken = deepcopy(valid)
                mutation(next(iter(broken["accounts"].values())))
                self.write_manifest(broken)
                with self.assertRaises(ValueError):
                    weekly_review.delivery_proof(self.root, run, self.config)
                self.assertFalse(self.status().get("needs_completion"))
        self.write_manifest(valid)
        proof = self.status()["receipt"]
        for key in ("report_hash", "document_url", "notification_key", "message_id"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                weekly_review.complete_week(self.root, "2026-11-16", {**proof, key: "different"}, moment("2026-11-20T18:02:00"), self.config)

    def test_existing_ordinary_notification_is_a_conflict_not_a_success_or_new_key(self):
        self.generate()
        run = self.status()
        manifest = self.manifest(run)
        notification = next(iter(manifest["accounts"].values()))["notifications"][run["notification_key"]]
        notification["content_hash"] = digest("已有普通日报\n\nhttps://synthetic.feishu.cn/docx/weekly20261120")
        self.write_manifest(manifest)
        status = self.status()
        self.assertEqual("delivery_check_required", status["state"])
        self.assertTrue(status["needs_verification"])
        self.assertFalse(status["due"])
        self.assertFalse(self.generate()["weekly_due"])
        self.assertEqual("notify:2026-11-20:review", status["notification_key"])

    def test_completed_week_stays_completed_after_legitimate_daily_document_update(self):
        self.generate()
        completed = self.deliver()
        manifest_path = self.root / "data" / "feishu-delivery" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        account = next(iter(manifest["accounts"].values()))
        account["documents"]["brief:2026-11-20:review"]["content_hash"] = digest("合法日终更新")
        self.write_manifest(manifest)
        daily_review.log_work(self.root, {
            "date": "2026-11-20", "observed_at": "2026-11-20T21:00:00+08:00",
            "coverage": [{"source": "codex_activity", "status": "ok", "scope": "合成晚间验证。", "source_ref": "codex://synthetic"}],
            "entries": [{"id": "work:evening", "title": "晚间合成验证", "status": "completed", "occurred_at": "2026-11-20T20:00:00+08:00",
                         "source": "codex_activity", "source_ref": "codex://synthetic", "evidence": "合成测试已核对。",
                         "outcome": "晚间合成验证通过。", "completion_basis": "test"}]}, self.config)
        result = daily_review.write_review(self.root, date(2026, 11, 20), moment("2026-11-20T21:00:00"), assistant.status(self.root))
        self.assertNotIn("reused", result)
        self.assertEqual(weekly_review.fingerprint(Path(result["report"]).read_text(encoding="utf-8")), result["weekly_delivery"]["content_hash"])
        self.assertEqual(completed["receipt"]["report_hash"], result["weekly_delivery"]["delivered_content_hash"])
        self.assertNotEqual(result["weekly_delivery"]["content_hash"], result["weekly_delivery"]["delivered_content_hash"])
        self.assertFalse(self.status("2026-11-20T21:01:00")["due"])
        self.assertEqual("completed", weekly_review.complete_week(self.root, "2026-11-16", completed["receipt"],
                                                                  moment("2026-11-20T21:01:00"), self.config)["state"])

    def test_disk_failure_keeps_uncompleted_intent_for_later_retry(self):
        with patch.object(daily_review.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.generate()
        status = self.status()
        self.assertTrue(status["due"])
        self.assertEqual("pending", status["state"])
        self.assertIsNone(status["content_hash"])
        self.assertEqual("generated", self.generate()["weekly_delivery"]["state"])


if __name__ == "__main__":
    unittest.main()
