# Changelog

## 2026-11-19

### Daily and weekly summary notifications

- Restored the 21:00 daily summary notification and same-run notification after a verified weekly review, using concise self-messages and real document links.
- Kept 09:30/13:30 daytime reviews, one saved document per report date, flexible weekly catch-up and stable notification keys; the 18:00 review and unsolicited evening work planning remain removed.
- Supersedes the previous silent-archive and deferred-weekly-notification preference; generation alone still does not prove message delivery.

## 2026-11-18

### Two reminder windows and silent archiving

- Changed the recommended reminder cadence to 09:30 and 13:30; removed the 18:00 review and unsolicited evening work planning.
- Kept one 21:00 daily summary as a silent archive, with no Feishu or original-chat recap. A weekly report generated outside the reminder windows waits for the next window to notify and confirm delivery.

### Weekly review in the single evening summary

- Added opt-in weekly reviews available from Friday evening, with weekend or next-online-run catch-up rather than requiring Friday 21:00. They aggregate the target week's verified private journal facts and preserve coverage gaps.
- Added `daily-review --weekly` for a manual offline preview. Weekly content reuses the date's review Markdown/JSON and Feishu review/notification keys rather than creating another document.
- Added read-only pending status, due-review generation and receipt-verified completion. A report retains its target Friday date and review/notification keys across weekend or later retries.
- Kept the weekly view concise: up to three results, two risks or unfinished items and three next-week priorities. Recurrence still requires the existing saved host task, not a Python daemon or another automation.

## 2026-11-11

### Concise routine output

- Simplified human reports to achievements, material blockers and next-day actions, retaining complete evidence in private machine records.
- Changed the routine workflow to numbered daytime reminders and one saved end-of-day summary per date; removed automatic daytime cloud overviews.
- Added notification-only publishing without cloud file access and `plan --preview` without Markdown or ICS output.
- Preserved historical documents and the existing safeguards for sender identity, deduplication and uncertain outcomes.

### Feishu workspace delivery

- Added `publish-feishu` with an offline preview and an explicitly enabled apply mode for reviewed documents in a dedicated personal workspace.
- Added private publication receipts, stable document updates, complete content readback, conflict checks and safeguards against duplicate creation after uncertain results.
- Added optional self-notifications from an explicitly selected and verified Feishu bot, while document and source operations continue to use the user's identity.
- Documented folder categories, partial authorization, notification delivery and project navigation.
- Preserved inline-code literals during full-content verification and recognized a single CLI error after diagnostic lines without saving raw error text or changing successful-response parsing.

## 2026-11-10

### Evidence-backed daily reviews

- Added an evidence-backed daily work journal with `log-work` and `daily-review`, Markdown and JSON reports, and separate storage for facts and suggestions. Logging work does not change task completion.
- Extended the skill to summarize relevant Codex work chats, plan morning priorities, check afternoon changes, review remaining work, and prepare an end-of-day report with practical learning and risk suggestions.
- Added a bounded Feishu message-history fallback using existing read permissions when the CLI shortcut only lacks reaction access. Its coverage remains partial: at most 10 active non-bot chats and 100 messages within a shared 45-second / 2 MiB budget.
- Fixed supported CLI response shapes, empty task lists, pagination interpretation, and the required `INBOX` selector for mail lists. Mail IDs do not imply access to subjects or bodies.
- Updated Chinese and English setup, scheduling, data-contract, authorization, and privacy guidance. Dot remains optional; local ICS files remain calendar drafts.
