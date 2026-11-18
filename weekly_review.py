"""Local weekly delivery intents; no scheduler, network, or automatic sending.

The host runs when it can. A week remains due until the existing Feishu
publisher's document and exact self-message receipts prove delivery.
"""
from contextlib import closing
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from daily_review import _connect, _day, _dt, _journal_path, _tz


def fingerprint(content):
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _runs(root):
    if not _journal_path(root).exists():
        return {}
    with closing(sqlite3.connect(_journal_path(root))) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'weekly_runs' AND type = 'table'").fetchone():
            return {}
        return {key: json.loads(payload) for key, payload in db.execute("SELECT week_start, payload FROM weekly_runs")}


def _save(root, run):
    with closing(_connect(root)) as db, db:
        db.execute("INSERT OR REPLACE INTO weekly_runs VALUES (?, ?)",
                   (run["week_start"], json.dumps(run, ensure_ascii=False, sort_keys=True)))


def _identity_manifest(root, config):
    path = Path(root) / "data" / "feishu-delivery" / "manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    integration = config.get("integrations", {}).get("feishu", {})
    settings = integration.get("delivery", {})
    profile = integration.get("profile", "codex-assistant")
    expected = settings.get("expected_owner_id")
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("accounts"), dict):
        raise ValueError("invalid delivery manifest")
    accounts = [(key, item) for key, item in manifest["accounts"].items()
                if isinstance(item, dict) and item.get("profile") == profile
                and (not expected or item.get("owner_id") == expected)]
    if len(accounts) != 1:
        raise ValueError("delivery account is missing or ambiguous for the configured profile/owner")
    namespace, account = accounts[0]
    owner, app = account.get("owner_id"), account.get("app_id")
    if not isinstance(owner, str) or not owner or not isinstance(app, str) or not app:
        raise ValueError("delivery account has no verified owner/app identity")
    if namespace != _digest(profile + "\0" + owner):
        raise ValueError("delivery account namespace mismatch")
    return account, settings, path


def delivery_proof(root, run, config):
    """Verify the pinned weekly body and exact notification using durable receipts."""
    if not run.get("content_hash"):
        raise ValueError("weekly document has not been generated")
    selected = _identity_manifest(root, config)
    if selected is None:
        raise ValueError("delivery manifest is absent")
    account, settings, path = selected
    document = account.get("documents", {}).get(run["document_key"], {})
    notification = account.get("notifications", {}).get(run["notification_key"], {})
    if document.get("state") != "verified" or document.get("content_hash") != run["content_hash"]:
        raise ValueError("weekly document is not verified with the pinned content hash")
    url = document.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or not document.get("token"):
        raise ValueError("weekly document has no verified URL/token")
    if notification.get("state") != "sent" or not isinstance(notification.get("message_id"), str) or not notification["message_id"]:
        raise ValueError("weekly notification has no successful message receipt")
    if notification.get("owner_id") != account["owner_id"] or notification.get("app_id") != account["app_id"]:
        raise ValueError("weekly notification account/app mismatch")
    if notification.get("sender") != settings.get("message_sender", "user"):
        raise ValueError("weekly notification sender mismatch")
    expected_hash = _digest(run["notification_text"] + "\n\n" + url)
    if notification.get("content_hash") != expected_hash:
        raise ValueError("notification does not match the pinned weekly text and document URL")
    return {"week_start": run["week_start"], "report_date": run["report_date"],
            "document_key": run["document_key"], "report_hash": run["content_hash"], "document_url": url,
            "notification_key": run["notification_key"], "message_id": notification["message_id"],
            "manifest": str(path), "profile": account["profile"], "owner_id": account["owner_id"], "app_id": account["app_id"]}


def _describe(root, run, config):
    result = {**run, "due": run["state"] != "completed", "needs_completion": False}
    if run["state"] == "completed":
        return result
    try:
        result["receipt"] = delivery_proof(root, run, config)
        result.update(due=False, needs_completion=True, state="delivered_unacknowledged")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result["delivery_detail"] = str(exc)
        # An existing successful/unknown send can be a normal daily brief or an
        # unresolved request. Neither authorizes rebuilding or another send.
        try:
            selected = _identity_manifest(root, config)
            notification = selected[0].get("notifications", {}).get(run["notification_key"], {}) if selected else {}
            if notification.get("state") in {"sent", "pending", "unknown", "conflict"}:
                result.update(due=False, state="delivery_check_required", needs_verification=True)
        except (ValueError, OSError, KeyError, TypeError):
            result.update(due=False, state="delivery_check_required", needs_verification=True)
    return result


