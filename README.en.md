# Codex Personal Assistant

**Dot is optional.** The recommended starting point is a saved local Codex schedule that delivers briefs to your chosen chat. Dot computer access is separate from this local workflow.

[中文](README.md)

Combine relevant daily Codex work, Feishu, email and calendars into morning priorities, evidence-backed accomplishments and an end-of-day review with practical learning and risk suggestions. Codex handles reasoning; a Python standard-library CLI handles local records, validation and planning. No separate OpenAI API key or background server is required.

This is an independent community project. Feishu access uses the official [lark-cli](https://github.com/larksuite/cli); this project is not an official OpenAI or Feishu product.

## What it does

- Collects Feishu search results or bounded chat history, the user's primary calendar, assigned incomplete tasks and message IDs from the user's `INBOX` through the official CLI, always as the user.
- Uses connected Gmail / Google Calendar tools in Codex for those services; it does not implement a separate Google OAuth client.
- Reads relevant daily Codex work through dynamically discovered host thread tools, bounded to 20 chats and the latest 3–5 turns per chat, then verifies selected results and artifacts.
- Stores tasks, goals, events, source coverage and change history in a local SQLite database.
- Deduplicates stable task IDs, protects completion feedback, and plans around known meetings, breaks, past time and configured workdays.
- Produces Chinese daily reports and local ICS drafts, with uncertainty, waiting items and deadline risks.
- Keeps a separate work journal and end-of-day review: verified completions, ongoing work, blockers, up to three next-day priorities, and 1–2 evidence-triggered learning suggestions with a 15–30-minute exercise.
- Supports a reusable Codex skill and saved schedules; a Dot can coordinate the workflow after the user connects a computer.

Collection does not automatically become a task list. `collect-feishu` saves read-only raw snapshots and imports only source coverage. Codex must review the results and ingest extracted tasks and actual calendar events. Mail collection is limited to `INBOX` and does not include full email bodies; reading subjects, addresses and bodies requires the corresponding additional read scopes. Failed reads are reported as unavailable, not empty success.

## Quick start

Python 3.9+ is required. The ledger, planner and tests use only the standard library. For AI reasoning, open the repository in a Codex environment that can access its files. Service connections are optional and independent.

```sh
git clone https://github.com/SUSTechWLA/codex-personal-assistant.git
cd codex-personal-assistant
python3 assistant.py init
python3 assistant.py status
python3 assistant.py plan
```

On Windows, use a working Python command or the launcher, which can also locate an installed Codex Python runtime:

```powershell
.\run-assistant.ps1 init
.\run-assistant.ps1 status
.\run-assistant.ps1 plan
```

`init` creates a configuration and an empty ledger. It does not add demo tasks, authenticate services or create schedules. Edit the generated `config.json` for your identity, time zone and working hours; see [config.example.json](config.example.json). Defaults are weekdays, 09:00–12:00 and 13:00–18:00, with 70% of available time allocated.

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

A skill does not run continuously by itself. Ask Codex / ChatGPT to save a schedule, for example:

> Every weekday at 09:00, 14:00, 18:00 and 21:00 in my time zone, run $personal-assistant for a morning plan, change check, progress review and end-of-day report. Use relevant daily Codex work and available sources. Deliver a concise final brief in this chat on each run, with evidence-backed results, next actions and relevant time blocks. Include up to two learning suggestions tied to actual work in the end-of-day report. Do not only save files or invent accomplishments. Confirm the saved time zone, schedule and destination.

These times are examples for a workday ending at 21:00; align the final review with your own workday. They are not schedules installed by this repository. Verify the saved task in Scheduled. Desktop alerts also depend on app and OS notification settings. Local steps require the computer online with the desktop app running.

Morning plans weigh real deadlines, priorities, known meetings and remaining capacity. The 14:00 check adjusts meaningful changes; the 18:00 review covers progress and remaining work, and the final review separates verified completions, ongoing work, blockers, next-day priorities, learning and risk notes, and coverage gaps.

The host tools provide Codex activity; Python does not parse private app databases. Retain only relevant `userMessage`, `final_answer` and selected artifact references, ignoring reasoning, command text and tool metadata. An ended thread, file mtime or dirty working tree is not delivery evidence. Assistant self-checks and automated briefs are not the user's product-development accomplishments. Work records remain local.

Learning suggestions need a concrete trigger from that day's project, failed check, repeated problem or changed module, a reason, and a small verifiable 15–30-minute exercise. They do not automatically become tasks or OKRs. Without sufficient evidence, the report states the gap instead of inventing accomplishments or declaring that no work happened.

A Dot can coordinate ongoing responsibilities once its user connects the computer. Connection, schedule and delivery must be verified separately; this is not a one-click Dot integration. See [Dot and proactive updates](docs/dot-setup.md).

## Data and boundaries

Runtime data is private by default and ignored by Git:

| Path | Contents |
| --- | --- |
| `config.json` | Local preferences |
| `assistant.sqlite3` | Ledger and history |
| `data/journal.sqlite3` | Daily evidence-backed work records and suggestions |
| `data/feishu/` | Source snapshots |
| `reports/` | Daily Markdown reports |
| `plans/` | Local ICS drafts |

Inspect staged files before publishing. `.gitignore` does not remove files already tracked by Git.

ICS generation does not write events into an online calendar. Online actions require explicit user authorization, appropriate write access, current availability checks and verified results. Initial read-only access does not provide calendar write permissions. The project does not automatically email coworkers, send Feishu messages or invite attendees.

Create the Git-ignored `data/` directory first. Save reviewed task inputs to `data/reviewed-summary.json` and work-log inputs to `data/reviewed-work.json`; do not put real work imports in the repository root.

```sh
mkdir -p data
python3 assistant.py ingest data/reviewed-summary.json
python3 assistant.py log-work data/reviewed-work.json
python3 assistant.py update 'source:stable-task-id' --status done
python3 assistant.py plan --date 2026-11-11
python3 assistant.py daily-review --date 2026-11-11
python3 -m unittest discover -s tests -v
```

In PowerShell, create the directory with `New-Item -ItemType Directory -Force data | Out-Null`. Task imports and work logs are independent: logging an activity does not mark a task done. Codex must review inputs before `log-work`; `daily-review` does not invent work or learning needs. It writes `reports/YYYY-MM-DD-review.md` and a matching JSON file. See [architecture and data contracts](docs/architecture.md) for fields and a synthetic example.

## Validation and limitations

Tests cover atomic imports, deduplication and terminal state protection, calendar snapshot safety, time zones, deadlines, capacity, ICS escaping and bounded simulated CLI collection, including empty-result and pagination envelopes. Daily-review tests cover completion evidence, date isolation, stale observations, transaction rollback, suggestion limits and independence from task status. Each real account must verify individual sources after the required approvals and user OAuth are complete. Passing tests or successfully reading one endpoint does not establish access to every Feishu source or verify online calendar writes.

Search matches are not complete conversations, and a primary calendar is not every work calendar. Model extraction can be wrong. Uncertain owners and dates stay pending confirmation. On runtimes without IANA time-zone data, Asia/Shanghai and UTC have built-in fallbacks; other zones may require OS time-zone data or `tzdata`.

See the [changelog](CHANGELOG.md) for updates. Contributions are welcome under the [MIT License](LICENSE). Use synthetic examples in issues and remove account identifiers, personal messages, tokens and authorization links before sharing diagnostics.
