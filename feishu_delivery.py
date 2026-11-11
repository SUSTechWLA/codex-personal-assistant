"""Publish reviewed assistant documents to an explicitly configured Feishu space.

No collection, authentication, arbitrary recipients, sharing or calendar writes.
Remote writes have durable intents: unknown outcomes are reconciled, never
blindly retried. The default entry point is a network-free preview.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

from collect_feishu import find_cli, parse_cli_error_bytes, parse_json_bytes, run_bounded, safe_error_metadata


MAX_DOCUMENTS = 30
MAX_CONTENT_BYTES = 500 * 1024
MAX_LIST_PAGES = 10
GOOD_DOCUMENT_STATES = {"verified", "unchanged"}


def _text(value, label, maximum=200, *, multiline=False):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters")
    if any(ord(c) < 32 and (not multiline or c not in "\n\r\t") for c in value):
        raise ValueError(f"{label} contains control characters")
    return value.strip() if not multiline else value


def _key(value, label="key"):
    value = _text(value, label, 128)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError(f"{label} must be a stable ASCII identifier")
    return value


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise ValueError("invalid Feishu resource token")
    return value


def _segment(value):
    value = _text(value, "folder segment", 100)
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("folder segments cannot contain path traversal or separators")
    return value


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(content):
    return content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _fingerprint(content):
    return _digest(_normalized(content))


def _canonical(content, title):
    """Compare the full Markdown through observed CLI round-trip differences.

    This is not a summary or a content marker. Every non-formatting character
    and every word boundary remains part of the comparison. Fenced code stays
    literal, including JSON strings and backslashes; unsupported conversions
    therefore fail closed instead of being assumed complete.
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    metadata = re.match(r"\A<title>([^\n]*)</title>\n*", content)
    if metadata and html.unescape(metadata[1]) == title:
        content = content[metadata.end():]
    # Whole-document JSON is data, not Markdown punctuation. Preserve quoted
    # whitespace, escapes, keys and values instead of de-escaping its strings.
    def distinct_object(pairs):
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate JSON keys")
        return dict(pairs)
    try:
        parsed_json = json.loads(content, object_pairs_hook=distinct_object)
    except (ValueError, TypeError):
        parsed_json = None
    if isinstance(parsed_json, (dict, list)):
        return [("json", json.dumps(parsed_json, ensure_ascii=False, separators=(",", ":")))]
    parts, prose, code = [], [], []
    fence, info = None, None

    def flush_prose():
        inline_literals = []

        def preserve_inline(match):
            literal = match[3].replace("\n", " ")
            # Code spans normalize line endings and one surrounding space, but
            # backslashes, entities and interior spaces remain literal data.
            if literal.startswith(" ") and literal.endswith(" ") and literal.strip(" "):
                literal = literal[1:-1]
            inline_literals.append(literal)
            return match[1] + "\x00inline-code-" + str(len(inline_literals) - 1) + "\x00"

        # An even backslash run before an opening backtick is literal prose;
        # only an odd run escapes that backtick and prevents a code span.
        value = re.sub(r"(?<![`\\])((?:\\\\)*)(`+)(?!`)(.*?)(?<!`)\2(?!`)", preserve_inline,
                       "\n".join(prose), flags=re.S)
        value = html.unescape(value)
        value = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", value)
        lines = value.split("\n")
        for index, line in enumerate(lines):
            # Only a whole separator row following a table header is normalized.
            # Column count and alignment colons are retained, as is all content.
            if index == 0 or "|" not in lines[index - 1] or "|" not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("|"):
                stripped = stripped[1:]
            if stripped.endswith("|"):
                stripped = stripped[:-1]
            cells = [cell.strip() for cell in stripped.split("|")]
            if len(cells) >= 2 and all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                lines[index] = "|" + "|".join((":" if cell.startswith(":") else "") + "-" +
                                              (":" if cell.endswith(":") else "") for cell in cells) + "|"
        value = re.sub(r"\s+", " ", "\n".join(lines)).strip()
        if value:
            parts.append(("prose", value, tuple(inline_literals)))
        prose.clear()

    for line in content.split("\n"):
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                parts.append(("code", info, "\n".join(code)))
                code, fence, info = [], None, None
            else:
                code.append(line)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening:
            flush_prose()
            fence, info = opening[1], opening[2].strip()
        else:
            prose.append(line)
    if fence is not None:
        # An unclosed code block must match literally, not be silently discarded.
        parts.append(("unclosed_code", info, "\n".join(code)))
    flush_prose()
    return parts


