"""Bounded read-only collection through the already-installed official Lark CLI.

This module never authenticates, reads credential stores, calls a model, or turns
untrusted source text into tasks. Codex reviews the snapshots separately.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 45
PAGE_LIMIT = 5
REACTION_SCOPE = "im:message.reactions:read"
HISTORY_PATH = "/open-apis/im/v1/messages"
FALLBACK_CHAT_LIMIT = 10
FALLBACK_MESSAGE_LIMIT = 100
SECRET_KEYS = {"access_token", "refresh_token", "tenant_access_token", "app_access_token", "user_access_token", "id_token", "app_secret", "client_secret", "authorization", "password", "verification_code", "otp"}
ITEM_KEYS = {"items", "messages", "tasks", "events", "message_list", "task_list", "event_list"}


def aware_datetime(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("采集起止时间必须为 ISO 日期时间") from exc
    if result.utcoffset() is None:
        raise ValueError("采集起止时间必须包含时区")
    return result


def native_candidates(shim, platform_name=None):
    """Locate npm's installed binary without executing its download-capable shim."""
    binary_name = "lark-cli.exe" if (platform_name or os.name) == "nt" else "lark-cli"
    shim = Path(shim)
    candidates = [shim.parent / "node_modules" / "@larksuite" / "cli" / "bin" / binary_name]
    resolved = shim.resolve()
    if resolved.name == "run.js" and resolved.parent.name == "scripts":
        # npm on Unix usually links <prefix>/bin/lark-cli into the package.
        candidates.append(resolved.parent.parent / "bin" / binary_name)
    # Also support a non-symlink shim under a conventional Unix npm prefix.
    candidates.append(shim.parent.parent / "lib" / "node_modules" / "@larksuite" / "cli" / "bin" / binary_name)
    return candidates


def find_cli():
    """Prefer the native binary; npm's JS launcher can auto-install software."""
    native = shutil.which("lark-cli.exe")
    if native:
        return [native]
    found = shutil.which("lark-cli.cmd") or shutil.which("lark-cli")
    if found:
        for candidate in native_candidates(found):
            if candidate.is_file():
                return [str(candidate)]
        if os.name != "nt" and Path(found).suffix not in {".js", ".cmd", ".ps1"}:
            # A globally installed Unix native CLI is allowed; reject npm JS.
            with Path(found).open("rb") as handle:
                header = handle.read(4)
            if header in {b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
                return [found]
    raise FileNotFoundError("未找到已经安装的官方 lark-cli 原生程序；不会自动安装或登录")


def source_specs(start, end):
    return {
        "feishu_messages": {
            "args": ["im", "+messages-search", "--query", "", "--start", start, "--end", end, "--no-reactions", "--page-all", "--page-limit", str(PAGE_LIMIT), "--page-size", "20"],
            "scope": f"用户身份跨会话消息搜索；{start} 至 {end}；最多 {PAGE_LIMIT} 页，每页20；仅可访问会话",
        },
        "feishu_calendar": {
            "args": ["calendar", "+agenda", "--start", start, "--end", end],
            "scope": f"本人主日历 agenda；{start} 至 {end}；不代表其他共享日历",
        },
        "feishu_tasks": {
            "args": ["task", "+get-my-tasks", "--complete=false", "--page-all", "--page-limit", str(PAGE_LIMIT)],
            "scope": f"本人未完成任务；最多 {PAGE_LIMIT} 页；不按采集起止时间过滤",
        },
        "feishu_mail": {
            "args": ["mail", "user_mailbox.messages", "list", "--user-mailbox-id", "me", "--folder-id", "INBOX", "--page-size", "20", "--page-all", "--page-limit", str(PAGE_LIMIT)],
            "scope": f"本人收件箱邮件消息列表（INBOX）；最多 {PAGE_LIMIT} 页，每页20；无日期过滤、未读取邮件正文，不代表其他文件夹",
        },
    }


def run_bounded(command, timeout_seconds=TIMEOUT_SECONDS, max_bytes=MAX_BYTES):
    """Bound combined stdout/stderr in memory and kill on excess or timeout."""
    if not 0 < timeout_seconds <= TIMEOUT_SECONDS or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("采集限制超过许可范围")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "shell": False}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(command, **kwargs)
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    lock = threading.Lock()
    exceeded = threading.Event()

    def read_pipe(name, pipe):
        try:
            while True:
                chunk = pipe.read(16384)
                if not chunk:
                    break
                with lock:
                    used = len(chunks["stdout"]) + len(chunks["stderr"])
                    available = max(0, max_bytes - used)
                    chunks[name].extend(chunk[:available])
                    if len(chunk) > available:
                        exceeded.set()
                        proc.kill()
                        break
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=read_pipe, args=(name, getattr(proc, name)), daemon=True) for name in chunks]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=3)
    finally:
        for reader in readers:
            reader.join(timeout=2)
        proc.stdout.close()
        proc.stderr.close()
    if timed_out:
        return {"error": "timeout", "returncode": proc.returncode}
    if exceeded.is_set():
        return {"error": "output_limit", "returncode": proc.returncode}
    return {"returncode": proc.returncode, "stdout": bytes(chunks["stdout"]), "stderr": bytes(chunks["stderr"])}


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if str(key).casefold() in SECRET_KEYS else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)\bhttps?://[^\s<>\"']*(?:access_token=|refresh_token=|[?&]code=|/oauth/|/verify)[^\s<>\"']*", "[REDACTED_AUTH_URL]", value)
        return value
    return value


