"""Evidence-backed work journals and daily reviews; no collection or model calls.

Codex verifies source material and supplies the structured envelope. This module
validates and stores those observations; metadata alone cannot prove delivery.
Journal facts and recommendations never update the task or goal ledger.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ENTRY_STATUSES = {"completed", "in_progress", "blocked", "cancelled"}
COMPLETION_BASES = {"artifact", "test", "commit", "user_confirmation"}
CATEGORIES = {"next_day", "next_week", "learning", "risk", "focus"}
SCHEMA_VERSION = 1


def _text(item, name, *, optional=False, maximum=6000, multiline=True):
    value = item.get(name)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    if any(ord(c) < 32 and (not multiline or c not in "\r\n\t") for c in value):
        raise ValueError(f"{name} contains control characters")
    return value.strip()


def _id(item, name="id"):
    value = _text(item, name, maximum=256, multiline=False)
    if not re.fullmatch(r"[\w.:@/-]+", value):
        raise ValueError(f"{name} is not a valid identifier")
    return value


def _object(raw, allowed, label):
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    extra = set(raw) - set(allowed)
    if extra:
        raise ValueError(f"unknown {label} fields: {', '.join(sorted(extra))}")


def _list(raw, label, maximum):
    if not isinstance(raw, list) or len(raw) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} items")
    return raw


def _dt(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timezone-aware ISO datetime")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a timezone-aware ISO datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return result


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _tz(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # The Windows embedded runtime does not always bundle the IANA database.
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        if name in {"UTC", "Etc/UTC"}:
            return timezone.utc
        raise ValueError(f"timezone is unavailable: {name}") from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _journal_path(root):
    return Path(root) / "data" / "journal.sqlite3"


def _connect(root):
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY, work_date TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS entries_by_date ON entries(work_date);
        CREATE TABLE IF NOT EXISTS coverage (
            work_date TEXT NOT NULL, source TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (work_date, source));
        CREATE TABLE IF NOT EXISTS recommendations (
            work_date TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (work_date, id));
        CREATE TABLE IF NOT EXISTS recommendation_state (
            work_date TEXT PRIMARY KEY, observed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS weekly_runs (
            week_start TEXT PRIMARY KEY, payload TEXT NOT NULL);
    """)
    db.commit()
    return db


