# v0.6.36 – Passive Conversation / False Task Guard

## Problem fixed
Ordinary work conversation such as:
`ไม่ได้สรุปครับ เขาให้พิมพ์ในหนังสือแค่เพิ่มเติมอุปกรณ์ 1 จุด`
was being interpreted as task-related automation. This could create false FU items,
attach chatter to an unrelated task, or cause future reminders.

## Changes
- Added deterministic passive-conversation guard before task automation.
- New FU auto-creation now requires an explicit work-request signal.
- Clear signals include `งานใหม่:`, assignment/request wording such as `ช่วย...`,
  `ฝาก...`, `รบกวน...`, or a direct actionable @mention.
- Ordinary factual reports/chatter are ignored by task automation and remain only in
  message history.
- Exact quoted replies to assistant reminders are still processed normally.
- Existing intent/completion/duplicate/reminder features are preserved.

## No database migration
None.

## No new environment variables
None.

## Rollback
Deploy v0.6.35 again. No schema rollback is required.