def response_metadata(value, source_id=None):
    """Conservative pagination: explicit exhaustion plus recognizable items only."""
    flags, tokens, counts, page_counts = [], [], [], []
    envelope = value if isinstance(value, dict) else {}
    task_data = envelope.get("data")
    # lark-cli 1.0.99 returns an empty task page as items:null, not [].
    # Interpret that only in the observed successful user-task response shape;
    # a missing field or a null list in an unknown response is not an empty result.
    empty_task_page = (source_id == "feishu_tasks" and envelope.get("ok") is True
                       and envelope.get("identity") == "user" and isinstance(task_data, dict)
                       and "items" in task_data and task_data["items"] is None
                       and task_data.get("has_more") is False)
    def walk(item):
        if isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                lower = key.casefold()
                if lower in {"has_more", "hasmore"} and type(child) is bool:
                    flags.append(child)
                elif lower in {"page_token", "next_page_token", "nextpagetoken", "next_cursor", "next_page"} and child:
                    tokens.append(True)
                elif lower in {"pages_fetched", "page_count", "pages_count"} and type(child) is int:
                    page_counts.append(child)
                if lower in ITEM_KEYS and isinstance(child, list):
                    counts.append(len(child))
                    # Source records may contain arbitrary JSON; do not interpret
                    # their message text/attachments as collection metadata.
                elif lower == "items" and item is task_data and empty_task_page:
                    counts.append(0)
                elif lower == "data" and isinstance(child, list):
                    # The agenda shortcut returns {ok:true,identity:user,data:[],meta:{}}.
                    # Count its records, but do not invent pagination evidence or
                    # interpret fields inside event records as page metadata.
                    if (item is value and source_id == "feishu_calendar"
                            and envelope.get("ok") is True and envelope.get("identity") == "user"):
                        counts.append(len(child))
                elif isinstance(child, (dict, list)):
                    walk(child)
    walk(value)
    count = sum(counts) if counts else None
    exhausted = bool(flags) and all(flag is False for flag in flags) and not tokens
    limited = any(flag is True for flag in flags) or bool(tokens) or any(p >= PAGE_LIMIT for p in page_counts)
    status = "ok" if exhausted and not limited and count is not None else "partial"
    reason = "分页元数据明确耗尽" if status == "ok" else "仍有后续页或已到页限" if limited else "未能从响应确认分页耗尽及完整结构"
    return {"status": status, "count": count, "reason": reason, "has_more": any(flags) if flags else None}