def _validate(envelope, config):
    _object(envelope, {"date", "observed_at", "coverage", "entries", "recommendations"}, "journal")
    day = _day(envelope.get("date"))
    observed = _dt(envelope.get("observed_at"), "observed_at")
    tz = _tz(config["timezone"])
    if observed.astimezone(tz).date() < day:
        raise ValueError("observed_at cannot precede the work date")
    coverage = []
    for raw in _list(envelope.get("coverage", []), "coverage", 100):
        _object(raw, {"source", "status", "scope", "source_ref"}, "coverage")
        item = {"source": _id(raw, "source"), "status": _text(raw, "status", multiline=False),
                "scope": _text(raw, "scope"), "source_ref": _text(raw, "source_ref", maximum=2000, multiline=False)}
        if item["status"] not in {"ok", "partial", "unavailable"}:
            raise ValueError("invalid coverage status")
        coverage.append(item)
    entries = []
    for raw in _list(envelope.get("entries", []), "entries", 2000):
        _object(raw, {"id", "title", "status", "occurred_at", "source", "source_ref", "evidence",
                      "outcome", "project", "task_id", "completion_basis"}, "entry")
        item = {"id": _id(raw), "title": _text(raw, "title", maximum=1000),
                "status": _text(raw, "status", multiline=False), "source": _id(raw, "source"),
                "source_ref": _text(raw, "source_ref", maximum=2000, multiline=False),
                "evidence": _text(raw, "evidence")}
        if item["status"] not in ENTRY_STATUSES:
            raise ValueError("invalid work entry status")
        occurred = _dt(raw.get("occurred_at"), "occurred_at")
        if occurred.astimezone(tz).date() != day:
            raise ValueError("occurred_at must belong to the work date in the configured timezone")
        if occurred > observed:
            raise ValueError("occurred_at cannot be later than observed_at")
        item["occurred_at"] = occurred.isoformat()
        for field in ("outcome", "project", "task_id", "completion_basis"):
            value = _text(raw, field, optional=True, maximum=6000 if field == "outcome" else 1000,
                          multiline=field in {"outcome", "project"})
            if value is not None:
                item[field] = value
        if "task_id" in item:
            item["task_id"] = _id(raw, "task_id")
        if "completion_basis" in item and item["completion_basis"] not in COMPLETION_BASES:
            raise ValueError("completion_basis must be artifact, test, commit or user_confirmation")
        if item["status"] == "completed" and not (item.get("outcome") and item.get("completion_basis")):
            raise ValueError("completed entries need outcome and completion_basis in addition to evidence and source_ref")
        entries.append(item)
    recommendations = None
    if "recommendations" in envelope:
        recommendations = []
        for raw in _list(envelope["recommendations"], "recommendations", 100):
            _object(raw, {"id", "category", "title", "why", "small_action", "source_ref", "priority"}, "recommendation")
            item = {"id": _id(raw), "category": _text(raw, "category", multiline=False),
                    "title": _text(raw, "title", maximum=1000), "why": _text(raw, "why"),
                    "small_action": _text(raw, "small_action"),
                    "source_ref": _text(raw, "source_ref", maximum=2000, multiline=False)}
            if item["category"] not in CATEGORIES:
                raise ValueError("invalid recommendation category")
            priority = raw.get("priority", 3)
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
                raise ValueError("recommendation priority must be an integer from 1 to 5")
            item["priority"] = priority
            recommendations.append(item)
    for label, rows, key in (("coverage", coverage, "source"), ("entries", entries, "id"),
                              ("recommendations", recommendations or [], "id")):
        if len({item[key] for item in rows}) != len(rows):
            raise ValueError(f"duplicate IDs in {label}")
    return day.isoformat(), observed.isoformat(), coverage, entries, recommendations


def log_work(root, envelope, config):
    """Atomically upsert verified facts; an explicit recommendations list replaces that day only."""
    day, observed, coverage, entries, recommendations = _validate(envelope, config)
    changes = {"entries": 0, "coverage": 0, "recommendations": 0}
    with closing(_connect(root)) as db, db:
        known = {row[0] for row in db.execute("SELECT source FROM coverage WHERE work_date = ?", (day,))}
        known.update(item["source"] for item in coverage)
        for item in entries:
            if item["source"] not in known:
                raise ValueError(f"work source needs coverage for this date: {item['source']}")
            old = db.execute("SELECT work_date FROM entries WHERE id = ?", (item["id"],)).fetchone()
            if old and old[0] != day:
                raise ValueError(f"entry ID already belongs to another date: {item['id']}")
        for item in coverage:
            key, payload = item["source"], _json(item)
            old = db.execute("SELECT observed_at, payload FROM coverage WHERE work_date = ? AND source = ?", (day, key)).fetchone()
            if old and _dt(old[0], "stored observed_at") > _dt(observed, "observed_at"):
                continue
            if not old or (observed, payload) != old:
                db.execute("INSERT OR REPLACE INTO coverage VALUES (?, ?, ?, ?)", (day, key, observed, payload))
                db.execute("INSERT INTO history (work_date, kind, record_id, observed_at, payload) VALUES (?, ?, ?, ?, ?)", (day, "coverage", key, observed, payload))
                changes["coverage"] += 1
        for item in entries:
            key, payload = item["id"], _json(item)
            old = db.execute("SELECT observed_at, payload FROM entries WHERE id = ?", (key,)).fetchone()
            if old and _dt(old[0], "stored observed_at") > _dt(observed, "observed_at"):
                continue
            if not old or payload != old[1]:
                db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?)", (key, day, observed, payload))
                db.execute("INSERT INTO history (work_date, kind, record_id, observed_at, payload) VALUES (?, ?, ?, ?, ?)", (day, "entry", key, observed, payload))
                changes["entries"] += 1
            elif observed != old[0]:
                db.execute("UPDATE entries SET observed_at = ? WHERE id = ?", (observed, key))
        recommendation_time = db.execute("SELECT observed_at FROM recommendation_state WHERE work_date = ?", (day,)).fetchone()
        if recommendations is not None and not (recommendation_time and _dt(recommendation_time[0], "stored observed_at") > _dt(observed, "observed_at")):
            old = dict(db.execute("SELECT id, payload FROM recommendations WHERE work_date = ?", (day,)))
            new = {item["id"]: _json(item) for item in recommendations}
            if old != new:
                db.execute("DELETE FROM recommendations WHERE work_date = ?", (day,))
                db.executemany("INSERT INTO recommendations VALUES (?, ?, ?)", [(day, key, value) for key, value in new.items()])
                db.execute("INSERT INTO history (work_date, kind, record_id, observed_at, payload) VALUES (?, ?, ?, ?, ?)",
                           (day, "recommendations", day, observed, _json(recommendations)))
                changes["recommendations"] = sum(old.get(key) != new.get(key) for key in set(old) | set(new))
            # Preserve an explicit empty replacement's timestamp as well, so an
            # older snapshot cannot resurrect suggestions that were withdrawn.
            db.execute("INSERT OR REPLACE INTO recommendation_state VALUES (?, ?)", (day, observed))
    return {"date": day, "journal": str(_journal_path(root)), "changed": changes}


