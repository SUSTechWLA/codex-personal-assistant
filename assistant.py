"""Local ledger and planner with optional read-only Feishu CLI collection. No model API."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CONFIG = {
    "timezone": "Asia/Shanghai",
    "workdays": [1, 2, 3, 4, 5],
    "work_windows": [["09:00", "12:00"], ["13:00", "18:00"]],
    "capacity_ratio": 0.7,
    "min_confidence": 0.8,
    "source_freshness_hours": 24,
    "required_sources": ["codex_activity", "feishu_messages", "feishu_calendar", "feishu_tasks", "feishu_mail", "gmail", "google_calendar"],
    "profile": {"name": None, "aliases": ["self"]},
    "integrations": {
        "feishu": {
            "profile": "codex-assistant",
            "authorization_status": "not_configured",
            "grant_mode": "read_only",
        }
    },
}
TASK_STATUSES = {"todo", "waiting", "done", "cancelled"}
SOURCE_STATUSES = {"ok", "partial", "unavailable"}
TABLES = ("sources", "goals", "tasks", "events")


class ValidationError(ValueError):
    pass


def parse_dt(value, label="datetime"):
    if not isinstance(value, str):
        raise ValidationError(f"{label} 必须是带时区的 ISO 日期时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} 日期时间无效") from exc
    if parsed.utcoffset() is None:
        raise ValidationError(f"{label} 缺少时区；请明确 +08:00 或 Z")
    return parsed


def text_field(item, name, *, optional=False, max_len=6000, multiline=True):
    value = item.get(name)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ValidationError(f"{name} 必须为 1–{max_len} 字符的文本")
    if any(ord(c) < 32 and (not multiline or c not in "\n\t") for c in value):
        raise ValidationError(f"{name} 含不允许的控制字符")
    return value.strip()


def identifier(item, name="id"):
    value = text_field(item, name, max_len=256, multiline=False)
    if not re.fullmatch(r"[\w.:@/-]+", value, flags=re.UNICODE):
        raise ValidationError(f"{name} 只允许字母、数字、下划线及 . : @ / -")
    return value


def bounded_number(value, name, lo, hi, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{name} 必须是有限数字")
    if not lo <= value <= hi or (integer and int(value) != value):
        raise ValidationError(f"{name} 必须在 {lo}–{hi} 内" + ("且为整数" if integer else ""))
    return int(value) if integer else value


def get_tz(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        # Windows standard-library Python may ship without IANA data.
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), "Asia/Shanghai")
        if name in {"UTC", "Etc/UTC"}:
            return timezone.utc
        raise ValidationError(f"本 Python 缺少 {name} 时区数据") from exc


def load_config(root):
    path = root / "config.json"
    if not path.exists():
        raise ValidationError("请先运行 init")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    get_tz(config["timezone"])
    days = config.get("workdays")
    if not isinstance(days, list) or not days or any(type(d) is not int or not 1 <= d <= 7 for d in days):
        raise ValidationError("workdays 应为 ISO 星期数字 1–7 的非空数组")
    windows = config.get("work_windows")
    if not isinstance(windows, list) or not windows:
        raise ValidationError("work_windows 应为非空时间段数组")
    previous = None
    for window in windows:
        if not isinstance(window, list) or len(window) != 2:
            raise ValidationError("work_windows 每项应为 [HH:MM,HH:MM]")
        try:
            begin, end = [time.fromisoformat(s) for s in window]
        except (ValueError, TypeError) as exc:
            raise ValidationError("工作时段格式无效") from exc
        if begin.tzinfo or end.tzinfo or begin >= end or (previous and begin < previous):
            raise ValidationError("工作时段必须按顺序且无重叠，不跨日")
        previous = end
    bounded_number(config.get("capacity_ratio"), "capacity_ratio", 0.1, 1)
    bounded_number(config.get("min_confidence"), "min_confidence", 0, 1)
    bounded_number(config.get("source_freshness_hours"), "source_freshness_hours", 0.1, 8760)
    aliases = config.get("profile", {}).get("aliases")
    if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
        raise ValidationError("profile.aliases 应为本人称呼数组")
    required = config.get("required_sources")
    if not isinstance(required, list) or any(not isinstance(s, str) or not s.strip() for s in required):
        raise ValidationError("required_sources 应为来源 ID 数组")
    return config


def connect(root):
    conn = sqlite3.connect(root / "assistant.sqlite3", timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    for table in TABLES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS history (sequence INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, record_id TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.commit()
    return conn


def init(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = load_config(root)
    with closing(connect(root)):
        pass
    return {"root": str(root), "database": str(root / "assistant.sqlite3"), "config": config}


def validate_record(table, raw):
    if not isinstance(raw, dict):
        raise ValidationError(f"{table} 每项必须是对象")
    common = {"id", "title"}
    extra = {
        "sources": {"id", "status", "observed_at", "scope", "details"},
        "goals": common | {"status", "evidence", "target_date"},
        "tasks": common | {"source", "source_ref", "evidence", "owner", "status", "confidence", "importance", "estimate_minutes", "due_at", "goal_id", "reopen"},
        "events": common | {"source", "source_ref", "start", "end", "status", "transparency"},
    }[table]
    unknown = set(raw) - extra
    if unknown:
        raise ValidationError(f"{table} 未知字段: {', '.join(sorted(unknown))}")
    result = {"id": identifier(raw)}
    if table == "sources":
        if raw.get("status") not in SOURCE_STATUSES:
            raise ValidationError("source.status 必须是 ok/partial/unavailable")
        result.update(status=raw["status"], observed_at=parse_dt(raw.get("observed_at"), "observed_at").isoformat(),
                      scope=text_field(raw, "scope"), details=text_field(raw, "details", optional=True))
        return result
    result["title"] = text_field(raw, "title", max_len=240, multiline=False)
    if table == "goals":
        if raw.get("status") not in {"active", "paused", "done", "cancelled"}:
            raise ValidationError("goal.status 必须是 active/paused/done/cancelled")
        result.update(status=raw["status"], evidence=text_field(raw, "evidence", optional=True))
        if raw.get("target_date"):
            result["target_date"] = date.fromisoformat(raw["target_date"]).isoformat()
        return result
    result["source"] = identifier(raw, "source")
    if table == "events":
        start, end = parse_dt(raw.get("start"), "start"), parse_dt(raw.get("end"), "end")
        if start >= end:
            raise ValidationError("event.end 必须晚于 start")
        status = raw.get("status", "confirmed")
        transparency = raw.get("transparency", "opaque")
        if status not in {"confirmed", "tentative", "cancelled"} or transparency not in {"opaque", "transparent"}:
            raise ValidationError("event.status/transparency 无效")
        result.update(start=start.isoformat(), end=end.isoformat(), status=status, transparency=transparency,
                      source_ref=text_field(raw, "source_ref", optional=True, max_len=2000))
        return result
    if raw.get("status") not in TASK_STATUSES:
        raise ValidationError("task.status 必须是 todo/waiting/done/cancelled")
    if "reopen" in raw and type(raw["reopen"]) is not bool:
        raise ValidationError("task.reopen 必须是布尔值")
    result.update(status=raw["status"], source_ref=text_field(raw, "source_ref", max_len=2000),
                  evidence=text_field(raw, "evidence"), owner=text_field(raw, "owner", optional=True, max_len=100, multiline=False),
                  confidence=bounded_number(raw.get("confidence"), "confidence", 0, 1),
                  importance=bounded_number(raw.get("importance"), "importance", 1, 5, True),
                  estimate_minutes=bounded_number(raw.get("estimate_minutes"), "estimate_minutes", 5, 4800, True),
                  due_at=parse_dt(raw["due_at"], "due_at").isoformat() if raw.get("due_at") else None,
                  goal_id=identifier(raw, "goal_id") if raw.get("goal_id") else None)
    return result


def read_table(conn, table):
    return [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {table} ORDER BY id")]


def ingest(root, envelope):
    load_config(root)
    if not isinstance(envelope, dict):
        raise ValidationError("导入文件必须是 JSON 对象")
    if set(envelope) - set(TABLES) - {"calendar_snapshots"}:
        raise ValidationError("导入只允许 sources/goals/tasks/events/calendar_snapshots")
    validated = {}
    for table in TABLES:
        records = envelope.get(table, [])
        if not isinstance(records, list):
            raise ValidationError(f"{table} 必须是数组")
        validated[table] = [validate_record(table, r) for r in records]
        ids = [r["id"] for r in validated[table]]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"{table} 同一次导入含重复 ID")
    snapshots = []
    incoming_sources = {record["id"]: record for record in validated["sources"]}
    for snapshot in envelope.get("calendar_snapshots", []):
        if not isinstance(snapshot, dict) or set(snapshot) != {"source", "start", "end", "complete"} or snapshot["complete"] is not True:
            raise ValidationError("calendar_snapshots 每项须 source/start/end/complete:true；部分读取不得替换")
        start, end = parse_dt(snapshot["start"]), parse_dt(snapshot["end"])
        if start >= end:
            raise ValidationError("calendar_snapshot.end 必须晚于 start")
        snapshot_source = identifier(snapshot, "source")
        # A historical successful read cannot certify this replacement. A
        # complete snapshot must accompany a successful read in this batch;
        # its nonempty scope is validated with the other source fields above.
        current_source = incoming_sources.get(snapshot_source)
        if not current_source or current_source["status"] != "ok":
            raise ValidationError("完整日历快照必须附带本批次 status=ok 的对应来源覆盖记录；读取失败或部分覆盖时保留旧日程")
        snapshots.append((snapshot_source, start, end))
    now = datetime.now(timezone.utc).isoformat()
    changes = {table: 0 for table in TABLES}
    removed = 0
    raw_tasks = {identifier(record): record for record in envelope.get("tasks", [])}
    with closing(connect(root)) as conn, conn:
        known_sources = {r["id"] for r in read_table(conn, "sources")} | {r["id"] for r in validated["sources"]}
        known_goals = {r["id"] for r in read_table(conn, "goals")} | {r["id"] for r in validated["goals"]}
        for record in validated["tasks"] + validated["events"]:
            if record["source"] not in known_sources:
                raise ValidationError(f"未知 source: {record['source']}；先提供来源覆盖记录")
        for record in validated["tasks"]:
            if record["goal_id"] and record["goal_id"] not in known_goals:
                raise ValidationError(f"未知 goal_id: {record['goal_id']}")
        incoming_event_ids = {r["id"] for r in validated["events"]}
        for source, begin, end in snapshots:
            if source not in known_sources:
                raise ValidationError(f"未知 calendar snapshot source: {source}")
            for event in read_table(conn, "events"):
                if event["source"] == source and event["id"] not in incoming_event_ids and parse_dt(event["start"]) < end and parse_dt(event["end"]) > begin:
                    conn.execute("DELETE FROM events WHERE id=?", (event["id"],))
                    conn.execute("INSERT INTO history(table_name,record_id,observed_at,payload) VALUES(?,?,?,?)", ("events", event["id"], now, json.dumps({"removed_by_complete_snapshot": event}, ensure_ascii=False)))
                    removed += 1
        for table in TABLES:
            for record in validated[table]:
                prior_row = conn.execute(f"SELECT payload FROM {table} WHERE id=?", (record["id"],)).fetchone()
                if prior_row and table == "tasks":
                    prior = json.loads(prior_row[0])
                    raw = raw_tasks[record["id"]]
                    if prior["status"] in {"done", "cancelled"} and record["status"] in {"todo", "waiting"} and not raw.get("reopen"):
                        record["status"] = prior["status"]
                payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
                if prior_row and prior_row[0] == payload:
                    continue
                conn.execute(f"INSERT INTO {table}(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (record["id"], payload))
                conn.execute("INSERT INTO history(table_name,record_id,observed_at,payload) VALUES(?,?,?,?)", (table, record["id"], now, payload))
                changes[table] += 1
    return {"changed": changes, "removed_calendar_events": removed}


def status(root):
    config = load_config(root)
    with closing(connect(root)) as conn:
        data = {table: read_table(conn, table) for table in TABLES}
        data["history_count"] = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    return {"config": config, **data}


def update_status(root, task_id, new_status):
    if new_status not in TASK_STATUSES:
        raise ValidationError("无效任务状态")
    load_config(root)
    with closing(connect(root)) as conn, conn:
        row = conn.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValidationError(f"没有任务 {task_id}")
        task = json.loads(row[0])
        task["status"] = new_status
        payload = json.dumps(task, ensure_ascii=False, sort_keys=True)
        conn.execute("UPDATE tasks SET payload=? WHERE id=?", (payload, task_id))
        conn.execute("INSERT INTO history(table_name,record_id,observed_at,payload) VALUES(?,?,?,?)", ("tasks", task_id, datetime.now(timezone.utc).isoformat(), payload))
    return task


def subtract_busy(windows, busy):
    result = list(windows)
    for busy_start, busy_end in sorted(busy):
        next_windows = []
        for begin, end in result:
            if busy_end <= begin or busy_start >= end:
                next_windows.append((begin, end))
            else:
                if begin < busy_start:
                    next_windows.append((begin, busy_start))
                if busy_end < end:
                    next_windows.append((busy_end, end))
        result = next_windows
    return result


def round_up(dt, minutes=5):
    seconds = int(dt.timestamp())
    rounded = ((seconds + minutes * 60 - 1) // (minutes * 60)) * minutes * 60
    if dt.microsecond and rounded == seconds:
        rounded += minutes * 60
    return datetime.fromtimestamp(rounded, dt.tzinfo)


def task_score(task, now, goals):
    score = task["importance"] * 10
    if task["goal_id"] and goals.get(task["goal_id"], {}).get("status") == "active":
        score += 12
    if task["due_at"]:
        hours = (parse_dt(task["due_at"]) - now).total_seconds() / 3600
        score += 45 if hours < 0 else 35 if hours <= 24 else 25 if hours <= 48 else 12 if hours <= 168 else 0
    return score


def build_plan(data, plan_date, now):
    config = data["config"]
    tz = get_tz(config["timezone"])
    now = now.astimezone(tz)
    day_start = datetime.combine(plan_date, time.min, tz)
    day_end = day_start + timedelta(days=1)
    windows = []
    if plan_date.isoweekday() in config["workdays"]:
        for begin, end in config["work_windows"]:
            start = max(datetime.combine(plan_date, time.fromisoformat(begin), tz), round_up(now))
            finish = datetime.combine(plan_date, time.fromisoformat(end), tz)
            if start < finish:
                windows.append((start, finish))
    events = [event for event in data["events"] if event["status"] != "cancelled" and event["transparency"] != "transparent" and parse_dt(event["start"]) < day_end and parse_dt(event["end"]) > day_start]
    free = subtract_busy(windows, [(parse_dt(e["start"]).astimezone(tz), parse_dt(e["end"]).astimezone(tz)) for e in events])
    free = [(round_up(s), e) for s, e in free if round_up(s) < e]
    free_minutes = sum(int((end - begin).total_seconds() // 60) for begin, end in free)
    budget = int(free_minutes * config["capacity_ratio"] // 5) * 5
    goals = {g["id"]: g for g in data["goals"]}
    aliases = {a.casefold() for a in config["profile"]["aliases"]}
    eligible, uncertain, waiting = [], [], []
    for task in data["tasks"]:
        if task["status"] == "waiting":
            waiting.append(task)
        elif task["status"] == "todo":
            if not task["owner"] or task["owner"].casefold() not in aliases or task["confidence"] < config["min_confidence"]:
                uncertain.append(task)
            else:
                eligible.append(task)
    eligible.sort(key=lambda t: (-task_score(t, now, goals), parse_dt(t["due_at"]) if t["due_at"] else datetime.max.replace(tzinfo=timezone.utc), t["id"]))
    # Protect feasible deadlines on the planned day before filling the rest
    # by importance/goal score. Earliest-deadline ordering is feasible for
    # these splittable tasks with a common start and the same free windows.
    # An already impossible task must not consume the time needed by another
    # task whose deadline can still be met.
    deadlines = sorted(
        [(parse_dt(t["due_at"]).astimezone(tz), t) for t in eligible
         if t["due_at"] and day_start <= parse_dt(t["due_at"]).astimezone(tz) < day_end
         and parse_dt(t["due_at"]) >= now],
        key=lambda pair: (pair[0], -task_score(pair[1], now, goals), pair[1]["id"]),
    )
    protected, committed = [], 0
    for due, task in deadlines:
        available_before_due = sum(
            max(0, int((min(end, due) - start).total_seconds() // 300) * 5)
            for start, end in free
        )
        needed = int(math.ceil(task["estimate_minutes"] / 5) * 5)
        if committed + needed <= min(budget, available_before_due):
            protected.append(task)
            committed += needed
    protected_ids = {task["id"] for task in protected}
    eligible = protected + [task for task in eligible if task["id"] not in protected_ids]
    blocks, remaining = [], {}
    slots = list(free)
    for task in eligible:
        needed = int(math.ceil(task["estimate_minutes"] / 5) * 5)
        for i, (start, end) in enumerate(slots):
            available = int((end - start).total_seconds() // 300) * 5
            while needed > 0 and available >= 5 and budget >= 5:
                chunk = min(needed, available, budget, 90)
                finish = start + timedelta(minutes=chunk)
                blocks.append({"task_id": task["id"], "title": task["title"], "start": start, "end": finish, "source_ref": task["source_ref"]})
                needed -= chunk
                available -= chunk
                budget -= chunk
                start = finish
            slots[i] = (start, end)
        if needed:
            remaining[task["id"]] = needed
    risks = []
    for task in eligible:
        if not task["due_at"]:
            continue
        due = parse_dt(task["due_at"]).astimezone(tz)
        task_blocks = [b for b in blocks if b["task_id"] == task["id"]]
        if due < now:
            risks.append((task, "已过明确截止时间；需确认是否改期"))
        elif due < day_end and (task["id"] in remaining or not task_blocks or max(b["end"] for b in task_blocks) > due):
            risks.append((task, "按当前容量无法在截止时间前完成；建议缩小范围或协商改期"))
    source_warnings = []
    sources = {s["id"]: s for s in data["sources"]}
    for sid in config["required_sources"]:
        source = sources.get(sid)
        if not source:
            source_warnings.append(f"{sid}：尚未读取")
        elif source["status"] != "ok":
            source_warnings.append(f"{sid}：{'部分覆盖' if source['status'] == 'partial' else '不可用'}，{source['scope']}")
        elif (now - parse_dt(source["observed_at"])).total_seconds() > config["source_freshness_hours"] * 3600:
            source_warnings.append(f"{sid}：读取已过期，请刷新")
        elif parse_dt(source["observed_at"]) > now + timedelta(minutes=5):
            source_warnings.append(f"{sid}：采集时间晚于当前时间，请核实时钟")
    return {"date": plan_date, "now": now, "blocks": blocks, "events": events, "eligible": eligible, "uncertain": uncertain, "waiting": waiting,
            "remaining": remaining, "risks": risks, "free_minutes": free_minutes, "scheduled_minutes": sum(int((b["end"] - b["start"]).total_seconds() // 60) for b in blocks),
            "source_warnings": source_warnings, "sources": data["sources"], "goals": data["goals"], "config": config}


def md(value):
    value = html.escape(str(value), quote=False)
    value = value.replace("\r", " ").replace("\n", " / ").replace("\t", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def render_markdown(plan):
    tz = get_tz(plan["config"]["timezone"])
    lines = [f"# {plan['date']} 工作计划", "", f"生成时间：{plan['now'].isoformat(timespec='minutes')}。所有时间为 {plan['config']['timezone']}。", "",
             "本地建议草案；未创建或变更在线日历。仅依据已读取内容，来源盲区见末尾。", "", "## 三项重点", ""]
    for index, task in enumerate(plan["eligible"][:3], 1):
        due = parse_dt(task["due_at"]).astimezone(tz).isoformat(timespec="minutes") if task["due_at"] else "未给出"
        lines.extend([f"{index}. **{md(task['title'])}**（重要性 {task['importance']}/5，估时 {task['estimate_minutes']} 分钟；截止 {md(due)}）", f"   依据：{md(task['evidence'])}；来源：{md(task['source_ref'])}；ID：{md(task['id'])}"])
    if not plan["eligible"]:
        lines.append("暂无有明确本人责任且置信度足够的待办；请先核实来源或责任人。")
    lines.extend(["", "## 时间安排", "", f"剩余工作空闲 {plan['free_minutes']} 分钟；安排 {plan['scheduled_minutes']} 分钟，容量上限为可用时间的 {plan['config']['capacity_ratio']:.0%}。", "", "| 时间 | 安排 | 类型 |", "| --- | --- | --- |"])
    entries = [(parse_dt(e["start"]).astimezone(tz), parse_dt(e["end"]).astimezone(tz), e["title"], "已有日程") for e in plan["events"]]
    entries += [(b["start"], b["end"], b["title"], "建议专注") for b in plan["blocks"]]
    for begin, end, title, kind in sorted(entries):
        begin_text = begin.strftime("%H:%M") if begin.date() == plan["date"] else begin.strftime("%m-%d %H:%M")
        end_text = end.strftime("%H:%M") if end.date() == plan["date"] else end.strftime("%m-%d %H:%M")
        lines.append(f"| {begin_text}–{end_text} | {md(title)} | {kind} |")
    if not entries:
        lines.append("| — | 无已读取的日程或可安排的时间块 | — |")
    lines.extend(["", "## 延期风险与未安排工作", ""])
    for task, reason in plan["risks"]:
        lines.append(f"- {md(task['title'])}：{md(reason)}。")
    for task in plan["eligible"]:
        if task["id"] in plan["remaining"]:
            lines.append(f"- {md(task['title'])}：尚余 {plan['remaining'][task['id']]} 分钟未安排，未自动挤占缓冲时间。")
    if not plan["risks"] and not plan["remaining"]:
        lines.append("已读取的明确待办中，未发现排程造成的延期风险；这不代表未读取内容没有风险。")
    lines.extend(["", "## 等待与待确认", ""])
    for task in plan["waiting"]:
        lines.append(f"- 等待：{md(task['title'])}。跟进依据：{md(task['evidence'])}；{md(task['source_ref'])}。")
    for task in plan["uncertain"]:
        reason = "责任人未确认或非本人" if not task["owner"] or task["owner"].casefold() not in {a.casefold() for a in plan["config"]["profile"]["aliases"]} else "提取置信度不足"
        lines.append(f"- 待确认：{md(task['title'])}（{reason}，置信度 {task['confidence']:.0%}）。依据：{md(task['evidence'])}；{md(task['source_ref'])}。")
    if not plan["waiting"] and not plan["uncertain"]:
        lines.append("暂无已记录的等待项或待确认项。")
    lines.extend(["", "## 目标与主动建议", ""])
    active = [g for g in plan["goals"] if g["status"] == "active"]
    for goal in active:
        linked = sum(1 for task in plan["eligible"] if task["goal_id"] == goal["id"])
        lines.append(f"- {md(goal['title'])}：当前有 {linked} 项明确待办关联。")
    if not active:
        lines.append("- 尚未确认长期目标。建议明确本周最重要的 1–3 个结果，再调整优先级。")
    if plan["source_warnings"]:
        lines.append("- 优先补齐下列来源盲区，避免把未读取的信息当作没有工作。")
    if plan["waiting"]:
        lines.append("- 对等待项准备一次集中跟进；未自动发送催办消息。")
    if plan["remaining"]:
        lines.append("- 工作量超出剩余容量：先缩减低优先级范围，再决定改期。")
    lines.extend(["", "## 来源覆盖", "", "| 来源 | 状态 | 最近读取 | 范围 |", "| --- | --- | --- | --- |"])
    for source in sorted(plan["sources"], key=lambda s: s["id"]):
        label = {"ok": "已读取所述范围", "partial": "部分覆盖", "unavailable": "不可用"}[source["status"]]
        lines.append(f"| {md(source['id'])} | {label} | {md(source['observed_at'])} | {md(source['scope'])} |")
    lines.extend(["", *[f"- {md(warning)}" for warning in plan["source_warnings"]], "", "日历冲突检查仅涵盖已读取并导入的日程。ICS 只包含本助理建议时间块，不包含已有会议。", ""])
    return "\n".join(lines)


def ics_text(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_ics_line(line):
    # RFC 5545 counts octets, not Unicode code points.
    chunks, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            chunks.append(current)
            current = " " + char
        else:
            current += char
    chunks.append(current)
    return "\r\n".join(chunks)


def render_ics(plan):
    stamp = plan["now"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Codex Personal Assistant//Local Draft//ZH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Codex 工作计划（建议草案）"]
    for block in plan["blocks"]:
        key = f"{plan['date']}|{block['task_id']}|{block['start'].isoformat()}"
        uid = hashlib.sha256(key.encode()).hexdigest()[:32] + "@codex-local-assistant"
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                      "DTSTART:" + block["start"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                      "DTEND:" + block["end"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                      "SUMMARY:" + ics_text("[建议] " + block["title"]),
                      "DESCRIPTION:" + ics_text("本地计划草案。来源：" + block["source_ref"]), "STATUS:TENTATIVE", "TRANSP:OPAQUE", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def write_plan(root, plan_date, now):
    data = status(root)
    plan = build_plan(data, plan_date, now)
    report = root / "reports" / (plan_date.isoformat() + ".md")
    calendar = root / "plans" / (plan_date.isoformat() + ".ics")
    report.parent.mkdir(parents=True, exist_ok=True)
    calendar.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(plan), encoding="utf-8")
    calendar.write_bytes(render_ics(plan).encode("utf-8"))
    return {"report": str(report), "calendar_draft": str(calendar), "scheduled_minutes": plan["scheduled_minutes"], "blocks": len(plan["blocks"]), "coverage_warnings": plan["source_warnings"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地个人助理账本与排程；可通过官方飞书 CLI 只读采集，不调用 AI API")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    import_cmd = commands.add_parser("ingest")
    import_cmd.add_argument("file", type=Path)
    update_cmd = commands.add_parser("update")
    update_cmd.add_argument("id")
    update_cmd.add_argument("--status", required=True, choices=sorted(TASK_STATUSES))
    plan_cmd = commands.add_parser("plan")
    plan_cmd.add_argument("--date", type=date.fromisoformat)
    plan_cmd.add_argument("--now", type=parse_dt)
    journal_cmd = commands.add_parser("log-work", help="导入由 Codex 核实的当日工作事实与建议，不修改任务状态")
    journal_cmd.add_argument("file", type=Path)
    review_cmd = commands.add_parser("daily-review", help="生成有来源的全天工作日报和建议")
    review_cmd.add_argument("--date", type=date.fromisoformat)
    review_cmd.add_argument("--now", type=parse_dt)
    publish_cmd = commands.add_parser("publish-feishu", help="预览或发布已审阅资料到本人飞书专属云空间；默认不触网")
    publish_cmd.add_argument("file", type=Path)
    publish_cmd.add_argument("--apply", action="store_true", help="执行已配置的本人文档写入和可选通知")
    collect_cmd = commands.add_parser("collect-feishu", help="使用已授权的官方 CLI 只读采集飞书，不登录或切换机器人身份")
    collect_cmd.add_argument("--start", required=True, type=parse_dt)
    collect_cmd.add_argument("--end", required=True, type=parse_dt)
    collect_cmd.add_argument("--profile", default="codex-assistant")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "init":
            result = init(root)
        elif args.command == "status":
            result = status(root)
        elif args.command == "ingest":
            result = ingest(root, json.loads(args.file.read_text(encoding="utf-8-sig")))
        elif args.command == "update":
            result = update_status(root, args.id, args.status)
        elif args.command == "log-work":
            from daily_review import log_work
            result = log_work(root, json.loads(args.file.read_text(encoding="utf-8-sig")), load_config(root))
        elif args.command == "daily-review":
            from daily_review import write_review
            data = status(root)
            now = args.now or datetime.now(timezone.utc)
            result = write_review(root, args.date or now.astimezone(get_tz(data["config"]["timezone"])).date(), now, data)
        elif args.command == "publish-feishu":
            from feishu_delivery import publish
            result = publish(root, json.loads(args.file.read_text(encoding="utf-8-sig")), load_config(root), apply=args.apply)
        elif args.command == "collect-feishu":
            load_config(root)
            from collect_feishu import collect
            result = collect(root, args.start.isoformat(), args.end.isoformat(), args.profile)
            result["coverage_ingest"] = ingest(root, json.loads(Path(result["source_envelope"]).read_text(encoding="utf-8")))
        else:
            config = load_config(root)
            now = args.now or datetime.now(timezone.utc)
            result = write_plan(root, args.date or now.astimezone(get_tz(config["timezone"])).date(), now)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