def parse_json_bytes(value):
    try:
        return json.loads(value.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def safe_error_metadata(value):
    """Keep only short ASCII error classifiers, never messages or raw stderr."""
    if not isinstance(value, dict):
        return {}
    error = value.get("error")
    error = error if isinstance(error, dict) else {}
    result = {}
    for name in ("type", "subtype", "code"):
        field = error.get(name, value.get(name))
        if type(field) not in (str, int):
            continue
        field = str(field)
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", field):
            result[name] = field
    return result


class ReadBudget:
    """Share one source's deadline and byte allowance across all CLI calls."""
    def __init__(self, command, timeout_seconds, max_bytes):
        if not 0 < timeout_seconds <= TIMEOUT_SECONDS or not 0 < max_bytes <= MAX_BYTES:
            raise ValueError("采集限制超过许可范围")
        self.command = command
        self.deadline = time.monotonic() + timeout_seconds
        self.max_bytes = max_bytes
        self.remaining_bytes = max_bytes
        self.limit = None

    def run(self, args):
        remaining_time = self.deadline - time.monotonic()
        if self.limit or remaining_time <= 0 or self.remaining_bytes <= 0:
            self.limit = self.limit or ("timeout" if remaining_time <= 0 else "output_limit")
            return {"error": self.limit, "returncode": None}
        result = run_bounded([*self.command, *args], min(remaining_time, TIMEOUT_SECONDS), self.remaining_bytes)
        self.remaining_bytes -= len(result.get("stdout", b"")) + len(result.get("stderr", b""))
        if result.get("error") in {"timeout", "output_limit"}:
            self.limit = result["error"]
            if self.limit == "output_limit":
                self.remaining_bytes = 0
        return result


def only_reaction_scope_missing(value):
    """This fallback never handles other missing scopes or unknown failures."""
    if not isinstance(value, dict) or value.get("ok") is not False or value.get("identity") != "user":
        return False
    error = value.get("error")
    return (isinstance(error, dict) and error.get("type") == "authorization"
            and error.get("subtype") == "missing_scope"
            and error.get("missing_scopes") == [REACTION_SCOPE])


def successful_user_data(result):
    if result.get("error") or result.get("returncode") != 0:
        return None
    envelope = parse_json_bytes(result.get("stdout", b""))
    if not isinstance(envelope, dict) or envelope.get("ok") is not True or envelope.get("identity") != "user":
        return None
    return envelope.get("data")


def history_chat_info(chat):
    if not isinstance(chat, dict) or chat.get("chat_status") != "normal":
        return None
    mode = chat.get("chat_mode")
    if mode != "group" and not (mode == "p2p" and chat.get("p2p_target_type") == "user"):
        return None
    chat_id = chat.get("chat_id")
    if not isinstance(chat_id, str) or not re.fullmatch(r"oc_[A-Za-z0-9]+", chat_id):
        return None
    name = chat.get("name")
    name = re.sub(r"[\x00-\x1f]", " ", name)[:240] if isinstance(name, str) else chat_id
    link = chat.get("chat_app_link")
    try:
        parsed = urlsplit(link) if isinstance(link, str) and len(link) <= 2048 else None
        trusted_link = (parsed and parsed.scheme == "https"
                        and parsed.hostname in {"applink.feishu.cn", "applink.larksuite.com"}
                        and parsed.username is None and parsed.password is None
                        and parsed.path == "/client/chat/open"
                        and parse_qs(parsed.query).get("openChatId") == [chat_id])
    except ValueError:
        trusted_link = False
    return {"chat_id": chat_id, "chat_name": name, "chat_app_link": link if trusted_link else None}


def collect_active_chat_history(snapshot, spec, profile, budget):
    """Read bounded active histories with existing scopes, never reaction APIs."""
    start = spec["args"][spec["args"].index("--start") + 1]
    end = spec["args"][spec["args"].index("--end") + 1]
    start_seconds, end_seconds = str(int(aware_datetime(start).timestamp())), str(int(aware_datetime(end).timestamp()))
    snapshot["scope"] = (f"受限活跃会话历史；{start} 至 {end}；最多{FALLBACK_CHAT_LIMIT}个正常非机器人会话、"
                         f"每会话首20条、总计最多{FALLBACK_MESSAGE_LIMIT}条；不是全量消息搜索")
    meta = {"mode": "active_chat_history", "reason": "missing_reaction_scope",
            "selected_chats": 0, "successful_chats": 0, "failed_chats": 0,
            "truncated": False, "errors": []}
    snapshot["fallback"] = meta
    items, seen, examined = [], set(), 0

    def call(args):
        return budget.run(["--profile", profile, *args, "--as", "user", "--format", "json"])

    listed = call(["im", "+chat-list", "--types", "p2p,group", "--sort", "active_time",
                   "--exclude-muted", "--page-size", "20", "--page-all", "--page-limit", "1"])
    chat_data = successful_user_data(listed)
    if not isinstance(chat_data, dict) or not isinstance(chat_data.get("chats"), list) or type(chat_data.get("has_more")) is not bool:
        meta["errors"].append({"stage": "chat_list", "error": listed.get("error", "invalid_or_failed_user_response")})
        selected = []
    else:
        candidates, chat_ids = [], set()
        for raw in chat_data["chats"]:
            chat = history_chat_info(raw)
            if chat and chat["chat_id"] not in chat_ids:
                chat_ids.add(chat["chat_id"])
                candidates.append(chat)
        selected = candidates[:FALLBACK_CHAT_LIMIT]
        meta["truncated"] = (len(candidates) > FALLBACK_CHAT_LIMIT or chat_data["has_more"]
                             or bool(chat_data.get("page_token")))
    meta["selected_chats"] = len(selected)
    for chat in selected:
        if examined >= FALLBACK_MESSAGE_LIMIT or budget.limit:
            meta["truncated"] = True
            break
        page_size = min(20, FALLBACK_MESSAGE_LIMIT - examined)
        params = {"container_id_type": "chat", "container_id": chat["chat_id"],
                  "start_time": start_seconds, "end_time": end_seconds,
                  "sort_type": "ByCreateTimeDesc", "page_size": page_size}
        result = call(["api", "GET", HISTORY_PATH, "--params", json.dumps(params, separators=(",", ":"))])
        data = successful_user_data(result)
        valid = isinstance(data, dict) and type(data.get("has_more")) is bool and "items" in data
        if valid:
            records = data["items"]
            if records is None and data["has_more"] is False:
                records = []
            valid = isinstance(records, list)
        if not valid:
            meta["failed_chats"] += 1
            error = {"chat_id": chat["chat_id"], "error": result.get("error", "invalid_or_failed_user_response")}
            classifiers = safe_error_metadata(parse_json_bytes(result.get("stderr", b"")))
            if classifiers:
                error["classification"] = classifiers
            meta["errors"].append(error)
            if budget.limit:
                meta["truncated"] = True
                break
            continue
        meta["successful_chats"] += 1
        examined += min(len(records), page_size)
        meta["truncated"] |= data["has_more"] or bool(data.get("page_token")) or len(records) > page_size
        for record in records[:page_size]:
            if not isinstance(record, dict):
                meta["truncated"] = True
                continue
            mid, created = record.get("message_id"), record.get("create_time")
            record_chat_id = record.get("chat_id")
            if (not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", mid)
                    or not re.fullmatch(r"[0-9]{1,20}", str(created))
                    or (record_chat_id is not None and record_chat_id != chat["chat_id"])):
                meta["truncated"] = True
                continue
            if not int(start_seconds) * 1000 <= int(created) < int(end_seconds) * 1000:
                continue
            if mid not in seen:
                seen.add(mid)
                items.append({**record, **chat})
    if meta["successful_chats"] + meta["failed_chats"] < len(selected):
        meta["truncated"] = True
    meta["bytes_consumed"] = budget.max_bytes - budget.remaining_bytes
    meta["messages_examined"] = examined
    if budget.limit:
        meta["limit"] = budget.limit
    if meta["successful_chats"]:
        meta["original_error_metadata"] = snapshot.pop("error_metadata", {})
        snapshot.pop("error", None)
        snapshot.update(status="partial", count=len(items), has_more=None,
                        data=redact({"ok": True, "identity": "user", "data": {"items": items},
                                     "meta": {"collection_mode": "active_chat_history"}}),
                        details=f"受限会话历史读取成功{meta['successful_chats']}个、失败{meta['failed_chats']}个，取得{len(items)}条；未读取反应，不是全量搜索")
    else:
        snapshot.update(status="unavailable", count=None, data=None,
                        details="受限会话历史未取得任何可确认成功的历史响应；未扩权、未切换机器人身份")
    return snapshot


def collect_one(source_id, spec, profile, command, timeout_seconds, max_bytes):
    args = ["--profile", profile, *spec["args"], "--as", "user", "--format", "json"]
    snapshot = {"source": source_id, "scope": spec["scope"], "command_args": args, "data": None, "count": None}
    try:
        budget = ReadBudget(command, timeout_seconds, max_bytes)
        result = budget.run(args)
        if result.get("error"):
            reason = "超过45秒采集时限" if result["error"] == "timeout" else "响应超过字节上限，未保存截断内容"
            snapshot.update(status="unavailable", error=result["error"], details=reason)
        else:
            parsed = parse_json_bytes(result["stdout"])
            api_code = parsed.get("code") if isinstance(parsed, dict) else None
            api_error = isinstance(parsed, dict) and (parsed.get("ok") is False or bool(parsed.get("error")) or (api_code is not None and api_code not in (0, "0")))
            if result["returncode"] != 0 or api_error:
                # Do not persist raw CLI error output, which could contain an
                # authentication URL or credential debugging information.
                classifiers = safe_error_metadata(parsed)
                if result["returncode"] != 0:
                    classifiers.update(safe_error_metadata(parse_json_bytes(result["stderr"])))
                labels = "; ".join(f"{name}={value}" for name, value in classifiers.items()) or "unknown"
                snapshot.update(status="unavailable", error="cli_failed", error_metadata=classifiers,
                                details=f"CLI exit={result['returncode']}；错误分类 {labels}；未认证或权限等错误请由用户处理，不自动登录")
                failure = parse_json_bytes(result.get("stderr", b"")) if result["returncode"] != 0 else parsed
                if source_id == "feishu_messages" and only_reaction_scope_missing(failure):
                    snapshot = collect_active_chat_history(snapshot, spec, profile, budget)
            elif parsed is None:
                snapshot.update(status="unavailable", error="invalid_json", details="CLI 未返回有效 UTF-8 JSON；未把未知输出当作空结果")
            else:
                meta = response_metadata(parsed, source_id)
                snapshot.update(status=meta["status"], count=meta["count"], details=meta["reason"], has_more=meta["has_more"], data=redact(parsed))
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot.update(status="unavailable", error="cli_unavailable", details="CLI 无法启动或读取失败；未读取认证信息，不切换机器人身份")
    snapshot["observed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return snapshot


def collect(root, start, end, profile="codex-assistant", *, command=None, timeout_seconds=TIMEOUT_SECONDS, max_bytes=MAX_BYTES):
    begin, finish = aware_datetime(start), aware_datetime(end)
    if begin >= finish:
        raise ValueError("采集结束时间必须晚于开始时间")
    if (finish - begin).total_seconds() > 31 * 86400:
        raise ValueError("单次消息和日历采集范围最多31天")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
        raise ValueError("profile 仅允许字母数字下划线和连字符")
    start, end = begin.isoformat(), finish.isoformat()
    specs = source_specs(start, end)
    try:
        cli = command if command is not None else find_cli()
        if not isinstance(cli, list) or not cli or any(not isinstance(arg, str) for arg in cli):
            raise ValueError("CLI command 必须是非空参数数组")
        unavailable = None
    except OSError:
        cli, unavailable = None, "未找到已安装的官方 CLI；未自动安装或认证"
    snapshots = {}
    if unavailable:
        for sid, spec in specs.items():
            snapshots[sid] = {"source": sid, "status": "unavailable", "scope": spec["scope"], "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "details": unavailable, "count": None, "data": None}
    else:
        with ThreadPoolExecutor(max_workers=4) as executor:
            jobs = {executor.submit(collect_one, sid, spec, profile, cli, timeout_seconds, max_bytes): sid for sid, spec in specs.items()}
            for future in as_completed(jobs):
                snapshots[jobs[future]] = future.result()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = Path(root) / "data" / "feishu" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    sources, summary = [], []
    for sid in specs:
        snapshot = snapshots[sid]
        path = directory / f"{sid}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sources.append({"id": sid, "status": snapshot["status"], "observed_at": snapshot["observed_at"], "scope": snapshot["scope"], "details": snapshot["details"]})
        summary.append({"id": sid, "status": snapshot["status"], "count": snapshot["count"], "snapshot": str(path), "details": snapshot["details"]})
    envelope_path = directory / "source-envelope.json"
    envelope_path.write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"directory": str(directory), "source_envelope": str(envelope_path), "sources": summary, "note": "仅采集只读原始资料与覆盖状态；待办、目标、日程须由 Codex 核实后另行提炼导入。未进行身份认证。"}