def read_day(root, review_date):
    """Read one day without creating an absent database or leaking other days."""
    day = review_date.isoformat()
    result = {"entries": [], "coverage": [], "recommendations": []}
    if not _journal_path(root).exists():
        return result
    with closing(sqlite3.connect(_journal_path(root))) as db:
        for table, order in (("entries", "id"), ("coverage", "source"), ("recommendations", "id")):
            result[table] = [json.loads(row[0]) for row in db.execute(
                f"SELECT payload FROM {table} WHERE work_date = ? ORDER BY {order}", (day,))]
    return result


def build_review(root, review_date, now, ledger, *, weekly=False):
    """A current task snapshot is context, never proof of a delivery on this day."""
    if not isinstance(review_date, date) or isinstance(review_date, datetime):
        raise ValueError("review_date must be a date")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone offset")
    config = ledger["config"]
    tz = _tz(config["timezone"])
    if review_date > now.astimezone(tz).date():
        raise ValueError("cannot review a future work date")
    data = read_day(root, review_date)
    by_source = {item["source"]: item for item in data["coverage"]}
    required = set(config.get("required_sources", [])) | {"codex_activity"}
    gaps = []
    for source in sorted(required | set(by_source)):
        item = by_source.get(source)
        if not item:
            gaps.append(f"{source}：本日尚未记录采集覆盖，不能据此认定没有事项")
        elif item["status"] != "ok":
            gaps.append(f"{source}：{'部分覆盖' if item['status'] == 'partial' else '不可用'}；{item['scope']}")
    if not data["entries"]:
        gaps.insert(0, "未采集到本日工作证据，不代表今天没有工作")
    recommendations = sorted(data["recommendations"], key=lambda item: (-item["priority"], item["id"]))
    # Only the user's own tasks belong in a personal review. A task linked to a
    # verified entry is included as explicit context even if ownership changed.
    aliases = set(config.get("profile", {}).get("aliases", ["self"]))
    if config.get("profile", {}).get("name"):
        aliases.add(config["profile"]["name"])
    linked = {item.get("task_id") for item in data["entries"] if item.get("task_id")}
    task_context = []
    for task in ledger.get("tasks", []):
        if task.get("owner") not in aliases and task.get("id") not in linked:
            continue
        task_context.append({key: task.get(key) for key in ("id", "title", "status", "due_at", "source_ref")})
    result = {"schema_version": SCHEMA_VERSION, "date": review_date.isoformat(),
            "generated_at": now.isoformat(), "timezone": config["timezone"],
            "entries": sorted(data["entries"], key=lambda item: (_dt(item["occurred_at"], "occurred_at"), item["id"])),
            "task_context": sorted(task_context, key=lambda item: item["id"]),
            "recommendations": recommendations, "next_day": [item for item in recommendations if item["category"] == "next_day"][:3],
            "learning": [item for item in recommendations if item["category"] == "learning"][:2],
            "coverage": data["coverage"], "coverage_gaps": gaps,
            "limits": ["工作事实由 Codex 核实后导入；系统不将聊天结束、计划或任务状态直接视为当日完成证据。",
                       "计划对照是生成时的任务账本快照，并非晨间原计划或历史状态；建议未写入任务、目标或在线日历。"]}
    output = config.get("output", {})
    if weekly or (output.get("weekly_enabled", False) and review_date.isoweekday() == output.get("weekly_day", 5)):
        result["weekly"] = build_weekly(root, review_date, now, config, task_context)
    return result


