import contextlib
import copy
import io
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import assistant
import feishu_delivery as delivery


class FakeCLI:
    """A stateful synthetic server behind the same bounded-runner interface."""
    def __init__(self):
        self.calls = []
        self.owner = "ou_syntheticOwner"
        self.app_id = "cli_syntheticApp"
        self.bot_name = "Personal Helper"
        self.identity_override = None
        self.documents = {}
        self.folders = {"fld_root": []}
        self.fetch_failures = 0
        self.create_timeout = False
        self.folder_timeout = False
        self.message_timeout = False
        self.message_failures = 0
        self.update_timeout = False
        self.update_result = "success"
        self.list_incomplete = False
        self.sent = []

    def response(self, value, identity):
        return {"returncode": 0, "stdout": json.dumps({"ok": True, "identity": identity, "data": value}).encode(), "stderr": b""}

    @staticmethod
    def flag(command, name, default=None):
        return command[command.index(name) + 1] if name in command else default

    def content(self, command, cwd):
        argument = self.flag(command, "--content")
        if not argument.startswith("@data/") or Path(argument[1:]).is_absolute():
            raise AssertionError("content must use a project-relative private @file")
        return (Path(cwd) / argument[1:]).read_text(encoding="utf-8")

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if kwargs["timeout_seconds"] != 45 or kwargs["max_bytes"] != 2 * 1024 * 1024:
            raise AssertionError("all operations must remain bounded")
        identity = self.identity_override or self.flag(command, "--as")
        if "whoami" in command:
            return {"returncode": 0, "stdout": json.dumps({"identity": identity, "available": True,
                    "appId": self.app_id, "onBehalfOf": {"openId": self.owner}}).encode(), "stderr": b""}
        if "+create-folder" in command:
            parent = self.flag(command, "--folder-token", "")
            token = "fld_child" + str(sum(len(v) for v in self.folders.values()) + 1)
            folder = {"name": self.flag(command, "--name"), "token": token, "type": "folder",
                      "url": "https://example.feishu.cn/drive/folder/" + token}
            self.folders.setdefault(parent, []).append(folder)
            self.folders[token] = []
            if self.folder_timeout:
                self.folder_timeout = False
                return {"error": "timeout", "returncode": -1}
            return self.response({"folder_token": token, "url": folder["url"]}, identity)
        if "drive" in command and "list" in command:
            token = json.loads(self.flag(command, "--params"))["folder_token"]
            data = {"files": self.folders.get(token, []), "has_more": False}
            if self.list_incomplete:
                data.pop("has_more")
            return self.response(data, identity)
        if "+create" in command:
            token = "dox_synthetic" + str(len(self.documents) + 1)
            self.documents[token] = {"content": self.content(command, kwargs["cwd"]), "revision_id": 1}
            self.folders.setdefault(self.flag(command, "--parent-token"), []).append(
                {"name": self.flag(command, "--title"), "token": token, "type": "docx",
                 "url": "https://example.feishu.cn/docx/" + token})
            if self.create_timeout:
                self.create_timeout = False
                return {"error": "timeout", "returncode": -1}
            return self.response({"document": {"document_id": token, "revision_id": 1,
                                  "url": "https://example.feishu.cn/docx/" + token}, "warnings": []}, identity)
        if "+fetch" in command:
            if self.fetch_failures:
                self.fetch_failures -= 1
                return {"returncode": 1, "stdout": json.dumps({"ok": False, "identity": identity,
                        "error": {"type": "authorization", "code": "missing_scope"}}).encode(), "stderr": b""}
            token = self.flag(command, "--doc")
            return self.response({"document": {"document_id": token, **self.documents[token]}}, identity)
        if "+update" in command:
            token = self.flag(command, "--doc")
            if int(self.flag(command, "--revision-id")) != self.documents[token]["revision_id"]:
                raise AssertionError("updates must use the just-fetched revision")
            self.documents[token].update(content=self.content(command, kwargs["cwd"]),
                                         revision_id=self.documents[token]["revision_id"] + 1)
            if self.update_timeout:
                self.update_timeout = False
                return {"error": "timeout", "returncode": -1}
            return self.response({"result": self.update_result,
                                  "document": {"revision_id": self.documents[token]["revision_id"]}, "warnings": []}, identity)
        if "/open-apis/bot/v3/info" in command:
            return self.response({"app_name": self.bot_name, "activate_status": 2, "open_id": "ou_syntheticBot"}, identity)
        if "+messages-send" in command:
            if self.message_failures:
                self.message_failures -= 1
                return {"returncode": 1, "stdout": json.dumps({"ok": False, "identity": identity,
                        "error": {"type": "authorization", "subtype": "missing_scope"}}).encode(), "stderr": b""}
            self.sent.append(list(command))
            if self.message_timeout:
                self.message_timeout = False
                return {"error": "timeout", "returncode": -1}
            return self.response({"message_id": "om_synthetic" + str(len(self.sent)), "chat_id": "oc_synthetic"}, identity)
        raise AssertionError("unexpected command")

    def count(self, command):
        return sum(command in call for call in self.calls)


