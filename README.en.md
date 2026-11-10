# Codex Personal Assistant

**Dot is optional.** The recommended starting point is a saved local Codex schedule that delivers briefs to your chosen chat. Dot computer access is separate from this local workflow.

[中文](README.md)

Turn Feishu, email and calendar context into sourced tasks, priorities, daily time blocks and reviews inside Codex. Codex handles reasoning; a Python standard-library CLI handles the local ledger, validation and planning. No separate OpenAI API key or background server is required.

This is an independent community project. Feishu access uses the official [lark-cli](https://github.com/larksuite/cli); this project is not an official OpenAI or Feishu product.

## What it does

- Collects Feishu message search results, the user's primary calendar, assigned incomplete tasks and mailbox message lists through the official CLI, always as the user.
- Uses connected Gmail / Google Calendar tools in Codex for those services; it does not implement a separate Google OAuth client.
- Stores tasks, goals, events, source coverage and change history in a local SQLite database.
- Deduplicates stable task IDs, protects completion feedback, and plans around known meetings, breaks, past time and configured workdays.
- Produces Chinese daily reports and local ICS drafts, with uncertainty, waiting items and deadline risks.
- Supports a reusable Codex skill and saved schedules; a Dot can coordinate the workflow after the user connects a computer.

Collection does not automatically become a task list. `collect-feishu` saves read-only raw snapshots and imports only source coverage. Codex must review the results and ingest extracted tasks and actual calendar events. Mailbox listing does not include full email bodies. Failed reads are reported as unavailable, not empty success.

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

Example collection window; replace the dates with your intended range:

```sh
python3 assistant.py collect-feishu \
  --start '2026-11-09T00:00:00+08:00' \
  --end '2026-11-11T00:00:00+08:00' \
  --profile codex-assistant
```

Each source is bounded to 45 seconds and 2 MiB, with at most four sources in parallel. Message, task and mail lists request at most five pages; an agenda whose pagination cannot be verified remains partial. The window applies to message search and agenda; task/mail list coverage is described separately in `scope`. A single collection window may span at most 31 days.

For Gmail and Google Calendar, connect their tools in Codex. The skill organizes retrieval and extraction; these Python scripts do not call Google APIs directly.

## Run automatically

A skill does not run continuously by itself. Ask Codex / ChatGPT to save a schedule, for example:

> Every weekday at 09:00, 14:00 and 18:00 in my time zone, run $personal-assistant for a morning plan, change check and progress review. Deliver a concise final brief in this chat on each scheduled run, including the next action and relevant time blocks. Do not only save files. Confirm the saved time zone, schedule and destination.

These are example times, not schedules installed by this repository. Verify the saved task in Scheduled. Desktop alerts also depend on app and OS notification settings. Local steps require the computer online with the desktop app running.

A Dot can coordinate ongoing responsibilities once its user connects the computer. Connection, schedule and delivery must be verified separately; this is not a one-click Dot integration. See [Dot and proactive updates](docs/dot-setup.md).

## Data and boundaries

Runtime data is private by default and ignored by Git:

| Path | Contents |
| --- | --- |
| `config.json` | Local preferences |
| `assistant.sqlite3` | Ledger and history |
| `data/feishu/` | Source snapshots |
| `reports/` | Daily Markdown reports |
| `plans/` | Local ICS drafts |

Inspect staged files before publishing. `.gitignore` does not remove files already tracked by Git.

ICS generation does not write events into an online calendar. Online actions require explicit user authorization, appropriate write access, current availability checks and verified results. Initial read-only access does not provide calendar write permissions. The project does not automatically email coworkers, send Feishu messages or invite attendees.

```sh
python3 assistant.py ingest reviewed-summary.json
python3 assistant.py update 'source:stable-task-id' --status done
python3 assistant.py plan --date 2026-11-11
python3 -m unittest discover -s tests -v
```

See [architecture and data contracts](docs/architecture.md) for the import format.

## Validation and limitations

Tests cover atomic imports, deduplication and terminal state protection, calendar snapshot safety, time zones, deadlines, capacity, ICS escaping and bounded simulated CLI collection. Real Feishu user-data access remains subject to account approval and has not been validated end to end in an environment awaiting enterprise approval. Online calendar writes are not covered by those tests.

Search matches are not complete conversations, and a primary calendar is not every work calendar. Model extraction can be wrong. Uncertain owners and dates stay pending confirmation. On runtimes without IANA time-zone data, Asia/Shanghai and UTC have built-in fallbacks; other zones may require OS time-zone data or `tzdata`.

Contributions are welcome under the [MIT License](LICENSE). Use synthetic examples in issues and remove account identifiers, personal messages, tokens and authorization links before sharing diagnostics.
