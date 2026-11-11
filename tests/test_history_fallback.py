"""Synthetic CLI fixtures for the narrowly scoped, read-only history fallback."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "collect_feishu.py"
spec = importlib.util.spec_from_file_location("fallback_collector", MODULE)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

START, END = "2026-11-09T00:00:00+08:00", "2026-11-11T00:00:00+08:00"
MISSING_REACTIONS = {"ok": False, "identity": "user", "error": {
    "type": "authorization", "subtype": "missing_scope",
    "missing_scopes": ["im:message.reactions:read"],
    "hint": "SECRET_AUTH_HINT https://example.org/oauth/?code=SECRET"}}

FAKE = r'''
import json,sys
args=sys.argv[1:]
mode=__MODE__
with open(__TRACE__,'a',encoding='utf-8') as trace: trace.write(json.dumps(args)+'\n')
assert args[:2]==['--profile','codex-assistant']
assert args[-4:]==['--as','user','--format','json']
if '+messages-search' in args:
    if mode=='diagnostic_prefix': sys.stderr.write('Refreshing cached credentials...\n\x1b[33m')
    sys.stderr.write(json.dumps({'ok':False,'identity':'user','error':{
        'type':'authorization','subtype':'missing_scope',
        'missing_scopes':['im:message.reactions:read'],
        'hint':'SECRET_AUTH_HINT https://example.org/oauth/?code=SECRET'}}))
    if mode=='diagnostic_prefix': sys.stderr.write('\x1b[0m\n')
    sys.exit(3)
elif '+chat-list' in args:
    assert args[args.index('--types')+1]=='p2p,group'
    assert args[args.index('--sort')+1]=='active_time'
    assert '--exclude-muted' in args
    assert args[args.index('--page-limit')+1]=='1'
    if mode=='invalid_chat_list':
        print(json.dumps({'ok':True,'identity':'user','data':{}})); sys.exit(0)
    chats=[{'chat_id':'oc_Bot','chat_mode':'p2p','chat_status':'normal','p2p_target_type':'bot'},
           {'chat_id':'oc_Closed','chat_mode':'group','chat_status':'closed'},
           {'chat_id':'oc_bad;command','chat_mode':'group','chat_status':'normal'}]
    count=1 if mode=='oversize' else 2 if mode=='partial_failure' else 12
    for i in range(count):
        chat_id='oc_Synthetic'+str(i)
        chats.append({'chat_id':chat_id,'chat_mode':'group' if i%2 else 'p2p',
                      'p2p_target_type':'user','chat_status':'normal','name':'Synthetic chat '+str(i),
                      'chat_app_link':'https://applink.feishu.cn/client/chat/open?openChatId='+chat_id})
    print(json.dumps({'ok':True,'identity':'user','data':{'chats':chats,'has_more':False,'page_token':''},
                      'meta':{'pagination':{'complete':True,'pages':1,'items':len(chats)}}}))
elif 'api' in args:
    assert args[2:5]==['api','GET','/open-apis/im/v1/messages']
    params=json.loads(args[args.index('--params')+1])
    assert set(params)=={'container_id_type','container_id','start_time','end_time','sort_type','page_size'}
    assert params['container_id_type']=='chat'
    assert params['container_id'].startswith('oc_Synthetic')
    assert params['start_time']=='1794153600' and params['end_time']=='1794326400'
    assert params['sort_type']=='ByCreateTimeDesc' and 1<=params['page_size']<=20
    if mode=='oversize': sys.stdout.write('x'*10000); sys.exit(0)
    if mode=='all_failed' or (mode=='partial_failure' and params['container_id']=='oc_Synthetic0'):
        sys.stderr.write(json.dumps({'ok':False,'identity':'user','error':{'type':'api','code':999}})); sys.exit(1)
    if mode=='invalid_history':
        print(json.dumps({'ok':True,'identity':'user','data':{}})); sys.exit(0)
    if mode=='partial_failure':
        print(json.dumps({'ok':True,'identity':'user','data':{'items':None,'has_more':False,'page_token':''}})); sys.exit(0)
    records=[]
    for i in range(params['page_size']):
        mid='om_Synthetic'+('' if mode=='duplicates' else params['container_id'])+str(i)
        created=int(params['end_time'])*1000+1000 if mode=='out_of_range' else int(params['start_time'])*1000+1000+i
        records.append({'message_id':mid,'chat_id':params['container_id'],'create_time':str(created),
                        'msg_type':'text','sender':{'id':'ou_Synthetic'},
                        'body':{'content':json.dumps({'text':'Synthetic message'})},
                        'access_token':'SYNTHETIC_SECRET_TOKEN'})
    print(json.dumps({'ok':True,'identity':'user','data':{'items':records,'has_more':False,'page_token':''}}))
else:
    raise AssertionError('Unexpected or non-read command')
'''


class HistoryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.fake = self.root / "fake_history_cli.py"
        self.trace = self.root / "calls.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, mode="normal", **kwargs):
        self.fake.write_text(FAKE.replace("__MODE__", repr(mode)).replace("__TRACE__", repr(str(self.trace))), encoding="utf-8")
        return collector.collect_one("feishu_messages", collector.source_specs(START, END)["feishu_messages"],
                                     "codex-assistant", [sys.executable, str(self.fake)],
                                     kwargs.get("timeout_seconds", 45), kwargs.get("max_bytes", collector.MAX_BYTES))

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()]

    def test_fallback_reads_only_fixed_get_histories_with_existing_user_scopes(self):
        result = self.collect()
        self.assertEqual("partial", result["status"])
        self.assertEqual(100, result["count"])
        self.assertIn("受限活跃会话历史", result["scope"])
        self.assertIn("不是全量消息搜索", result["scope"])
        self.assertEqual(10, result["fallback"]["selected_chats"])
        self.assertEqual(5, result["fallback"]["successful_chats"])
        self.assertTrue(result["fallback"]["truncated"])
        items = result["data"]["data"]["items"]
        self.assertEqual(100, len({item["message_id"] for item in items}))
        self.assertTrue(all(item["chat_name"].startswith("Synthetic chat") for item in items))
        self.assertTrue(all(item["chat_app_link"].startswith("https://applink.feishu.cn/") for item in items))
        calls = self.calls()
        self.assertEqual(7, len(calls))
        for args in calls[2:]:
            self.assertEqual(["api", "GET", collector.HISTORY_PATH], args[2:5])
        self.assertNotIn("SECRET_AUTH_HINT", json.dumps(result))
        self.assertNotIn("SYNTHETIC_SECRET_TOKEN", json.dumps(result))
        self.assertNotIn("error", result)

    def test_only_one_exact_reaction_scope_failure_triggers_fallback(self):
        variants = [
            {"ok": False, "identity": "user", "error": {"type": "authorization", "subtype": "missing_scope", "missing_scopes": []}},
            {"ok": False, "identity": "user", "error": {"type": "authorization", "subtype": "missing_scope", "missing_scopes": [collector.REACTION_SCOPE, "another:scope"]}},
            {"ok": False, "identity": "bot", "error": MISSING_REACTIONS["error"]},
            {"ok": False, "identity": "user", "error": {"type": "api", "code": 500}},
        ]
        for failure in variants:
            result = {"returncode": 3, "stdout": b"", "stderr": json.dumps(failure).encode()}
            with self.subTest(failure=failure), mock.patch.object(collector, "run_bounded", return_value=result) as run:
                snapshot = collector.collect_one("feishu_messages", collector.source_specs(START, END)["feishu_messages"],
                                                 "codex-assistant", ["synthetic-cli"], 45, collector.MAX_BYTES)
                self.assertEqual("unavailable", snapshot["status"])
                self.assertEqual(1, run.call_count)
                self.assertNotIn("fallback", snapshot)
        canonical = {"returncode": 3, "stdout": b"", "stderr": json.dumps(MISSING_REACTIONS).encode()}
        with mock.patch.object(collector, "run_bounded", return_value=canonical) as run:
            snapshot = collector.collect_one("feishu_calendar", collector.source_specs(START, END)["feishu_calendar"],
                                             "codex-assistant", ["synthetic-cli"], 45, collector.MAX_BYTES)
            self.assertEqual(1, run.call_count)
            self.assertNotIn("fallback", snapshot)

    def test_diagnostic_prefix_keeps_exact_fallback_and_never_persists_stderr(self):
        snapshot = self.collect("diagnostic_prefix")
        self.assertEqual("partial", snapshot["status"])
        self.assertGreater(snapshot["fallback"]["successful_chats"], 0)
        persisted = json.dumps(snapshot)
        self.assertNotIn("Refreshing cached credentials", persisted)
        self.assertNotIn("SECRET_AUTH_HINT", persisted)

    def test_diagnostic_error_parser_rejects_ambiguous_or_non_error_json(self):
        encoded = json.dumps(MISSING_REACTIONS).encode()
        ambiguous = (
            encoded + b"\n" + encoded,
            b"[]\n" + encoded,
            b"[\n1, 2\n]\n" + encoded,
            b"{\"wrapper\":" + encoded + b"}",
            b"Diagnostic\n" + json.dumps({"ok": True, "identity": "user", "data": {}}).encode(),
            b"Diagnostic\n{\"ok\":false,\"identity\":\"bot\",\"identity\":\"user\",\"error\":{\"type\":\"authorization\"}}",
            b'{"ok":false,"identity":"bot","identity":"user","error":{"type":"authorization","subtype":"missing_scope","missing_scopes":["im:message.reactions:read"]}}',
        )
        for value in ambiguous:
            with self.subTest(value=value[:20]):
                parsed = collector.parse_cli_error_bytes(value)
                self.assertFalse(collector.only_reaction_scope_missing(parsed))
        decorated = b"[auth] Refreshing cached credentials...\n\x1b[33m" + encoded + b"\x1b[0m\n"
        self.assertTrue(collector.only_reaction_scope_missing(collector.parse_cli_error_bytes(decorated)))
        self.assertIsNone(collector.parse_json_bytes(decorated))
        # Error envelopes on stdout need the same ambiguity check, even when
        # a wrapper incorrectly reports a successful process exit code.
        duplicate_identity = ambiguous[-1]
        with mock.patch.object(collector, "run_bounded", return_value={
                "returncode": 0, "stdout": duplicate_identity, "stderr": b""}) as run:
            snapshot = collector.collect_one("feishu_messages", collector.source_specs(START, END)["feishu_messages"],
                                             "codex-assistant", ["synthetic-cli"], 45, collector.MAX_BYTES)
            self.assertEqual(1, run.call_count)
            self.assertNotIn("fallback", snapshot)

    def test_unknown_metadata_and_total_history_failure_are_unavailable(self):
        for mode in ("invalid_chat_list", "invalid_history", "all_failed"):
            with self.subTest(mode=mode):
                snapshot = self.collect(mode)
                self.assertEqual("unavailable", snapshot["status"])
                self.assertIsNone(snapshot["data"])
                self.assertIsNone(snapshot["count"])
                self.assertEqual(0, snapshot["fallback"]["successful_chats"])

    def test_confirmed_empty_history_and_another_failure_remain_partial(self):
        snapshot = self.collect("partial_failure")
        self.assertEqual(("partial", 0), (snapshot["status"], snapshot["count"]))
        self.assertEqual(1, snapshot["fallback"]["successful_chats"])
        self.assertEqual(1, snapshot["fallback"]["failed_chats"])

    def test_message_allowance_counts_examined_records_not_only_unique_results(self):
        snapshot = self.collect("duplicates")
        self.assertEqual(20, snapshot["count"])
        self.assertEqual(100, snapshot["fallback"]["messages_examined"])
        self.assertEqual(5, snapshot["fallback"]["successful_chats"])
        self.assertTrue(snapshot["fallback"]["truncated"])

    def test_history_items_outside_requested_window_are_not_returned(self):
        snapshot = self.collect("out_of_range")
        self.assertEqual(("partial", 0), (snapshot["status"], snapshot["count"]))
        self.assertEqual([], snapshot["data"]["data"]["items"])
        self.assertEqual(100, snapshot["fallback"]["messages_examined"])

    def test_all_calls_share_one_deadline_and_byte_budget(self):
        clock, calls = [100.0], []
        chat_data = {"ok": True, "identity": "user", "data": {"chats": [
            {"chat_id": "oc_Synthetic0", "chat_mode": "group", "chat_status": "normal"}], "has_more": False}}
        def run(command, timeout_seconds, max_bytes):
            calls.append((command, timeout_seconds, max_bytes))
            if timeout_seconds < 2:
                clock[0] += timeout_seconds
                return {"error": "timeout", "returncode": -1}
            clock[0] += 2
            if "+messages-search" in command:
                return {"returncode": 3, "stdout": b"", "stderr": json.dumps(MISSING_REACTIONS).encode()}
            return {"returncode": 0, "stdout": json.dumps(chat_data).encode(), "stderr": b""}
        with mock.patch.object(collector.time, "monotonic", side_effect=lambda: clock[0]), mock.patch.object(collector, "run_bounded", side_effect=run):
            snapshot = collector.collect_one("feishu_messages", collector.source_specs(START, END)["feishu_messages"],
                                             "codex-assistant", ["synthetic-cli"], 5, 2048)
        self.assertEqual(3, len(calls))
        self.assertEqual([5, 3, 1], [call[1] for call in calls])
        self.assertGreater(calls[0][2], calls[1][2])
        self.assertGreater(calls[1][2], calls[2][2])
        self.assertEqual(105.0, clock[0])
        self.assertEqual("timeout", snapshot["fallback"]["limit"])
        self.assertEqual("unavailable", snapshot["status"])

    def test_output_limit_is_shared_and_never_persists_truncated_history(self):
        snapshot = self.collect("oversize", max_bytes=2048)
        self.assertEqual("unavailable", snapshot["status"])
        self.assertIsNone(snapshot["data"])
        self.assertEqual("output_limit", snapshot["fallback"]["limit"])
        self.assertEqual(2048, snapshot["fallback"]["bytes_consumed"])
        self.assertEqual(3, len(self.calls()))


if __name__ == "__main__":
    unittest.main()