class TransformingCLI(FakeCLI):
    def __init__(self):
        super().__init__()
        self.transform = None

    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if self.transform and ("+create" in command or "+update" in command):
            for value in self.documents.values():
                value["content"] = self.transform(value["content"])
        return result


class FeishuDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cli = FakeCLI()
        self.config = {"integrations": {"feishu": {"profile": "synthetic-profile", "delivery": {
            "enabled": True, "root_name": "Synthetic Workspace", "root_token": "fld_root",
            "root_url": "https://example.feishu.cn/drive/folder/fld_root",
            "folder_discovery": "managed_only", "send_self_enabled": False}}}}
        self.bundle = {"schema_version": 1, "documents": [{"key": "day:2026-11-11:review",
                       "title": "Synthetic daily review", "folder": ["01 Daily", "2026", "11"],
                       "content": "# Synthetic report\n\nA reviewed result.\n"}]}

    @property
    def settings(self):
        return self.config["integrations"]["feishu"]["delivery"]

    def publish(self, bundle=None, config=None, **kwargs):
        return delivery.publish(self.root, bundle or self.bundle, config or self.config,
                                runner=self.cli, command=["synthetic-cli"], apply=True, **kwargs)

    def account(self):
        manifest = json.loads((self.root / "data" / "feishu-delivery" / "manifest.json").read_text(encoding="utf-8"))
        return next(iter(manifest["accounts"].values()))

    def notify(self):
        self.bundle["notification"] = {"key": "review:2026-11-11", "text": "A synthetic result is ready."}
        self.settings["send_self_enabled"] = True

    def message_only(self):
        self.bundle = {"schema_version": 1, "documents": [],
                       "notification": {"key": "notify:2026-11-11:morning", "text": "1. Review the current task.\n2. Check the known blocker."}}
        self.settings.clear()
        self.settings.update(enabled=True, send_self_enabled=True, folder_discovery="managed_only")

    def test_notification_only_preview_is_offline_without_cloud_configuration(self):
        self.message_only()
        with patch("feishu_delivery.find_cli", side_effect=AssertionError("no CLI discovery")):
            result = delivery.publish(self.root, self.bundle, self.config)
        self.assertEqual(result["mode"], "preview")
        self.assertFalse(result["root_required"])
        self.assertEqual(result["documents"], [])
        self.assertIsNone(result["root_name"])
        self.assertIsNone(result["folder_discovery"])
        self.assertFalse((self.root / "data").exists())
        self.settings["enabled"] = False
        with patch("feishu_delivery.find_cli", side_effect=AssertionError("no CLI discovery")), self.assertRaises(ValueError):
            delivery.publish(self.root, self.bundle, self.config, apply=True)

    def test_notification_only_sends_plain_text_and_never_accesses_cloud_resources(self):
        self.message_only()
        result = self.publish()
        self.assertTrue(result["all_delivered"])
        self.assertEqual(result["documents"], [])
        self.assertIsNone(result["root"])
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--text"), self.bundle["notification"]["text"])
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--user-id"), self.cli.owner)
        self.assertEqual(len(self.cli.calls), 2)
        self.assertTrue(all("whoami" in call or "+messages-send" in call for call in self.cli.calls))
        account = self.account()
        self.assertNotIn("root", account)
        self.assertEqual(account["folders"], {})
        self.assertEqual(account["documents"], {})
        self.assertFalse((self.root / "data" / "feishu-delivery" / "content").exists())

    def test_notification_only_ignores_unused_cloud_settings_and_preserves_document_receipts(self):
        self.publish()
        before = self.account()
        self.bundle = {"schema_version": 1, "documents": [],
                       "notification": {"key": "notify:2026-11-11:afternoon", "text": "1. Continue the known task."}}
        # These cloud settings cannot affect a self reminder or trigger a root
        # migration, a destination lookup, or a read of an existing document.
        self.settings.update(root_token="obsolete / token", root_name="..", folder_discovery="unused-mode",
                             send_self_enabled=True)
        offset = len(self.cli.calls)
        result = self.publish()
        self.assertTrue(result["all_delivered"])
        self.assertIsNone(result["root"])
        self.assertTrue(all("whoami" in call or "+messages-send" in call for call in self.cli.calls[offset:]))
        after = self.account()
        self.assertEqual(before["root"], after["root"])
        self.assertEqual(before["folders"], after["folders"])
        self.assertEqual(before["documents"], after["documents"])
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--text"), self.bundle["notification"]["text"])

    def test_notification_only_deduplication_and_known_or_unknown_failures_keep_their_rules(self):
        self.message_only()
        for outcome in ("success", "failed", "unknown"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                cli = FakeCLI()
                cli.message_failures = 1 if outcome == "failed" else 0
                cli.message_timeout = outcome == "unknown"
                first = delivery.publish(directory, self.bundle, self.config, apply=True, runner=cli, command=["synthetic-cli"])
                second = delivery.publish(directory, self.bundle, self.config, apply=True, runner=cli, command=["synthetic-cli"])
                expected_first = {"success": "sent", "failed": "failed", "unknown": "unknown"}[outcome]
                expected_second = {"success": "unchanged", "failed": "sent", "unknown": "unknown"}[outcome]
                self.assertEqual(first["notification"]["state"], expected_first)
                self.assertEqual(second["notification"]["state"], expected_second)
                self.assertEqual(second["all_delivered"], outcome != "unknown")
                self.assertEqual(len(cli.sent), 1)
                self.assertTrue(all("whoami" in call or "+messages-send" in call for call in cli.calls))

    def test_notification_only_bot_is_verified_without_any_document_or_root(self):
        self.message_only()
        self.settings.update(message_sender="bot", expected_bot_name="Personal Helper")
        result = self.publish()
        self.assertTrue(result["all_delivered"])
        self.assertEqual(self.cli.count("/open-apis/bot/v3/info"), 1)
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--as"), "bot")
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--user-id"), self.cli.owner)
        self.assertTrue(all("whoami" in call or "/open-apis/bot/v3/info" in call or "+messages-send" in call
                            for call in self.cli.calls))
        self.bundle["notification"]["key"] = "notify:2026-11-11:stage"
        self.settings["expected_bot_name"] = "Different Helper"
        self.assertEqual(self.publish()["notification"]["state"], "failed")
        self.assertEqual(len(self.cli.sent), 1)
        self.assertNotIn("root", self.account())

    def test_notification_only_preserves_identity_and_application_namespace_checks(self):
        self.message_only()
        first_owner = self.cli.owner
        self.settings["expected_owner_id"] = first_owner
        self.assertTrue(self.publish()["all_delivered"])
        self.cli.owner = "ou_otherOwner"
        self.assertEqual(self.publish()["state"], "failed")
        self.assertEqual(len(self.cli.sent), 1)
        self.settings.pop("expected_owner_id")
        self.assertTrue(self.publish()["all_delivered"])
        self.assertEqual(self.cli.flag(self.cli.sent[1], "--user-id"), "ou_otherOwner")
        path = self.root / "data" / "feishu-delivery" / "manifest.json"
        accounts = json.loads(path.read_text(encoding="utf-8"))["accounts"]
        self.assertEqual(len(accounts), 2)
        self.assertTrue(all("root" not in account for account in accounts.values()))
        self.cli.app_id = "cli_differentApp"
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(len(self.cli.sent), 2)

    def test_empty_bundle_still_requires_documents_or_a_valid_notification(self):
        for value in ({"schema_version": 1, "documents": []},
                      {"schema_version": 1, "documents": [], "notification": None},
                      {"schema_version": 1, "documents": [], "notification": {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                delivery.publish(self.root, value, self.config, apply=True, runner=self.cli, command=["synthetic-cli"])
        self.assertFalse(self.cli.calls)

    def test_preview_and_disabled_apply_do_not_discover_cli_or_touch_network(self):
        with patch("feishu_delivery.find_cli", side_effect=AssertionError("no CLI discovery")):
            result = delivery.publish(self.root, self.bundle, self.config)
            self.assertEqual(result["mode"], "preview")
            self.assertNotIn("content", result["documents"][0])
            self.assertFalse((self.root / "data").exists())
            self.settings["enabled"] = False
            with self.assertRaises(ValueError):
                delivery.publish(self.root, self.bundle, self.config, apply=True)

    def test_managed_only_creates_once_and_same_content_is_verified_without_writes(self):
        first = self.publish()
        self.assertTrue(first["all_delivered"])
        second = self.publish()
        self.assertEqual(second["documents"][0]["state"], "unchanged")
        self.assertEqual(self.cli.count("+create"), 1)
        self.assertEqual(self.cli.count("+create-folder"), 3)
        self.assertEqual(self.cli.count("list"), 0)
        self.assertTrue(all(self.cli.flag(call, "--as") == "user" for call in self.cli.calls))

    def test_managed_only_needs_explicit_root_and_lock_blocks_concurrent_work(self):
        self.settings.pop("root_token")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.cli.calls)
        self.settings["root_token"] = "fld_root"
        directory = self.root / "data" / "feishu-delivery"
        directory.mkdir(parents=True)
        (directory / "publish.lock").write_text("another process", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.cli.calls)
        self.assertTrue((directory / "publish.lock").exists())

    def test_create_receipt_survives_fetch_failure_and_retry_never_recreates(self):
        self.cli.fetch_failures = 1
        first = self.publish()
        self.assertFalse(first["all_delivered"])
        record = self.account()["documents"][self.bundle["documents"][0]["key"]]
        self.assertEqual(record["state"], "created")
        self.assertTrue(record["token"])
        self.assertTrue(self.publish()["all_delivered"])
        self.assertEqual(self.cli.count("+create"), 1)

    def test_unknown_create_is_not_blindly_repeated(self):
        self.cli.create_timeout = True
        self.assertEqual(self.publish()["documents"][0]["state"], "unknown")
        self.assertEqual(self.publish()["documents"][0]["state"], "unknown")
        self.assertEqual(self.cli.count("+create"), 1)

    def test_unknown_folder_create_is_not_repeated(self):
        self.cli.folder_timeout = True
        self.assertEqual(self.publish()["documents"][0]["state"], "unknown")
        self.assertEqual(self.publish()["documents"][0]["state"], "unknown")
        self.assertEqual(self.cli.count("+create-folder"), 1)
        self.assertEqual(self.cli.count("+create"), 0)

    def test_update_checks_revision_and_detects_external_edit(self):
        token = self.publish()["documents"][0]["token"]
        self.bundle["documents"][0]["content"] = "# Reviewed change\n"
        self.assertTrue(self.publish()["all_delivered"])
        self.assertEqual(self.cli.count("+update"), 1)
        self.cli.documents[token].update(content="Manual notes", revision_id=99)
        self.bundle["documents"][0]["content"] = "# Further change\n"
        result = self.publish()
        self.assertEqual(result["documents"][0]["state"], "conflict")
        self.assertEqual(self.cli.documents[token]["content"], "Manual notes")
        self.assertEqual(self.cli.count("+update"), 1)

    def test_partial_update_blocks_requested_notification(self):
        self.publish()
        self.notify()
        self.bundle["documents"][0]["content"] = "# New content\n"
        self.cli.update_result = "partial_success"
        result = self.publish()
        self.assertFalse(result["all_delivered"])
        self.assertEqual(result["documents"][0]["state"], "partial")
        self.assertEqual(result["notification"]["state"], "blocked_documents")
        self.assertFalse(self.cli.sent)

    def test_user_notification_targets_only_current_owner_and_is_durable_deduplicated(self):
        self.notify()
        first = self.publish()
        self.assertTrue(first["all_delivered"])
        self.assertEqual(first["notification"]["state"], "sent")
        command = self.cli.sent[0]
        self.assertEqual(self.cli.flag(command, "--user-id"), self.cli.owner)
        self.assertEqual(self.cli.flag(command, "--as"), "user")
        self.assertLessEqual(len(self.cli.flag(command, "--idempotency-key")), 50)
        self.assertIn(first["documents"][0]["url"], self.cli.flag(command, "--text"))
        self.assertEqual(self.publish()["notification"]["state"], "unchanged")
        self.assertEqual(len(self.cli.sent), 1)

    def test_unknown_notification_is_not_retried_beyond_cli_dedup_window(self):
        self.notify()
        self.cli.message_timeout = True
        self.assertEqual(self.publish()["notification"]["state"], "unknown")
        self.assertEqual(self.publish()["notification"]["state"], "unknown")
        self.assertEqual(len(self.cli.sent), 1)

    def test_definite_permission_failure_can_retry_same_notification_key(self):
        self.notify()
        self.cli.message_failures = 1
        first = self.publish()
        self.assertEqual(first["notification"]["state"], "failed")
        self.assertFalse(first["all_delivered"])
        record = self.account()["notifications"][self.bundle["notification"]["key"]]
        original_key = record["idempotency_key"]
        self.assertEqual(self.publish()["notification"]["state"], "sent")
        record = self.account()["notifications"][self.bundle["notification"]["key"]]
        self.assertEqual(record["idempotency_key"], original_key)
        self.assertEqual([item["state"] for item in record["attempts"]], ["failed", "sent"])
        self.assertEqual(len(self.cli.sent), 1)

    def test_full_round_trip_verification_accepts_only_observed_markdown_format_changes(self):
        self.cli = TransformingCLI()
        doc = self.bundle["documents"][0]
        doc["content"] = ("# Synthetic report\n\nalpha\\_beta &amp; gamma\\.\n\n"
                          "| Item | Value |\n| --- | --- |\n| mode | active |\n\n"
                          "An exact final sentence.\n")
        self.cli.transform = lambda body: ("<title>" + doc["title"] + "</title>\n\n" +
                                           body.replace("\\_", "_").replace("\\.", ".")
                                           .replace("&amp;", "&").replace("| --- | --- |", "|-|-|")
                                           .replace("\n\n", "\n\n\n"))
        self.notify()
        self.assertTrue(self.publish()["all_delivered"])
        self.assertEqual(self.publish()["documents"][0]["state"], "unchanged")
        self.assertEqual(len(self.cli.sent), 1)

    def test_cli_auto_h1_title_accepts_h2_body_and_existing_h1_inputs(self):
        body = "## Outcomes\nAn exact result.\n\n## Problems\nA known issue.\n\n## Tomorrow\nContinue the task.\n"
        for heading in (None, "document_title", "Custom body heading"):
            with self.subTest(heading=heading), tempfile.TemporaryDirectory() as directory:
                bundle = copy.deepcopy(self.bundle)
                document = bundle["documents"][0]
                title = document["title"]
                source_heading = title if heading == "document_title" else heading
                document["content"] = ("# " + source_heading + "\n\n" if source_heading else "") + body
                cli = TransformingCLI()
                # Observed CLI behavior: add an H1 only if the source omitted
                # it. Its XML title metadata can be present in either case.
                cli.transform = lambda content: "<title>" + title + "</title>\n\n" + (
                    "# " + title + "\n\n" if source_heading is None else "") + content
                config = copy.deepcopy(self.config)
                config["integrations"]["feishu"]["delivery"]["send_self_enabled"] = True
                bundle["notification"] = {"key": "h1:verified", "text": "A verified summary is ready."}
                first = delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])
                self.assertTrue(first["all_delivered"])
                second = delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])
                self.assertEqual(second["documents"][0]["state"], "unchanged")
                self.assertEqual(len(cli.sent), 1)

    def test_cli_auto_h1_title_does_not_hide_wrong_titles_duplicates_or_body_loss(self):
        title = self.bundle["documents"][0]["title"]
        body = ("## Outcomes\nAn exact result.\n\n## Problems\nA known issue.\n\n"
                "## Tomorrow\nContinue the task.\n\n```markdown\n# " + title + "\n```\n")
        changes = {
            "wrong_title": lambda content: "# A different title\n\n" + content,
            "duplicate_title": lambda content: "# " + title + "\n\n# " + title + "\n\n" + content,
            "truncated_body": lambda content: "# " + title + "\n\n" + content[:len(content) // 2],
            "deleted_paragraph": lambda content: "# " + title + "\n\n" + content.replace("An exact result.\n", ""),
            "empty_body": lambda content: "# " + title + "\n",
            "changed_code_heading": lambda content: "# " + title + "\n\n" + content.replace(
                "```markdown\n# " + title, "```markdown\n# A changed code literal"),
        }
        for label, change in changes.items():
            with self.subTest(change=label), tempfile.TemporaryDirectory() as directory:
                bundle = copy.deepcopy(self.bundle)
                bundle["documents"][0]["content"] = body
                bundle["notification"] = {"key": "h1:unverified", "text": "A reviewed summary is ready."}
                config = copy.deepcopy(self.config)
                config["integrations"]["feishu"]["delivery"]["send_self_enabled"] = True
                cli = TransformingCLI()
                cli.transform = lambda content: "<title>" + title + "</title>\n\n" + change(content)
                result = delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])
                self.assertFalse(result["all_delivered"])
                self.assertEqual(result["documents"][0]["state"], "partial")
                self.assertEqual(result["notification"]["state"], "blocked_documents")
                self.assertFalse(cli.sent)

    def test_empty_truncated_wrong_and_one_character_changed_readbacks_never_deliver(self):
        original = ("# Full source\n\nCONTENT-MARKER\n\nA meaningful result and "
                    "https://example.test/result?id=42\n\nFinal evidence: exact.\n")
        mutations = {
            "empty": lambda body: "",
            "truncated": lambda body: body[:len(body) // 2],
            "wrong_with_marker": lambda body: "CONTENT-MARKER\nA completely different body.",
            "one_character": lambda body: body.replace("id=42", "id=43"),
        }
        for operation in ("create", "update"):
            for label, mutate in mutations.items():
                with self.subTest(operation=operation, corruption=label), tempfile.TemporaryDirectory() as directory:
                    cli = TransformingCLI()
                    bundle = copy.deepcopy(self.bundle)
                    bundle["documents"][0]["content"] = original
                    config = copy.deepcopy(self.config)
                    config["integrations"]["feishu"]["delivery"]["send_self_enabled"] = True
                    if operation == "update":
                        self.assertTrue(delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])["all_delivered"])
                        bundle["documents"][0]["content"] += "\nNew reviewed update.\n"
                    bundle["notification"] = {"key": "corruption:test", "text": "Reviewed result"}
                    cli.transform = mutate
                    result = delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])
                    self.assertFalse(result["all_delivered"])
                    self.assertEqual(result["documents"][0]["state"], "partial")
                    self.assertEqual(result["notification"]["state"], "blocked_documents")
                    self.assertFalse(cli.sent)

    def test_json_values_and_fenced_literals_are_compared_without_losing_escapes(self):
        payload = json.dumps({"result": "complete", "count": 42, "literal": "a\\!  b"})
        for fenced in (False, True):
            for corruption in (False, True):
                with self.subTest(fenced=fenced, corruption=corruption), tempfile.TemporaryDirectory() as directory:
                    cli = TransformingCLI()
                    bundle = copy.deepcopy(self.bundle)
                    bundle["documents"][0]["content"] = ("```json\n" + payload + "\n```\n") if fenced else payload
                    if corruption:
                        cli.transform = lambda body: body.replace('"count": 42', '"count": 43')
                    result = delivery.publish(directory, bundle, self.config, apply=True, runner=cli, command=["synthetic-cli"])
                    self.assertEqual(result["all_delivered"], not corruption)
        # A whitespace change inside a quoted JSON value is meaningful too.
        with tempfile.TemporaryDirectory() as directory:
            cli = TransformingCLI()
            cli.transform = lambda body: body.replace("a\\\\!  b", "a\\\\! b")
            bundle = copy.deepcopy(self.bundle)
            bundle["documents"][0]["content"] = payload
            self.assertFalse(delivery.publish(directory, bundle, self.config, apply=True, runner=cli, command=["synthetic-cli"])["all_delivered"])

    def test_inline_code_preserves_literal_backslashes_entities_and_spaces(self):
        source = "# Inline literals\n\nUse `C:\\\\workspace\\\\tool.exe` and `a  b &amp; c`. Also \\\\`x  y`.\n"
        mutations = (
            lambda body: body,
            lambda body: body.replace("`C:", "``C:").replace("tool.exe`", "tool.exe``"),
            lambda body: body.replace("\\\\workspace", "\\workspace"),
            lambda body: body.replace("a  b", "a b"),
            lambda body: body.replace("&amp;", "&"),
            lambda body: body.replace("x  y", "x y"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index), tempfile.TemporaryDirectory() as directory:
                cli = TransformingCLI()
                cli.transform = mutate
                bundle = copy.deepcopy(self.bundle)
                bundle["documents"][0]["content"] = source
                bundle["notification"] = {"key": "inline:test", "text": "Reviewed result"}
                config = copy.deepcopy(self.config)
                config["integrations"]["feishu"]["delivery"]["send_self_enabled"] = True
                result = delivery.publish(directory, bundle, config, apply=True, runner=cli, command=["synthetic-cli"])
                self.assertEqual(result["all_delivered"], index < 2)
                if index >= 2:
                    self.assertEqual(result["notification"]["state"], "blocked_documents")
                    self.assertFalse(cli.sent)

    def test_old_verified_corrupt_baseline_is_rechecked_against_full_reviewed_body(self):
        token = self.publish()["documents"][0]["token"]
        self.cli.documents[token]["content"] = ""
        path = self.root / "data" / "feishu-delivery" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        account = next(iter(manifest["accounts"].values()))
        record = account["documents"][self.bundle["documents"][0]["key"]]
        record["remote_hash"] = hashlib.sha256(b"").hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.notify()
        result = self.publish()
        self.assertFalse(result["all_delivered"])
        self.assertEqual(result["documents"][0]["state"], "partial")
        self.assertFalse(self.cli.sent)
        self.assertEqual(self.cli.count("+create"), 1)

    def test_applied_update_timeout_can_reconcile_complete_content_without_rewriting(self):
        self.publish()
        self.bundle["documents"][0]["content"] = "# Complete updated content\n"
        self.cli.update_timeout = True
        self.assertEqual(self.publish()["documents"][0]["state"], "unknown")
        self.assertTrue(self.publish()["all_delivered"])
        self.assertEqual(self.cli.count("+update"), 1)

    def test_disabled_notification_does_not_prevent_document_archive(self):
        self.bundle["notification"] = {"key": "pending:review", "text": "Synthetic reminder"}
        result = self.publish()
        self.assertEqual(result["documents"][0]["state"], "verified")
        self.assertEqual(result["notification"]["state"], "skipped_missing_permission")
        self.assertFalse(result["all_delivered"])
        self.assertFalse(self.cli.sent)

    def test_bot_sender_is_explicit_verified_and_never_used_for_documents(self):
        self.notify()
        self.settings.update(message_sender="bot", expected_bot_name="Personal Helper")
        result = self.publish()
        self.assertTrue(result["all_delivered"])
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--as"), "bot")
        self.assertEqual(self.cli.flag(self.cli.sent[0], "--user-id"), self.cli.owner)
        self.assertEqual(self.cli.count("/open-apis/bot/v3/info"), 1)
        doc_calls = [c for c in self.cli.calls if "docs" in c or "drive" in c or "whoami" in c]
        self.assertTrue(all(self.cli.flag(c, "--as") == "user" for c in doc_calls))

    def test_unexpected_bot_name_blocks_message_and_does_not_fallback(self):
        self.notify()
        self.settings.update(message_sender="bot", expected_bot_name="Another Helper")
        result = self.publish()
        self.assertEqual(result["notification"]["state"], "failed")
        self.assertFalse(self.cli.sent)
        self.assertEqual(result["documents"][0]["state"], "verified")

    def test_identity_owner_and_application_changes_cannot_reuse_manifest(self):
        self.settings["expected_owner_id"] = self.cli.owner
        self.publish()
        self.cli.owner = "ou_otherOwner"
        self.assertEqual(self.publish()["state"], "failed")
        self.settings.pop("expected_owner_id")
        with self.assertRaises(ValueError):
            self.publish()
        self.cli.owner = "ou_syntheticOwner"
        self.cli.app_id = "cli_differentApp"
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.cli.count("+create"), 1)

    def test_unconfirmed_user_identity_is_rejected_before_resource_writes(self):
        self.cli.identity_override = "bot"
        result = self.publish()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.cli.count("+create-folder"), 0)

    def test_failed_pagination_is_not_an_empty_folder_and_unmanaged_doc_is_not_overwritten(self):
        self.settings["folder_discovery"] = "list"
        self.bundle["documents"][0]["folder"] = []
        self.cli.list_incomplete = True
        self.assertEqual(self.publish()["documents"][0]["state"], "failed")
        self.assertEqual(self.cli.count("+create"), 0)
        self.cli.list_incomplete = False
        self.cli.folders["fld_root"].append({"name": self.bundle["documents"][0]["title"], "type": "docx", "token": "dox_other"})
        self.assertEqual(self.publish()["documents"][0]["state"], "conflict")
        self.assertEqual(self.cli.count("+update"), 0)

    def test_partial_document_failure_does_not_erase_other_successes(self):
        other = copy.deepcopy(self.bundle["documents"][0])
        other.update(key="day:2026-11-11:morning", title="Synthetic morning")
        self.bundle["documents"].append(other)
        self.cli.fetch_failures = 1
        result = self.publish()
        self.assertFalse(result["all_delivered"])
        self.assertEqual([d["state"] for d in result["documents"]], ["failed", "verified"])
        self.assertEqual(len(self.account()["documents"]), 2)

    def test_bundle_rejects_recipient_traversal_duplicate_keys_and_large_content(self):
        cases = []
        value = copy.deepcopy(self.bundle)
        value["notification"] = {"key": "n", "text": "x", "recipient": "ou_other"}
        cases.append(value)
        for segment in ("..", "a/b", "a\\b", "bad\x00"):
            value = copy.deepcopy(self.bundle)
            value["documents"][0]["folder"] = [segment]
            cases.append(value)
        value = copy.deepcopy(self.bundle)
        value["documents"].append(copy.deepcopy(value["documents"][0]))
        cases.append(value)
        value = copy.deepcopy(self.bundle)
        value["documents"][0]["content"] = "中" * (delivery.MAX_CONTENT_BYTES // 2)
        cases.append(value)
        for value in cases:
            with self.subTest(value_type=list(value)):
                with self.assertRaises(ValueError):
                    self.publish(value)
        self.assertFalse(self.cli.calls)

    def test_cli_default_preview_works_in_isolation(self):
        assistant.init(self.root)
        path = self.root / "data" / "synthetic-publish.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.bundle), encoding="utf-8")
        output = io.StringIO()
        with patch("feishu_delivery.find_cli", side_effect=AssertionError("preview cannot discover CLI")), contextlib.redirect_stdout(output):
            code = assistant.main(["--root", str(self.root), "publish-feishu", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "preview")


if __name__ == "__main__":
    unittest.main()