def build_weekly(root, review_date, now, config, task_context):
    """Aggregate journal observations, never report prose or a task snapshot as history.

    Entries have globally stable IDs on one work date; task_id links observations
    of the same work across dates. The latest evidenced state wins per task.
    All independent observations remain available in the private machine report.
    """
    start = review_date - timedelta(days=review_date.weekday())
    days = [start + timedelta(days=i) for i in range((review_date - start).days + 1)
            if (start + timedelta(days=i)).isoweekday() in config["workdays"]]
    day_keys = {day.isoformat() for day in days}
    selected = {}
    if _journal_path(root).exists():
        with closing(sqlite3.connect(_journal_path(root))) as db:
            rows = db.execute("SELECT sequence, work_date, kind, record_id, observed_at, payload FROM history "
                              "WHERE work_date >= ? AND work_date <= ? ORDER BY sequence",
                              (start.isoformat(), review_date.isoformat()))
            for sequence, day, kind, record_id, observed, payload in rows:
                if day not in day_keys or kind not in {"entry", "coverage", "recommendations"}:
                    continue
                observed = _dt(observed, "observed_at")
                if observed > now:
                    continue
                item = json.loads(payload)
                if kind == "entry" and _dt(item["occurred_at"], "occurred_at") > now:
                    continue
                key = (day, kind, record_id)
                order = (observed, sequence)
                if key not in selected or order > selected[key][0]:
                    selected[key] = (order, item)
    facts, coverage, recommendations = [], {day: [] for day in sorted(day_keys)}, []
    for (day, kind, _), (order, item) in selected.items():
        if kind == "entry":
            facts.append({**item, "work_date": day, "observed_at": order[0].isoformat()})
        elif kind == "coverage":
            coverage[day].append(item)
        elif day == review_date.isoformat():
            # Earlier next-day suggestions can already be completed or withdrawn.
            recommendations = item
    facts.sort(key=lambda item: (_dt(item["occurred_at"], "occurred_at"),
                                 _dt(item["observed_at"], "observed_at"), item["id"]))
    work_items = {}
    for item in facts:
        key = "task:" + item["task_id"] if item.get("task_id") else "entry:" + item["id"]
        work_items[key] = item
    tasks = {item["id"]: item for item in task_context}
    latest = list(work_items.values())
    achievements = [item for item in latest if item["status"] == "completed" and not _routine(item)
                    and tasks.get(item.get("task_id"), {}).get("status") != "cancelled"]
    blocked = [item for item in latest if item["status"] == "blocked" and not _routine(item)
               and tasks.get(item.get("task_id"), {}).get("status") not in {"done", "cancelled"}]
    recommendations.sort(key=lambda item: (-item["priority"], item["id"]))
    next_week = [item for item in recommendations if item["category"] in {"next_week", "focus"}]
    if not next_week:
        # These are explicitly current suggestions, not dated progress or promises.
        next_week = [{**item, "category": "current_task_context", "small_action": "建议确认：" + item["title"]}
                     for item in task_context if item["status"] in {"todo", "waiting"}
                     and not any(fact.get("task_id") == item["id"] and fact["status"] in {"completed", "cancelled"}
                                 for fact in latest)]
    risks = [*blocked, *[item for item in recommendations if item["category"] == "risk"]]
    required = set(config.get("required_sources", [])) | {"codex_activity"}
    gaps, missing_workdays = [], []
    for day, sources in coverage.items():
        by_source = {item["source"]: item for item in sources}
        sources.sort(key=lambda item: item["source"])
        if by_source.get("codex_activity", {}).get("status") in {None, "unavailable"}:
            missing_workdays.append(day)
        for source in sorted(required | set(by_source)):
            item = by_source.get(source)
            if not item or item["status"] != "ok":
                gaps.append({"date": day, "source": source, "status": item["status"] if item else "missing",
                             "scope": item["scope"] if item else "本工作日尚未记录采集覆盖，不能据此认定没有工作"})
    return {"start_date": start.isoformat(), "end_date": review_date.isoformat(),
            "workdays": sorted(day_keys), "next_week_start": (start + timedelta(days=7)).isoformat(),
            "entries": facts, "work_items": latest, "achievements": achievements, "risks": risks,
            "next_week": next_week, "coverage": [{"date": day, "sources": sources} for day, sources in coverage.items()],
            "missing_workdays": missing_workdays, "coverage_gaps": gaps,
            "max_chars": config.get("output", {}).get("weekly_max_chars", 600),
            "limits": ["同一 task_id 只按最新证据状态计一次成果；无关联 ID 的独立事实不会凭相似标题合并。",
                       "任务账本只提供当前建议与完成/取消反馈，不补造本周历史；下周重点均为建议。"]}


