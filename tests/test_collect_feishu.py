"""Fake subprocess fixtures prove collection behavior without live user data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "collect_feishu.py"
spec = importlib.util.spec_from_file_location("collect_feishu", MODULE)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

FAKE = r'''
import json,sys,time
args=sys.argv[1:]
assert args[args.index('--as')+1]=='user'
assert args[args.index('--profile')+1]=='codex-assistant'
assert '--format' in args
if 'im' in args:
    assert args[args.index('--query')+1]==''
    assert args[args.index('--page-limit')+1]=='5'
    print(json.dumps({'data':{'items':[{'id':'m1','text':'hello'}],'has_more':False},'access_token':'secret-not-for-disk'}))
elif 'calendar' in args:
    print(json.dumps({'data':{'events':[]}}))
elif 'task' in args:
    assert '--complete=false' in args
    print(json.dumps({'data':{'tasks':[{'id':'t1'}],'has_more':True,'next_page_token':'more'}}))
elif 'mail' in args:
    assert args[args.index('--user-mailbox-id')+1]=='me'
    assert args[args.index('--folder-id')+1]=='INBOX'
    assert args[args.index('--page-size')+1]=='20'
    print(json.dumps({'code':9999,'error':'Unauthorized https://example.org/oauth/?code=secret-auth-code'}))
    sys.exit(1)
else: sys.exit(5)
'''


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Compare the same canonical paths used by native CLI discovery even
        # when a Windows runner's TEMP contains an 8.3 directory alias.
        self.root = Path(self.tmp.name).resolve()
        self.fake = self.root / "fake_cli.py"
        self.fake.write_text(FAKE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, **kwargs):
        return collector.collect(self.root, "2026-11-09T00:00:00+08:00", "2026-11-11T00:00:00+08:00", command=[sys.executable, str(self.fake)], **kwargs)

    def test_native_cli_discovery_uses_installed_npm_binary_without_running_shim(self):
        shim = self.root / "npm-prefix" / "lark-cli.cmd"
        shim.parent.mkdir()
        shim.write_text("must not execute this shim", encoding="utf-8")
        native = collector.native_candidates(shim)[0]
        native.parent.mkdir(parents=True)
        native.write_bytes(b"installed-test-binary")
        with mock.patch.object(collector.shutil, "which", side_effect=lambda name: str(shim) if name == "lark-cli.cmd" else None):
            self.assertEqual([str(native)], collector.find_cli())

    def test_unix_npm_script_and_prefix_layouts_are_supported(self):
        installed_script = self.root / "lib" / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
        installed_script.parent.mkdir(parents=True)
        installed_script.write_text("must not execute this shim", encoding="utf-8")
        package_binary = installed_script.parent.parent / "bin" / "lark-cli"
        self.assertIn(package_binary, collector.native_candidates(installed_script, "posix"))
        prefix_shim = self.root / "bin" / "lark-cli"
        self.assertIn(package_binary, collector.native_candidates(prefix_shim, "posix"))

    def test_sources_fail_independently_and_only_exhausted_pages_are_ok(self):
        result = self.collect()
        sources = {s["id"]: s for s in result["sources"]}
        self.assertEqual("ok", sources["feishu_messages"]["status"])
        self.assertEqual(1, sources["feishu_messages"]["count"])
        self.assertEqual("partial", sources["feishu_calendar"]["status"])
        self.assertEqual(0, sources["feishu_calendar"]["count"])
        self.assertEqual("partial", sources["feishu_tasks"]["status"])
        self.assertEqual("unavailable", sources["feishu_mail"]["status"])
        self.assertIsNone(sources["feishu_mail"]["count"])
        envelope = json.loads(Path(result["source_envelope"]).read_text(encoding="utf-8"))
        self.assertEqual({"sources"}, set(envelope))
        self.assertEqual(4, len(envelope["sources"]))
        for source in envelope["sources"]:
            self.assertIsNotNone(collector.aware_datetime(source["observed_at"]).utcoffset())
        persisted = "".join(p.read_text(encoding="utf-8") for p in Path(result["directory"]).glob("*.json"))
        self.assertNotIn("secret-not-for-disk", persisted)
        self.assertNotIn("secret-auth-code", persisted)
        self.assertIn("REDACTED", persisted)

    def test_collection_timeout_and_byte_limit_are_bounded(self):
        self.fake.write_text("import time; time.sleep(3)", encoding="utf-8")
        result = collector.run_bounded([sys.executable, str(self.fake)], timeout_seconds=.15)
        self.assertEqual("timeout", result["error"])
        self.fake.write_text("import sys; sys.stdout.write('x'*100000); sys.stderr.write('y'*100000)", encoding="utf-8")
        result = collector.run_bounded([sys.executable, str(self.fake)], max_bytes=1024)
        self.assertEqual("output_limit", result["error"])
        self.assertNotIn("stdout", result)

    def test_unknown_json_and_invalid_json_are_not_silent_empty_success(self):
        self.fake.write_text("print('{}')", encoding="utf-8")
        result = self.collect()
        self.assertEqual({"partial"}, {s["status"] for s in result["sources"]})
        self.fake.write_text("print('not json')", encoding="utf-8")
        result = self.collect()
        self.assertEqual({"unavailable"}, {s["status"] for s in result["sources"]})

    def test_pagination_limit_and_nonempty_cursor_remain_partial(self):
        metadata = collector.response_metadata({"data": {"items": [], "has_more": False}, "pages_fetched": 5})
        self.assertEqual("partial", metadata["status"])
        metadata = collector.response_metadata({"items": [], "has_more": False, "next_cursor": "x"})
        self.assertEqual("partial", metadata["status"])
        metadata = collector.response_metadata({"data": {"items": [], "has_more": False}})
        self.assertEqual("ok", metadata["status"])

    def test_agenda_shortcut_array_is_counted_without_claiming_full_coverage(self):
        envelope = {"ok": True, "identity": "user", "data": [], "meta": {}}
        metadata = collector.response_metadata(envelope, "feishu_calendar")
        self.assertEqual(0, metadata["count"])
        self.assertEqual("partial", metadata["status"])
        envelope["data"] = [{"id": "synthetic-event", "has_more": False,
                             "items": [], "description": "untrusted event content"}]
        metadata = collector.response_metadata(envelope, "feishu_calendar")
        self.assertEqual(1, metadata["count"])
        self.assertIsNone(metadata["has_more"])
        self.assertEqual("partial", metadata["status"])

    def test_empty_nullable_task_page_is_complete_only_with_explicit_exhaustion(self):
        envelope = {"ok": True, "identity": "user", "data": {
            "has_more": False, "items": None, "page_token": ""}}
        metadata = collector.response_metadata(envelope, "feishu_tasks")
        self.assertEqual(0, metadata["count"])
        self.assertEqual("ok", metadata["status"])
        envelope["data"]["page_token"] = "next-synthetic-page"
        self.assertEqual("partial", collector.response_metadata(envelope, "feishu_tasks")["status"])
        envelope["data"] = {"has_more": False}
        metadata = collector.response_metadata(envelope, "feishu_tasks")
        self.assertIsNone(metadata["count"])
        self.assertEqual("partial", metadata["status"])
        envelope["data"] = {"items": None, "has_more": True}
        metadata = collector.response_metadata(envelope, "feishu_tasks")
        self.assertIsNone(metadata["count"])
        self.assertEqual("partial", metadata["status"])

    def test_shortcut_shapes_are_normalized_in_persisted_collection_results(self):
        self.fake.write_text(
            "import json,sys\n"
            "args=sys.argv[1:]\n"
            "payload=[] if 'calendar' in args else {'items':None,'has_more':False,'page_token':''}\n"
            "print(json.dumps({'ok':True,'identity':'user','data':payload,'meta':{}}))\n",
            encoding="utf-8")
        sources = {s["id"]: s for s in self.collect()["sources"]}
        self.assertEqual((0, "partial"), (sources["feishu_calendar"]["count"], sources["feishu_calendar"]["status"]))
        self.assertEqual((0, "ok"), (sources["feishu_tasks"]["count"], sources["feishu_tasks"]["status"]))

    def test_mail_collection_selects_required_inbox_folder(self):
        self.fake.write_text(
            "import json,sys\n"
            "args=sys.argv[1:]\n"
            "if 'mail' in args and ('--folder-id' not in args or args[args.index('--folder-id')+1]!='INBOX'):\n"
            "    sys.exit(1)\n"
            "print(json.dumps({'ok':True,'identity':'user','data':{'items':[],'has_more':False}}))\n",
            encoding="utf-8")
        source = next(s for s in self.collect()["sources"] if s["id"] == "feishu_mail")
        self.assertEqual("ok", source["status"])
        self.assertEqual(0, source["count"])

    def test_range_and_profile_reject_shell_characters_before_running(self):
        for profile in ["bad & command", "bad;command", "a\ncommand"]:
            with self.assertRaises(ValueError):
                collector.collect(self.root, "2026-11-09T00:00:00+08:00", "2026-11-11T00:00:00+08:00", profile, command=[sys.executable, str(self.fake)])
        for start, end in [("2026-11-09T00:00:00", "2026-11-11T00:00:00+08:00"), ("2026-11-11T00:00:00+08:00", "2026-11-09T00:00:00+08:00"), ("2026-01-01T00:00:00+08:00", "2026-12-31T00:00:00+08:00")]:
            with self.assertRaises(ValueError):
                collector.collect(self.root, start, end, command=[sys.executable, str(self.fake)])

    def test_raw_snapshot_does_not_change_task_or_event_records(self):
        self.collect()
        self.assertFalse((self.root / "assistant.sqlite3").exists())
        self.assertFalse((self.root / "config.json").exists())
        persisted = list((self.root / "data" / "feishu").glob("*/*.json"))
        self.assertEqual(5, len(persisted))

    def test_nonzero_stderr_records_only_safe_error_classifiers(self):
        stderr = {"ok": False, "error": {"type": "auth_error", "subtype": "missing_user_token", "code": 99991663,
                  "message": "secret-error-message https://example.org/oauth/?code=secret-code", "access_token": "secret-access-token"}}
        self.fake.write_text("import json,sys\nsys.stderr.write(json.dumps(" + repr(stderr) + "))\nsys.exit(3)", encoding="utf-8")
        result = self.collect()
        for source in result["sources"]:
            self.assertEqual("unavailable", source["status"])
            snapshot = json.loads(Path(source["snapshot"]).read_text(encoding="utf-8"))
            self.assertEqual({"type": "auth_error", "subtype": "missing_user_token", "code": "99991663"}, snapshot["error_metadata"])
            self.assertIsNone(snapshot["data"])
        persisted = "".join(p.read_text(encoding="utf-8") for p in Path(result["directory"]).glob("*.json"))
        self.assertNotIn("secret-", persisted)
        self.assertNotIn("example.org", persisted)
        self.assertIn("missing_user_token", persisted)
        self.assertEqual({}, collector.safe_error_metadata({"error": {"type": "https://example.org", "subtype": "bad\nfield", "code": "a" * 65}}))

    def test_ok_false_without_error_is_unavailable_even_with_exhausted_items(self):
        self.fake.write_text("import json; print(json.dumps({'ok':False,'data':{'items':[],'has_more':False}}))", encoding="utf-8")
        result = self.collect()
        self.assertEqual({"unavailable"}, {source["status"] for source in result["sources"]})
        self.assertTrue(all(source["count"] is None for source in result["sources"]))


if __name__ == "__main__":
    unittest.main()