def _url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        domains = ("feishu.cn", "larksuite.com", "larkoffice.com")
        bad_query = {k.casefold() for k in parse_qs(parsed.query)} & {"access_token", "refresh_token", "code", "user_code"}
        if (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and parsed.path and not bad_query
                and any(host == d or host.endswith("." + d) for d in domains)):
            return value
    except ValueError:
        pass
    return None


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) - {"schema_version", "documents", "notification"}:
        raise ValueError("bundle permits schema_version, documents and notification only")
    if bundle.get("schema_version") != 1 or isinstance(bundle.get("schema_version"), bool):
        raise ValueError("bundle schema_version must be 1")
    docs = bundle.get("documents")
    if not isinstance(docs, list) or not 1 <= len(docs) <= MAX_DOCUMENTS:
        raise ValueError("documents must contain between 1 and 30 reviewed documents")
    clean, keys, locations, total = [], set(), set(), 0
    for raw in docs:
        if not isinstance(raw, dict) or set(raw) != {"key", "title", "folder", "content"}:
            raise ValueError("each document requires exactly key, title, folder and content")
        key, title = _key(raw["key"]), _text(raw["title"], "title", 240)
        folder = raw["folder"]
        if not isinstance(folder, list) or len(folder) > 6:
            raise ValueError("folder must have at most six segments")
        folder = [_segment(part) for part in folder]
        content = _text(raw["content"], "content", MAX_CONTENT_BYTES, multiline=True)
        size = len(content.encode("utf-8"))
        total += size
        location = (tuple(folder), title)
        if key in keys or location in locations:
            raise ValueError("document keys and folder/title combinations must be unique")
        keys.add(key)
        locations.add(location)
        clean.append({"key": key, "title": title, "folder": folder, "content": content,
                      "content_bytes": size, "content_hash": _fingerprint(content)})
    if total > MAX_CONTENT_BYTES:
        raise ValueError("reviewed content exceeds the shared 500 KiB limit")
    notification = bundle.get("notification")
    if notification is not None:
        if not isinstance(notification, dict) or set(notification) != {"key", "text"}:
            raise ValueError("notification permits key and text only; recipients are never accepted")
        notification = {"key": _key(notification["key"], "notification key"),
                        "text": _text(notification["text"], "notification text", 5000, multiline=True)}
    return {"schema_version": 1, "documents": clean, "notification": notification}


def _settings(config):
    integration = config.get("integrations", {}).get("feishu", {})
    delivery = integration.get("delivery", {})
    if not isinstance(delivery, dict):
        raise ValueError("integrations.feishu.delivery must be an object")
    profile = _text(integration.get("profile", "codex-assistant"), "profile", 64)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        raise ValueError("invalid Feishu profile")
    mode = delivery.get("folder_discovery", "list")
    if mode not in {"list", "managed_only"}:
        raise ValueError("folder_discovery must be list or managed_only")
    for flag in ("enabled", "send_self_enabled"):
        if flag in delivery and type(delivery[flag]) is not bool:
            raise ValueError(f"delivery.{flag} must be boolean")
    root_token = delivery.get("root_token")
    if root_token is not None:
        root_token = _token(root_token)
    expected = delivery.get("expected_owner_id")
    if expected is not None and not re.fullmatch(r"ou_[A-Za-z0-9]+", str(expected)):
        raise ValueError("expected_owner_id must be a user open ID")
    sender = delivery.get("message_sender", "user")
    if sender not in {"user", "bot"}:
        raise ValueError("message_sender must be user or explicitly selected bot")
    bot_name = delivery.get("expected_bot_name")
    if bot_name is not None:
        bot_name = _text(bot_name, "expected_bot_name", 240)
    if sender == "bot" and not bot_name:
        raise ValueError("explicit bot delivery requires expected_bot_name")
    return {"profile": profile, "enabled": delivery.get("enabled", False),
            "send_self_enabled": delivery.get("send_self_enabled", False),
            "root_name": _segment(delivery.get("root_name", "个人工作台")),
            "root_token": root_token, "root_url": _url(delivery.get("root_url")),
            "folder_discovery": mode, "expected_owner_id": expected,
            "message_sender": sender, "expected_bot_name": bot_name}


