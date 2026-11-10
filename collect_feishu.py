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

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 45
PAGE_LIMIT = 5
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
            "args": ["mail", "user_mailbox.messages", "list", "--user-mailbox-id", "me", "--page-size", "20", "--page-all", "--page-limit", str(PAGE_LIMIT)],
            "scope": f"本人邮箱消息列表；最多 {PAGE_LIMIT} 页，每页20；无日期过滤、未读取邮件正文",
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


def response_metadata(value):
    """Conservative pagination: explicit exhaustion plus recognizable items only."""
    flags, tokens, counts, page_counts = [], [], [], []
    def walk(item):
        if isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                lower = key.casefold()
                if lower in {"has_more", "hasmore"} and type(child) is bool:
                    flags.append(child)
                elif lower in {"next_page_token", "nextpagetoken", "next_cursor", "next_page"} and child:
                    tokens.append(True)
                elif lower in {"pages_fetched", "page_count", "pages_count"} and type(child) is int:
                    page_counts.append(child)
                if lower in ITEM_KEYS and isinstance(child, list):
                    counts.append(len(child))
                    # Source records may contain arbitrary JSON; do not interpret
                    # their message text/attachments as collection metadata.
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


def collect_one(source_id, spec, profile, command, timeout_seconds, max_bytes):
    args = ["--profile", profile, *spec["args"], "--as", "user", "--format", "json"]
    snapshot = {"source": source_id, "scope": spec["scope"], "command_args": args, "data": None, "count": None}
    try:
        result = run_bounded([*command, *args], timeout_seconds, max_bytes)
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
            elif parsed is None:
                snapshot.update(status="unavailable", error="invalid_json", details="CLI 未返回有效 UTF-8 JSON；未把未知输出当作空结果")
            else:
                meta = response_metadata(parsed)
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