def _md(value):
    value = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " / ").replace("\t", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def _routine(entry):
    # Historical delivery observations share this source with genuine, requested
    # tooling releases. Keep verified code/test work without guessing from titles.
    return entry["source"] == "assistant_delivery" and entry.get("completion_basis") not in {"commit", "test"}


def _brief_text(value, item):
    """Remove detail-only references before selecting a verbatim short sentence."""
    text = str(value)
    for key in ("id", "task_id", "source_ref"):
        if item.get(key):
            text = re.sub(r"(?<!\w)" + re.escape(item[key]) + r"(?!\w)", " ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s，。；！？、<>]+", " ", text)
    # Quoted paths may contain spaces. Unquoted Windows paths are removed up to
    # a sentence/clause boundary rather than leaking a trailing private filename.
    text = re.sub(r'''(["'`])(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n]*?\1''', " ", text)
    text = re.sub(r'''(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\r\n，。；！？、<>"'`]+''', " ", text)
    text = re.sub(r'''(?<![\w/])/[\w.~+-]+(?:/[^\s，。；！？、<>"'`]*)?''', " ", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" `，,；;：:—-")
    text = re.sub(r"\s+([，。；！？,.!?;:])", r"\1", text)
    return text


def _brief_sentence(item, fields, fallback, limit=36):
    """Prefer supplied concise wording; never generate a semantic summary.

    An overlong field can fall back to another supplied field or a whole clause.
    If neither fits, use a neutral disclosure instead of cutting a claim in half.
    """
    candidates = [_brief_text(item[field], item) for field in fields if item.get(field)]
    for text in candidates:
        sentence = re.split(r"(?<=[。！？!?])\s*|(?<=[.])\s+", text, maxsplit=1)[0].strip()
        if 4 <= len(sentence) <= limit:
            return sentence.rstrip("。.!！?？") + "。"
    for text in candidates:
        clause = re.split(r"[，,；;]", text, maxsplit=1)[0].strip()
        if 4 <= len(clause) <= limit:
            return clause.rstrip("。.!！?？") + "。"
    return fallback


def _brief_items(items, fields, fallback, maximum, *, limit=36):
    result = []
    for item in items:
        sentence = _brief_sentence(item, fields, fallback, limit=limit)
        if sentence not in result:
            result.append(sentence)
        if len(result) == maximum:
            break
    return result


def _brief_completed(review):
    return _brief_items((item for item in review["entries"] if item["status"] == "completed" and not _routine(item)),
                        ("outcome", "title"), "已有可核实的完成记录，具体结果待简述。", 3)


def _coverage_note(review):
    # Normal bounded/partial reads are already known limitations. Only a missing
    # work source, or an unread calendar relevant to tomorrow, merits one line.
    sources = {item["source"]: item["status"] for item in review["coverage"]}
    missing_work = sources.get("codex_activity") in {None, "unavailable"}
    missing_calendar = bool(review["next_day"]) and not any(
        sources.get(source) in {"ok", "partial"} for source in ("feishu_calendar", "google_calendar"))
    if missing_work and missing_calendar:
        return "注：工作活动与日历尚未读取，成果及安排待核实。"
    if missing_work:
        return "注：工作活动尚未读取，成果记录可能遗漏。"
    if missing_calendar:
        return "注：尚未核对日历，明日安排待确认。"
    return None


def render_review(review):
    """A compact human brief; complete evidence remains in the private JSON."""
    if "weekly" in review:
        return render_weekly_review(review)
    lines = [f"# {review['date']} 工作总结", "", "**今日成果**"]
    completed = _brief_completed(review)
    lines.extend([f"{i}. {_md(text)}" for i, text in enumerate(completed, 1)] or ["尚无已核实的今日成果，不代表没有工作。"])
    blocked = _brief_items((item for item in review["entries"] if item["status"] == "blocked" and not _routine(item)),
                           ("outcome", "title"), "有工作受阻，具体问题待简述。", 2)
    if blocked:
        lines.extend(["", "**问题/待办**", *[f"{i}. {_md(text)}" for i, text in enumerate(blocked, 1)]])
    tomorrow = _brief_items(review["next_day"], ("small_action", "title"), "明日建议的具体动作待简述。", 3)
    lines.extend(["", "**明日计划（建议）**", *([f"{i}. {_md(text)}" for i, text in enumerate(tomorrow, 1)] or ["暂未确认明日计划。"])])
    note = _coverage_note(review)
    if note:
        lines.extend(["", note])
    return "\n".join([*lines, ""])


def render_weekly_review(review):
    """Keep today's result and the week in one concise, evidence-free document."""
    week = review["weekly"]
    today_items = _brief_completed({"entries": [item for item in week["achievements"] if item["work_date"] == review["date"]]})
    today = "；".join(text.rstrip("。") for text in today_items) or "尚无已核实成果，不能据此认定没有工作"
    if len(_md(today)) > 72:
        today = today_items[0].rstrip("。")
    if len(_md(today)) > 72:
        today = "今日已有核实成果"
    achievements = _brief_items(reversed(week["achievements"]), ("outcome", "title"), "已有核实成果，具体结果待简述。", 3, limit=44)
    risks = _brief_items(week["risks"], ("outcome", "small_action", "title"), "有实质问题待确认。", 2, limit=44)
    priorities = _brief_items(week["next_week"], ("small_action", "title"), "下周重点的具体动作待确认。", 3, limit=44)
    for items, fallback in ((achievements, "已有核实成果，具体结果待简述。"), (risks, "有实质问题待确认。"),
                            (priorities, "下周重点的具体动作待确认。")):
        items[:] = [text if len(_md(text)) <= 60 else fallback for text in items]
    note = None
    if week["missing_workdays"]:
        dates = "、".join(day[5:].replace("-", "/") for day in week["missing_workdays"])
        note = f"注：{dates}工作活动未采集，周成果可能遗漏。"
    elif any(item["status"] in {"missing", "unavailable"} for item in week["coverage_gaps"]):
        note = "注：部分来源尚未读取，周报仅据已核实信息。"

    def render():
        lines = [f"# {review['date']} 工作总结", "", f"今日：{_md(today)}。", "",
                 f"**本周成果（{week['start_date']}—{week['end_date']}）**"]
        lines.extend([f"{i}. {_md(text)}" for i, text in enumerate(achievements, 1)] or ["尚无已核实的本周成果，不代表没有工作。"])
        if risks:
            lines.extend(["", "**问题/风险**", *[f"{i}. {_md(text)}" for i, text in enumerate(risks, 1)]])
        lines.extend(["", "**下周重点（建议）**", *([f"{i}. {_md(text)}" for i, text in enumerate(priorities, 1)] or ["暂未确认下周重点。"])])
        if note:
            lines.extend(["", note])
        return "\n".join([*lines, ""])

    # Drop whole lower-ranked items instead of truncating a claim mid-sentence.
    maximum = week["max_chars"]
    rendered = render()
    while len(rendered) > maximum and any(len(items) > 1 for items in (achievements, risks, priorities)):
        candidates = [items for items in (priorities, risks, achievements) if len(items) > 1]
        candidates[0].pop()
        rendered = render()
    if len(rendered) > maximum:
        today = "已有核实成果" if today_items else "尚无已核实成果"
        for items, fallback in ((achievements, "已有核实成果，具体结果待简述。"), (risks, "有实质问题待确认。"),
                                (priorities, "下周重点的具体动作待确认。")):
            items[:] = [text if len(_md(text)) <= 36 else fallback for text in items]
        rendered = render()
    return rendered


def write_review(root, review_date, now, ledger, *, weekly=False, weekly_due=False):
    from weekly_review import fingerprint, prepare_week, record_generation, saved_result
    run = prepare_week(root, review_date, now, ledger["config"], due=weekly_due)
    if weekly_due and run is None:
        return {"weekly_due": False, "detail": "当前没有可生成的到期周；请以 weekly-status 核实待确认交付。"}
    if run:
        review_date = _day(run["report_date"])
        weekly = True
        if run.get("content_hash") and run["state"] != "completed":
            return saved_result(root, run)
    review = build_review(root, review_date, now, ledger, weekly=weekly)
    destination = Path(root) / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    paths = {"report": destination / f"{review_date.isoformat()}-review.md",
             "review_json": destination / f"{review_date.isoformat()}-review.json"}
    staged = []
    try:
        for key, content in (("report", render_review(review)), ("review_json", json.dumps(review, ensure_ascii=False, indent=2) + "\n")):
            # Names and destinations derive only from a validated date, and each
            # temporary file stays beside its destination for atomic replacement.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination, prefix=".review-", suffix=".tmp", delete=False) as stream:
                staged.append((Path(stream.name), paths[key]))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, final in staged:
            os.replace(temporary, final)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    result = {**{key: str(value) for key, value in paths.items()}, "date": review["date"],
            "entries": len(review["entries"]), "completed": sum(item["status"] == "completed" for item in review["entries"]),
            "brief_completed": len(_brief_completed(review)),
            "next_day_suggestions": len(review["next_day"]), "coverage_gaps": review["coverage_gaps"]}
    if "weekly" in review:
        result["weekly"] = {"start_date": review["weekly"]["start_date"], "end_date": review["weekly"]["end_date"],
                            "achievements": len(review["weekly"]["achievements"]),
                            "missing_workdays": review["weekly"]["missing_workdays"]}
    if run:
        run = record_generation(root, run, now, paths["report"].read_text(encoding="utf-8"))
        result["weekly_delivery"] = {key: run[key] for key in ("week_start", "week_end", "report_date", "state", "content_hash",
                                                              "document_key", "notification_key", "notification_text")}
        if run["state"] == "completed":
            result["weekly_delivery"]["delivered_content_hash"] = run["content_hash"]
            result["weekly_delivery"]["content_hash"] = fingerprint(paths["report"].read_text(encoding="utf-8"))
    return result