def weekly_status(root, now, config):
    tz = _tz(config["timezone"])
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone offset")
    local = now.astimezone(tz)
    output = config.get("output", {})
    base = {"enabled": output.get("weekly_enabled", False), "as_of": now.isoformat(),
            "due": False, "needs_completion": False, "state": "waiting"}
    if not base["enabled"]:
        return {**base, "state": "disabled"}
    runs = _runs(root)
    pending = sorted((run for run in runs.values() if run["state"] != "completed"), key=lambda run: run["week_start"])
    if pending:
        return {**base, **_describe(root, pending[0], config)}
    weekly_day = output.get("weekly_day", 5)
    due_time = time.fromisoformat(output.get("weekly_due_time", "18:00"))
    latest_end = local.date() - timedelta(days=local.weekday()) + timedelta(days=weekly_day - 1)
    if datetime.combine(latest_end, due_time, tzinfo=tz) > local:
        latest_end -= timedelta(days=7)
    first_date = _day(output["weekly_start_date"]) if output.get("weekly_start_date") else latest_end
    end = first_date - timedelta(days=first_date.weekday()) + timedelta(days=weekly_day - 1)
    if end < first_date:
        end += timedelta(days=7)
    while end <= latest_end:
        start = end - timedelta(days=end.weekday())
        if start.isoformat() not in runs or runs[start.isoformat()]["state"] != "completed":
            return {**base, "due": True, "state": "due", "week_start": start.isoformat(), "week_end": end.isoformat(),
                    "report_date": end.isoformat(), "document_key": f"brief:{end.isoformat()}:review",
                    "notification_key": f"notify:{end.isoformat()}:review"}
        end += timedelta(days=7)
    return base


def prepare_week(root, report_date, now, config, *, due=False):
    runs = _runs(root)
    if due:
        status = weekly_status(root, now, config)
        if not status["due"]:
            return None
        start, end = status["week_start"], status["week_end"]
    else:
        existing = next((run for run in runs.values() if run["report_date"] == report_date.isoformat()), None)
        if existing:
            return existing
        output = config.get("output", {})
        if not output.get("weekly_enabled", False) or report_date.isoweekday() != output.get("weekly_day", 5):
            return None
        tz = _tz(config["timezone"])
        if datetime.combine(report_date, time.fromisoformat(output.get("weekly_due_time", "18:00")), tzinfo=tz) > now:
            return None
        if output.get("weekly_start_date") and report_date < _day(output["weekly_start_date"]):
            return None
        end, start = report_date.isoformat(), (report_date - timedelta(days=report_date.weekday())).isoformat()
    if start in runs:
        return runs[start]
    run = {"week_start": start, "week_end": end, "report_date": end, "state": "pending",
           "created_at": now.isoformat(), "generated_at": None, "completed_at": None, "content_hash": None,
           "document_key": f"brief:{end}:review", "notification_key": f"notify:{end}:review",
           "notification_text": f"1. {start}—{end} 周工作总结已整理，包含本周成果、风险与下周重点。"}
    # A stable primary key is claimed before generating or publishing anything.
    with closing(_connect(root)) as db, db:
        db.execute("INSERT OR IGNORE INTO weekly_runs VALUES (?, ?)", (start, json.dumps(run, ensure_ascii=False, sort_keys=True)))
        return json.loads(db.execute("SELECT payload FROM weekly_runs WHERE week_start = ?", (start,)).fetchone()[0])


def record_generation(root, run, now, content):
    if run["state"] != "completed":
        run = {**run, "state": "generated", "generated_at": now.isoformat(), "content_hash": fingerprint(content)}
        _save(root, run)
    return run


def saved_result(root, run):
    report = Path(root) / "reports" / (run["report_date"] + "-review.md")
    machine = report.with_suffix(".json")
    if not report.exists() or not machine.exists() or fingerprint(report.read_text(encoding="utf-8")) != run["content_hash"]:
        raise ValueError("pinned weekly artifact is missing or changed; verify delivery before rebuilding")
    return {"report": str(report), "review_json": str(machine), "date": run["report_date"],
            "reused": True, "weekly_delivery": run}


def complete_week(root, week_start, receipt, now, config):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone offset")
    start = week_start.isoformat() if isinstance(week_start, date) else _day(week_start).isoformat()
    run = _runs(root).get(start)
    if run is None:
        raise ValueError("no weekly generation intent exists for this week")
    if not isinstance(receipt, dict):
        raise ValueError("weekly receipt must be an object")
    proof = run["receipt"] if run["state"] == "completed" else delivery_proof(root, run, config)
    required = ("week_start", "report_date", "document_key", "report_hash", "document_url", "notification_key", "message_id")
    if any(receipt.get(key) != proof[key] for key in required):
        raise ValueError("weekly receipt does not match the pinned document and sent notification")
    if run["state"] != "completed":
        run = {**run, "state": "completed", "completed_at": now.isoformat(), "receipt": proof}
        _save(root, run)
    return {"week_start": start, "report_date": run["report_date"], "state": "completed", "receipt": proof}
