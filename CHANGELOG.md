# Changelog

## 2026-11-10

- Added an evidence-backed daily work journal with `log-work` and `daily-review`, Markdown and JSON reports, and separate storage for facts and suggestions. Logging work does not change task completion.
- Extended the skill to summarize relevant Codex work chats, plan morning priorities, check afternoon changes, review remaining work, and prepare an end-of-day report with practical learning and risk suggestions.
- Added a bounded Feishu message-history fallback using existing read permissions when the CLI shortcut only lacks reaction access. Its coverage remains partial: at most 10 active non-bot chats and 100 messages within a shared 45-second / 2 MiB budget.
- Fixed supported CLI response shapes, empty task lists, pagination interpretation, and the required `INBOX` selector for mail lists. Mail IDs do not imply access to subjects or bodies.
- Updated Chinese and English setup, scheduling, data-contract, authorization, and privacy guidance. Dot remains optional; local ICS files remain calendar drafts.
