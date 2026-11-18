# Codex Personal Assistant

**Dot is optional.** The recommended starting point is a saved local Codex schedule that delivers briefs to your chosen chat. Dot computer access is separate from this local workflow.

[中文](README.md)

Use relevant Codex work, Feishu, email and calendars for concise numbered daytime reminders and one nightly summary of results and tomorrow’s plan. Codex handles reasoning; Python retains private state and evidence. No separate OpenAI API key or background server is required.

This is an independent community project. Feishu access uses the official [lark-cli](https://github.com/larksuite/cli); this project is not an official OpenAI or Feishu product.

## What it does

- Reads authorized Feishu/Codex activity, email and calendars through the official CLI and connected host tools.
- Keeps stable task IDs, completion feedback, real deadlines and capacity checks in private records.
- At 09:30/13:30, sends only 1–3 numbered self-only reminders, roughly 80–160 Chinese characters; no meaningful change means one next action, with no new document.
- At 21:00, archives and notifies the user about one `YYYY-MM-DD 工作总结`: up to three results, two concerns and three next-day items, around 250–400 Chinese characters. The notification includes a short summary and verified document URL, without evening work assignments.
- Supports a flexible weekly review on Friday evening or the weekend, with catch-up on the next assistant run after downtime. Each week has one review, always merged into that week's Friday summary. Historical files remain untouched.
- Runs from a saved local schedule without Dot. Delivery is disabled until authorized and configured.

Collection is not an accomplishment. Codex reviews private raw results before extracting actions. Mail listing is limited to INBOX IDs; unauthorized bodies stay unread. Assistant delivery/self-checks are not product-development results, and failed reads are not empty success.

## Quick start

Python 3.9+ is required. The ledger, planner and tests use only the standard library. For AI reasoning, open the repository in a Codex environment that can access its files. Service connections are optional and independent.

```sh
git clone https://github.com/SUSTechWLA/codex-personal-assistant.git
cd codex-personal-assistant
python3 assistant.py init
python3 assistant.py status
python3 assistant.py plan --preview
```

On Windows, use a working Python command or the launcher, which can also locate an installed Codex Python runtime:

```powershell
.\run-assistant.ps1 init
.\run-assistant.ps1 status
.\run-assistant.ps1 plan --preview
```

`init` creates a configuration and an empty ledger. It does not add demo tasks, authenticate services or create schedules. Edit the generated `config.json` for your identity, time zone and working hours; see [config.example.json](config.example.json). Defaults are weekdays, 09:00–12:00 and 13:00–18:00, with 70% of available time allocated.

The example’s `output` fields record preferences for the skill and schedule. Python does not dynamically enforce every time or length field; compact reviews use fixed item limits and short-sentence rules. `weekly_enabled` defaults to false and ISO `weekly_day` defaults to 5 (Friday). The flexible catch-up workflow below does not require the computer to be online at 21:00 on Friday. This setting does not create a schedule.

Install the skill after reviewing its destination:

```sh
python3 install_skill.py --dry-run
python3 install_skill.py
```

The default destination is `$CODEX_HOME/skills/personal-assistant`, or `~/.codex/skills/personal-assistant` when `CODEX_HOME` is unset. Existing skills are not overwritten by default; use `--name my-work-assistant` for a separate installation. The installer binds the current project path but does not authenticate apps or create schedules. See `python3 install_skill.py --help` for options.

Then ask Codex:

> Use $personal-assistant to check available work sources, plan today's priorities, and identify anything that needs confirmation.

Use your chosen skill name if you installed it under another name.

## Connect services

Follow [Feishu setup](docs/setup-feishu.md) for the official CLI and explicit read-only scopes. Enterprise accounts may require administrator approval. A configured app or ready bot identity does not prove personal data access is authorized. No desktop clicking, private client database access or cookie extraction is used.

The search-shortcut and detailed-mail scopes are optional enhancements; basic collection can run without these four additional scopes. In CLI `1.0.99`, the search shortcut requires `im:message.reactions:read` during its precheck even with `--no-reactions`. Only when that specific scope is missing, the collector uses the official CLI's basic GET chat-history endpoint under existing message-read permissions: at most 10 recently active non-bot chats, 20 messages per chat and 100 total, within the message source's 45-second / 2-MiB budget. Coverage is always `partial`, not global search. The fallback does not authorize, switch to a bot, download software or bypass server-side permissions.

The mail-list scope `mail:user_mailbox.message:readonly` alone does not allow reading subjects, addresses or bodies. Without the three additional detail scopes, collection retains only `INBOX` IDs and leaves bodies unread. The [setup guide](docs/setup-feishu.md) separates basic and optional access. Do not substitute `--recommend`, which requests all business domains, for an explicit scope selection.

Example collection window; replace the dates with your intended range:

```sh
python3 assistant.py collect-feishu \
  --start '2026-11-09T00:00:00+08:00' \
  --end '2026-11-11T00:00:00+08:00' \
  --profile codex-assistant
```

Each source is bounded to 45 seconds and 2 MiB, with at most four sources in parallel. Message, task and inbox mail lists request at most five pages; an agenda whose pagination cannot be verified remains partial. The window applies to message search and agenda; task/inbox list coverage is described separately in `scope`. A single collection window may span at most 31 days.

For Gmail and Google Calendar, connect their tools in Codex. The skill organizes retrieval and extraction; these Python scripts do not call Google APIs directly.

## Run automatically

Save a local Codex schedule; Dot is optional. Installing the skill does not create a schedule. For example:

> On workdays at 09:30 and 13:30, send 1–3 numbered reminders to my Feishu assistant conversation, roughly 80–160 Chinese characters; if nothing changed, give one next action. Save no ordinary daytime documents. Cancel the 18:00 review; I plan my evenings. At 21:00, archive one “YYYY-MM-DD 工作总结” and notify me with a concise summary and its verified document URL. Notify me in the same run when an enabled weekly review is generated and verified, without waiting for another daytime window. Keep at most three results, two concerns and three next-day items, around 250–400 characters. Omit implementation details and assign no evening work. Keep a concise original-chat fallback for daytime reviews and summary delivery, and verify the saved schedule, time zone and destination.

Adjust times to the actual workday and verify the task in Scheduled. Use `plan --preview` JSON for internal daytime planning; it creates no Markdown or ICS. The original `plan` remains available only for a separately requested calendar draft. Local work needs the computer online with the desktop app running; API success does not guarantee a system alert.

For automatic weekly reviews, set `output.weekly_enabled` to true, `weekly_day` to 5 and `weekly_due_time` to `18:00`, then update the existing host task: generate when available from Friday evening; if unfinished, catch up on the weekend or the next assistant run after downtime. Each week has one review with `report_date` fixed to the target week's Friday. Weekend or later retries update that Friday's document and notification keys rather than creating a report for the execution date. Optional `weekly_start_date` limits the first eligible week; the public example defaults to null. Weekend runs only handle a pending weekly review, without ordinary daytime reminders.

Retain a short account of the target Friday's verified results, then keep the weekly view around 400–600 Chinese characters: at most three results, two risks/unfinished items and three next-week priorities. Aggregate private journal facts for the target week's configured workdays, keeping a delayed prior-week report separate from the new week's facts and recommendations. An early Friday report states its actual collection cutoff. Preserve material coverage gaps; do not concatenate daily logs, treat a proposal as an implementation, or treat simulation as hardware acceptance. Reruns reflect the latest verified corrections rather than reconstructing a historical point in time. Manual offline generation reuses the target Friday's review files, without another schedule or weekly document. Technical detail belongs in authorized existing requirements/technical documents; full evidence and machine JSON remain private.

A Dot can coordinate this only after separate computer connection and destination verification. A saved local schedule does not prove Dot access.

## Deliver results to personal Feishu

The 09:30/13:30 reminder bundles use `documents:[]` plus a valid notification, without accessing cloud files or a root. At 21:00, publish one summary under `brief:<date>:review` in `01 每日工作/YYYY/MM`, then notify the user with a concise summary and verified document URL. Ordinary reminder keys are `notify:<date>:morning|changes`; daily and weekly summary notifications use the report date's review key. A generated and verified weekly review notifies in the same run, including nightly or weekend catch-up, without waiting for a daytime window. Preserve historical files and navigation.

For an existing review key, retain the manifest-bound title, folder and token; do not create another key to rename an older report. New dates default to “YYYY-MM-DD 工作总结”; a weekly report may use “YYYY-MM-DD 工作总结（含周报）”. Weekly `report_date` is fixed to the target Friday. Retain `brief:<report_date>:review` and `notify:<report_date>:review` across retries rather than changing dates or adding a weekly key/navigation update. The one-document limit applies only to routine assistant output, not separately requested requirements/technical documents. Preserve existing requirements documents.

Create the ignored data directory and save reviewed input as `data/bundle.json`:

```sh
python3 assistant.py publish-feishu data/bundle.json
```

This is a network-free preview with no body output. Add `--apply` only when the authorized destination and configuration are ready. Self-only bot delivery verifies the same app/profile and exact bot name; data reads and nightly documents retain user identity. Conflicts/unknown outcomes stop for verification. Confirm document and message results separately and do not promise OS alerts. See [concise Feishu delivery](docs/feishu-delivery.md) for configuration and both bundle forms.

## Data and boundaries

Runtime data is private by default and ignored by Git:

| Path | Contents |
| --- | --- |
| `config.json` | Local preferences |
| `assistant.sqlite3` | Ledger and history |
| `data/journal.sqlite3` | Daily evidence-backed work records and suggestions |
| `data/feishu-delivery/` | Private managed-document mapping, content state and publish lock |
| `data/feishu/` | Source snapshots |
| `reports/` | One compact nightly summary and private full machine JSON; other drafts only when separately requested |
| `plans/` | Local ICS drafts |

Inspect staged files before publishing. `.gitignore` does not remove files already tracked by Git.

ICS generation does not write events into an online calendar. Calendar changes still need specific authorization, appropriate write access, current availability checks and verified results; personal document/brief delivery does not authorize meeting changes. Already authorized personal workspace results and self-only briefs do not need repeated permission requests, while official OAuth identity steps still belong to the user. The project does not automatically message coworkers, send email or meeting invitations, or publicly share work records.

Create the Git-ignored `data/` directory first. Save reviewed task inputs to `data/reviewed-summary.json` and work-log inputs to `data/reviewed-work.json`; do not put real work imports in the repository root.

```sh
mkdir -p data
python3 assistant.py ingest data/reviewed-summary.json
python3 assistant.py log-work data/reviewed-work.json
python3 assistant.py update 'source:stable-task-id' --status done
python3 assistant.py plan --preview --date 2026-11-11
python3 assistant.py daily-review --date 2026-11-11
python3 assistant.py daily-review --weekly --date 2026-11-13
python3 assistant.py weekly-status
python3 assistant.py daily-review --weekly-due
python3 -m unittest discover -s tests -v
```

In PowerShell, create the directory with `New-Item -ItemType Directory -Force data | Out-Null`. Task imports and work logs are independent: logging an activity does not mark a task done. Codex must review inputs before `log-work`; `daily-review` does not invent work or learning needs. It writes `reports/YYYY-MM-DD-review.md` and a matching JSON file. See [architecture and data contracts](docs/architecture.md) for fields and a synthetic example.

Run `weekly-status` before collection; on weekends, stop if no review is pending. For `state=delivered_unacknowledged` or `needs_completion=true`, save the returned `receipt` unchanged as private JSON and call `weekly-complete` without publishing again. For `delivery_check_required`, stop writes/sends and verify the uncertain result. Only `due=true` calls `daily-review --weekly-due`; use its `weekly_delivery.notification_text` unchanged and let the publisher append the document URL. Pending content/hash are frozen across recovery. After verifying the full document and self-notification, confirm with `weekly-complete --week-start YYYY-MM-DD --receipt data/actual-delivery-receipt.json`. A daily report, local generation or preview is not delivery. After completion, an evening update may preserve the weekly content in the same document without another notification. Optional `--now` supports controlled-time testing.

## Validation and limitations

Tests cover atomic imports, deduplication and terminal state protection, calendar snapshot safety, time zones, deadlines, capacity, ICS escaping and bounded simulated CLI collection, including empty-result and pagination envelopes. Daily-review tests cover completion evidence, date isolation, stale observations, transaction rollback, suggestion limits and independence from task status. Each real account must verify individual sources after the required approvals and user OAuth are complete. Passing tests or successfully reading one endpoint does not establish access to every Feishu source or verify online calendar writes.

Search matches are not complete conversations, and a primary calendar is not every work calendar. Model extraction can be wrong. Uncertain owners and dates stay pending confirmation. On runtimes without IANA time-zone data, Asia/Shanghai and UTC have built-in fallbacks; other zones may require OS time-zone data or `tzdata`.

See the [changelog](CHANGELOG.md) for updates. Contributions are welcome under the [MIT License](LICENSE). Use synthetic examples in issues and remove account identifiers, personal messages, tokens and authorization links before sharing diagnostics.

Daytime reviews remain at 09:30/13:30. The 21:00 daily summary and a generated weekly review also notify the user; they do not wait for the next daytime window. Reuse the original pinned document plus unchanged `weekly_delivery.notification_text` as a full bundle, verify the document and append its real URL, and retain the same keys. Archiving alone is not notification delivery. Confirm completion only after document and self-message verification; do not resend an already delivered key. Friday 18:00 is an eligibility threshold, not an extra review or evening work assignment.
