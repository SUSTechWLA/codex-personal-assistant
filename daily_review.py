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
CATEGORIES = {"next_day", "learning", "risk", "focus"}
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


def build_review(root, review_date, now, ledger):
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
    return {"schema_version": SCHEMA_VERSION, "date": review_date.isoformat(),
            "generated_at": now.isoformat(), "timezone": config["timezone"],
            "entries": sorted(data["entries"], key=lambda item: (_dt(item["occurred_at"], "occurred_at"), item["id"])),
            "task_context": sorted(task_context, key=lambda item: item["id"]),
            "recommendations": recommendations, "next_day": [item for item in recommendations if item["category"] == "next_day"][:3],
            "learning": [item for item in recommendations if item["category"] == "learning"][:2],
            "coverage": data["coverage"], "coverage_gaps": gaps,
            "limits": ["工作事实由 Codex 核实后导入；系统不将聊天结束、计划或任务状态直接视为当日完成证据。",
                       "计划对照是生成时的任务账本快照，并非晨间原计划或历史状态；建议未写入任务、目标或在线日历。"]}


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


def _brief_items(items, fields, fallback, maximum):
    result = []
    for item in items:
        sentence = _brief_sentence(item, fields, fallback)
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


def write_review(root, review_date, now, ledger):
    review = build_review(root, review_date, now, ledger)
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
    return {**{key: str(value) for key, value in paths.items()}, "date": review["date"],
            "entries": len(review["entries"]), "completed": sum(item["status"] == "completed" for item in review["entries"]),
            "brief_completed": len(_brief_completed(review)),
            "next_day_suggestions": len(review["next_day"]), "coverage_gaps": review["coverage_gaps"]}
