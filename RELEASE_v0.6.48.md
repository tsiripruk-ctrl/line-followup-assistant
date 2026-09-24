# v0.6.48 — Owner Forced Follow-up Control

## Goal
Allow the owner to temporarily force repeated follow-up on one specific existing FU task/person when normal reminder limits are too conservative.

## Owner Private commands
- `บังคับติดตาม FU-260924-0012`
  - enables forced follow-up with the default 2-hour interval.
- `บังคับติดตาม FU-260924-0012 ทุก 1 ชั่วโมง`
  - enables forced follow-up with an explicit interval.
- `ปิดบังคับติดตาม FU-260924-0012`
- `หยุดบังคับติดตาม FU-260924-0012`
  - disables forced mode and restores the normal reminder schedule.
- `ดูงานบังคับติดตาม`
  - lists current forced follow-up tasks, assignees, interval and next follow-up time.

## Behavior
- Forced mode bypasses the normal per-task daily reminder cap.
- It still respects the 08:30–17:30 group follow-up window.
- It still respects per-scan and per-group anti-burst limits.
- Default interval is 2 hours; accepted owner intervals are clamped to 1–8 hours.
- Forced reminders continue to use the existing context-aware reminder generator instead of a repeated fixed sentence.
- A human-provided future checkpoint can still move `next_reminder_at` later; after that checkpoint the forced interval resumes.
- Closing a task stops reminders. Supported completion/owner/dashboard close paths clear forced mode.
- Reopening a previously closed task does not silently restore an old forced mode.

## Storage
Forced-follow-up configuration is stored as JSON in the existing `owner_preferences` table under `forced_followup_tasks_v1`. No database migration or new environment variable is required.

## Scheduler / timeline
- Forced messages use `OutboundTaskMessage.message_kind = FORCED_REMINDER`.
- Timeline event: `FORCED_REMINDER_SENT`.
- Owner state changes are recorded as `FORCED_FOLLOWUP_ENABLED` and `FORCED_FOLLOWUP_DISABLED`.

## Regression coverage
- Forced-mode registry persistence and manual disable.
- Safe interval clamping.
- Forced task bypasses normal daily cap.
- Forced task is rescheduled according to the configured interval.