class DeliveryFailure(Exception):
    def __init__(self, state, detail):
        self.state, self.detail = state, detail
        super().__init__(detail)


@contextmanager
def _lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "publish.lock"
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("another publication holds publish.lock; inspect an interrupted run before clearing its lock") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}))
        yield
    finally:
        path.unlink(missing_ok=True)


def _atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Publisher:
    def __init__(self, root, settings, runner, command):
        self.root, self.settings = Path(root).resolve(), settings
        self.runner, self.command = runner, command
        self.directory = self.root / "data" / "feishu-delivery"
        self.path = self.directory / "manifest.json"
        self.cache = {}

    def save(self):
        _atomic_json(self.path, self.manifest)

    def call(self, args, *, write=False, docs=False, whoami=False, identity="user"):
        command = [*self.command, "--profile", self.settings["profile"], *args, "--as", identity]
        if not docs and not whoami:
            command += ["--format", "json"]
        try:
            result = self.runner(command, cwd=self.root, timeout_seconds=45, max_bytes=2 * 1024 * 1024)
        except OSError:
            raise DeliveryFailure("failed", "CLI could not start; no authentication was attempted") from None
        if result.get("error"):
            raise DeliveryFailure("unknown" if write else "failed", "CLI timeout or output limit; remote outcome is not assumed")
        parsed = parse_json_bytes(result.get("stdout", b""))
        if result.get("returncode") != 0 or not isinstance(parsed, dict) or parsed.get("ok") is False:
            classifiers = safe_error_metadata(parsed) or safe_error_metadata(parse_cli_error_bytes(result.get("stderr", b"")))
            suffix = ", ".join(f"{k}={v}" for k, v in sorted(classifiers.items())) or "unclassified"
            definite = classifiers.get("type") in {"authorization", "validation", "permission", "argument"}
            raise DeliveryFailure("failed" if not write or definite else "unknown", "CLI request failed (" + suffix + ")")
        if parsed.get("identity") != identity or (not whoami and parsed.get("ok") is not True):
            raise DeliveryFailure("unknown" if write else "failed", "response did not confirm the required user identity")
        return parsed if whoami else parsed.get("data")

    def start(self):
        identity = self.call(["whoami"], whoami=True)
        owner = identity.get("onBehalfOf", {}).get("openId")
        if identity.get("available") is not True or not isinstance(owner, str) or not re.fullmatch(r"ou_[A-Za-z0-9]+", owner):
            raise DeliveryFailure("failed", "whoami did not confirm an available personal user")
        if self.settings["expected_owner_id"] and owner != self.settings["expected_owner_id"]:
            raise DeliveryFailure("failed", "current user does not match the configured publication owner")
        self.owner = owner
        app_id = identity.get("appId")
        if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", app_id):
            raise DeliveryFailure("failed", "whoami did not identify the profile application")
        self.app_id = app_id
        self.namespace = _digest(self.settings["profile"] + "\0" + owner)
        if self.path.exists():
            try:
                self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise ValueError("delivery manifest cannot be read; refusing to create duplicate resources") from None
            if not isinstance(self.manifest, dict) or self.manifest.get("schema_version") != 1 or not isinstance(self.manifest.get("accounts"), dict):
                raise ValueError("invalid delivery manifest; refusing remote writes")
        else:
            self.manifest = {"schema_version": 1, "accounts": {}}
        self.account = self.manifest["accounts"].setdefault(self.namespace, {
            "profile": self.settings["profile"], "owner_id": owner, "app_id": app_id,
            "folders": {}, "documents": {}, "notifications": {}})
        if self.account.get("owner_id") != owner or self.account.get("profile") != self.settings["profile"]:
            raise ValueError("manifest account mismatch")
        if self.account.get("app_id") != app_id:
            raise ValueError("profile application changed; existing resources require explicit migration")
        if any(not isinstance(self.account.get(name), dict) for name in ("folders", "documents", "notifications")):
            raise ValueError("invalid account manifest; refusing remote writes")
        configured = self.settings["root_token"]
        if configured and any(account is not self.account and isinstance(account, dict)
                              and account.get("root", {}).get("token") == configured
                              for account in self.manifest["accounts"].values()):
            raise ValueError("configured root is already bound to another account/profile")
        previous = self.account.get("root")
        if previous and ((configured and configured != previous.get("token")) or previous.get("name") != self.settings["root_name"]):
            raise ValueError("configured root changed; migrate it explicitly instead of mixing destinations")
        if not previous and configured:
            self.account["root"] = {"name": self.settings["root_name"], "token": configured,
                                    "url": self.settings["root_url"], "state": "verified", "configured": True}
            self.save()
        elif not previous:
            self.account["root"] = self.folder_record(self.settings["root_name"], "", None)
            self.save()
        self.root_folder = self.ensure_folder(self.account["root"], "")

    def list_folder(self, token):
        if token in self.cache:
            return self.cache[token]
        records, cursor, seen = [], None, set()
        for _ in range(MAX_LIST_PAGES):
            params = {"folder_token": token, "page_size": 200}
            if cursor:
                params["page_token"] = cursor
            data = self.call(["drive", "files", "list", "--params", json.dumps(params, ensure_ascii=False, separators=(",", ":"))])
            if not isinstance(data, dict) or not isinstance(data.get("files"), list) or type(data.get("has_more")) is not bool:
                raise DeliveryFailure("failed", "folder listing did not confirm complete pagination")
            records.extend(data["files"])
            if data["has_more"] is False and not data.get("next_page_token"):
                self.cache[token] = records
                return records
            cursor = data.get("next_page_token")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise DeliveryFailure("failed", "folder listing has an invalid or repeated page cursor")
            seen.add(cursor)
        raise DeliveryFailure("failed", "folder listing exceeded its page limit; it is not treated as empty")

    @staticmethod
    def folder_record(name, parent, token):
        return {"name": name, "parent": parent, "token": token, "url": None, "state": "new"}

    def ensure_folder(self, record, parent):
        if record.get("token"):
            return _token(record["token"])
        unknown = record.get("state") in {"pending", "unknown"}
        if self.settings["folder_discovery"] == "list":
            candidates = [x for x in self.list_folder(parent) if isinstance(x, dict) and x.get("name") == record["name"] and x.get("type") == "folder"]
            if len(candidates) > 1:
                raise DeliveryFailure("conflict", "multiple same-name folders; no destination was guessed")
            if candidates:
                record.update(token=_token(candidates[0].get("token")), url=_url(candidates[0].get("url")), state="verified")
                self.save()
                return record["token"]
        if unknown:
            raise DeliveryFailure("unknown", "folder creation has an unresolved outcome; no duplicate creation was attempted")
        record["state"] = "pending"
        self.save()
        args = ["drive", "+create-folder", "--name", record["name"]]
        if parent:
            args += ["--folder-token", parent]
        try:
            data = self.call(args, write=True)
            if not isinstance(data, dict):
                raise DeliveryFailure("unknown", "folder creation returned no receipt")
            try:
                token = _token(data.get("folder_token", data.get("token")))
            except ValueError:
                raise DeliveryFailure("unknown", "folder creation returned an invalid token") from None
            record.update(token=token, url=_url(data.get("url")), state="verified")
            self.save()
            self.cache.pop(parent, None)
            return token
        except DeliveryFailure as exc:
            record.update(state=exc.state, detail=exc.detail)
            self.save()
            raise

    def destination(self, parts):
        parent = self.root_folder
        for index, part in enumerate(parts):
            path = json.dumps(parts[:index + 1], ensure_ascii=False, separators=(",", ":"))
            record = self.account["folders"].setdefault(path, self.folder_record(part, parent, None))
            if record.get("parent") != parent:
                raise DeliveryFailure("conflict", "managed folder parent changed")
            parent = self.ensure_folder(record, parent)
        return parent

    def fetch(self, token):
        data = self.call(["docs", "+fetch", "--doc", token, "--doc-format", "markdown"], docs=True)
        document = data.get("document") if isinstance(data, dict) else None
        if (not isinstance(document, dict) or document.get("document_id") != token
                or not isinstance(document.get("content"), str)
                or type(document.get("revision_id")) is not int or document["revision_id"] < 0):
            raise DeliveryFailure("failed", "document fetch returned an unrecognized content/revision receipt")
        return document

    def expected_content(self, record):
        content_hash = record.get("pending_hash", record.get("content_hash"))
        if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise DeliveryFailure("unknown", "previous publication has no valid reviewed-content receipt")
        try:
            content = (self.directory / "content" / (content_hash + ".md")).read_text(encoding="utf-8")
        except (OSError, ValueError):
            raise DeliveryFailure("unknown", "reviewed source content is unavailable; document completeness cannot be assumed") from None
        if _fingerprint(content) != content_hash:
            raise DeliveryFailure("unknown", "reviewed source content changed after its publication intent")
        return content

    @staticmethod
    def check_content(record, actual, expected):
        if _canonical(actual, record["title"]) != _canonical(expected, record["title"]):
            raise DeliveryFailure("partial", "complete document read-back does not match the reviewed content")

    def verify(self, record, *, expected_revision=None):
        document = self.fetch(record["token"])
        if expected_revision is not None and document["revision_id"] != expected_revision:
            raise DeliveryFailure("conflict", "document revision changed before verification")
        self.check_content(record, document["content"], self.expected_content(record))
        record.update(remote_hash=_fingerprint(document["content"]), revision=document["revision_id"],
                      content_hash=record.get("pending_hash", record.get("content_hash")), state="verified")
        record.pop("detail", None)
        self.save()
        return document

    def content_file(self, document):
        directory = self.directory / "content"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (document["content_hash"] + ".md")
        path.write_text(document["content"], encoding="utf-8")
        return "@" + path.relative_to(self.root).as_posix()

    def publish_document(self, document):
        key = document["key"]
        record = self.account["documents"].get(key)
        try:
            parent = self.destination(document["folder"])
            if record and (record.get("folder") != document["folder"] or record.get("title") != document["title"]):
                raise DeliveryFailure("conflict", "managed document title or folder changed; migration was not implied")
            if not record:
                record = {"key": key, "title": document["title"], "folder": document["folder"],
                          "parent": parent, "state": "new", "token": None, "url": None}
                self.account["documents"][key] = record
                self.save()
            if record.get("token"):
                if record.get("state") in {"created", "updated"}:
                    self.verify(record, expected_revision=record.get("receipt_revision"))
                elif record.get("state") in {"pending", "unknown", "partial"}:
                    fetched = self.fetch(record["token"])
                    self.check_content(record, fetched["content"], self.expected_content(record))
                    self.verify(record)
                current = self.fetch(record["token"])
                if (_fingerprint(current["content"]) != record.get("remote_hash")
                        or current["revision_id"] != record.get("revision")):
                    raise DeliveryFailure("conflict", "remote document was edited outside the last verified publication")
                if record.get("content_hash") == document["content_hash"]:
                    # Revalidate old manifests too; an old baseline can itself
                    # have recorded a truncated but readable document.
                    self.check_content(record, current["content"], document["content"])
                    return self.document_result(record, "unchanged")
                record.update(state="pending", operation="update", pending_hash=document["content_hash"])
                self.save()
                data = self.call(["docs", "+update", "--doc", record["token"], "--command", "overwrite",
                                  "--doc-format", "markdown", "--content", self.content_file(document),
                                  "--revision-id", str(current["revision_id"])], write=True, docs=True)
                receipt = data.get("document") if isinstance(data, dict) else None
                if not isinstance(receipt, dict) or type(receipt.get("revision_id")) is not int:
                    raise DeliveryFailure("unknown", "document update returned no revision receipt")
                record.update(state="updated", receipt_revision=receipt["revision_id"])
                self.save()
                if data.get("result") != "success" or data.get("warnings"):
                    raise DeliveryFailure("partial", "document update was not confirmed as complete success")
                self.verify(record, expected_revision=receipt["revision_id"])
                return self.document_result(record)
            unresolved = record.get("state") in {"pending", "unknown"}
            if self.settings["folder_discovery"] == "list":
                candidates = [x for x in self.list_folder(parent) if isinstance(x, dict) and x.get("name") == document["title"] and x.get("type") == "docx"]
                if candidates:
                    if len(candidates) != 1 or not unresolved:
                        raise DeliveryFailure("conflict", "same-name unmanaged document exists; it was not adopted or overwritten")
                    token = _token(candidates[0].get("token"))
                    fetched = self.fetch(token)
                    self.check_content(record, fetched["content"], self.expected_content(record))
                    record.update(token=token, url=_url(candidates[0].get("url")))
                    self.save()
                    self.verify(record)
                    if record["content_hash"] == document["content_hash"]:
                        return self.document_result(record)
                    return self.publish_document(document)
            if unresolved:
                raise DeliveryFailure("unknown", "document creation has an unresolved outcome; no duplicate creation was attempted")
            record.update(state="pending", operation="create", pending_hash=document["content_hash"])
            self.save()
            data = self.call(["docs", "+create", "--doc-format", "markdown", "--title", document["title"],
                              "--parent-token", parent, "--content", self.content_file(document)], write=True, docs=True)
            receipt = data.get("document") if isinstance(data, dict) else None
            if not isinstance(receipt, dict):
                raise DeliveryFailure("unknown", "document creation returned no receipt")
            try:
                token = _token(receipt.get("document_id"))
            except ValueError:
                raise DeliveryFailure("unknown", "document creation returned an invalid token") from None
            record.update(token=token, url=_url(receipt.get("url")), state="created", receipt_revision=receipt.get("revision_id"))
            self.save()  # Keep the token even when the following fetch fails.
            self.cache.pop(parent, None)
            if data.get("warnings"):
                raise DeliveryFailure("partial", "document creation reported conversion warnings")
            self.verify(record, expected_revision=record.get("receipt_revision"))
            return self.document_result(record)
        except (DeliveryFailure, ValueError) as exc:
            state = exc.state if isinstance(exc, DeliveryFailure) else "failed"
            detail = exc.detail if isinstance(exc, DeliveryFailure) else "resource receipt could not be validated"
            if record is not None:
                # A confirmed create/update with a failed verification remains
                # recoverable through its token, never another create call.
                if record.get("state") not in {"created", "updated"} or state in {"conflict", "partial"}:
                    record["state"] = state
                record["detail"] = detail
                self.save()
                return self.document_result(record, state, detail)
            return {"key": key, "title": document["title"], "state": state, "detail": detail}

    @staticmethod
    def document_result(record, state=None, detail=None):
        result = {k: record.get(k) for k in ("key", "title", "token", "url")}
        result["state"] = state or record["state"]
        if detail:
            result["detail"] = detail
        return result

    def notify(self, notification, documents):
        if notification is None:
            return {"state": "not_requested"}
        key = notification["key"]
        if not self.settings["send_self_enabled"]:
            return {"key": key, "state": "skipped_missing_permission", "detail": "self-message delivery is disabled; no message was sent"}
        if any(doc["state"] not in GOOD_DOCUMENT_STATES for doc in documents):
            return {"key": key, "state": "blocked_documents", "detail": "not every requested document was verified"}
        links = [doc["url"] for doc in documents if doc.get("url")]
        text = notification["text"]
        if links:
            text += "\n\n" + "\n".join(links[:5])
        if len(links) > 5:
            text += f"\n另有 {len(links) - 5} 份资料已归档。"
        content_hash = _digest(text)
        previous = self.account["notifications"].get(key)
        if previous:
            if (previous.get("state") == "sent" and previous.get("content_hash") == content_hash
                    and previous.get("sender") == self.settings["message_sender"]):
                return {"key": key, "state": "unchanged", "message_id": previous.get("message_id")}
            if previous.get("state") != "failed":
                return {"key": key, "state": "conflict" if previous.get("state") == "sent" else "unknown",
                        "detail": "notification key already has a delivered or unresolved intent; no automatic resend"}
        sender = self.settings["message_sender"]
        if sender == "bot":
            try:
                info = self.call(["api", "GET", "/open-apis/bot/v3/info"], identity="bot")
                if not isinstance(info, dict) or info.get("app_name") != self.settings["expected_bot_name"]:
                    return {"key": key, "state": "failed", "detail": "profile bot does not match the explicitly authorized assistant name"}
            except DeliveryFailure as exc:
                return {"key": key, "state": "failed", "detail": exc.detail}
        idempotency = (previous or {}).get("idempotency_key") or "pa-" + _digest(self.namespace + "\0" + key)[:44]
        attempts = list((previous or {}).get("attempts", []))
        if previous and not attempts:
            attempts.append({k: previous[k] for k in ("state", "detail", "sender") if k in previous})
        attempt = {"state": "pending", "started_at": datetime.now(timezone.utc).isoformat(), "sender": sender}
        attempts.append(attempt)
        record = {"state": "pending", "content_hash": content_hash, "idempotency_key": idempotency,
                  "owner_id": self.owner, "app_id": self.app_id, "sender": sender, "attempts": attempts}
        self.account["notifications"][key] = record
        self.save()
        try:
            data = self.call(["im", "+messages-send", "--user-id", self.owner,
                              "--idempotency-key", idempotency, "--text", text], write=True, identity=sender)
            message_id = data.get("message_id") if isinstance(data, dict) else None
            if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", message_id):
                raise DeliveryFailure("unknown", "message request returned no verifiable message receipt")
            record.update(state="sent", message_id=message_id)
            attempt.update(state="sent", message_id=message_id)
            self.save()
            return {"key": key, "state": "sent", "message_id": message_id}
        except DeliveryFailure as exc:
            record.update(state=exc.state, detail=exc.detail)
            attempt.update(state=exc.state, detail=exc.detail)
            self.save()
            return {"key": key, "state": exc.state, "detail": exc.detail}


def publish(root, bundle, config, *, apply=False, runner=None, command=None):
    """Preview by default. Apply only explicitly reviewed content to this user."""
    bundle, settings = validate_bundle(bundle), _settings(config)
    if not apply:
        documents = [{k: doc[k] for k in ("key", "title", "folder", "content_bytes", "content_hash")} for doc in bundle["documents"]]
        return {"mode": "preview", "all_delivered": False, "profile": settings["profile"],
                "root_name": settings["root_name"], "root_configured": bool(settings["root_token"]),
                "folder_discovery": settings["folder_discovery"], "documents": documents,
                "notification": {"requested": bundle["notification"] is not None, "enabled": settings["send_self_enabled"],
                                 "sender": settings["message_sender"]},
                "note": "No network calls or remote changes; apply requires explicit configuration and --apply."}
    if not settings["enabled"]:
        raise ValueError("Feishu delivery is disabled; configure the destination before applying")
    if settings["folder_discovery"] == "managed_only" and not settings["root_token"]:
        raise ValueError("managed_only requires the token of an explicitly created dedicated root folder")
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("publication project root does not exist")
    publisher = Publisher(root, settings, runner or run_bounded, command or find_cli())
    with _lock(publisher.directory):
        try:
            publisher.start()
        except DeliveryFailure as exc:
            return {"mode": "apply", "all_delivered": False, "state": exc.state, "detail": exc.detail,
                    "documents": [], "notification": {"state": "blocked_documents"}}
        documents = [publisher.publish_document(doc) for doc in bundle["documents"]]
        notification = publisher.notify(bundle["notification"], documents)
        delivered = all(doc["state"] in GOOD_DOCUMENT_STATES for doc in documents) and notification["state"] in {"sent", "unchanged", "not_requested"}
        return {"mode": "apply", "all_delivered": delivered, "documents": documents,
                "notification": notification, "manifest": str(publisher.path),
                "root": {"name": settings["root_name"], "token": publisher.root_folder,
                         "url": publisher.account["root"].get("url")}}
