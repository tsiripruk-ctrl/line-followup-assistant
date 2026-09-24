# v0.6.47 — Mention Resilience & Natural Assignment Fallback

## Problem
Natural assignment text could pass the intent detector but still fail to create a task when the runtime had no usable `primary_human_mention`. This could happen when LINE member-profile enrichment failed or when the user typed/copied a textual `@name` instead of a native LINE mention.

## Fix
- Treat LINE mention metadata as authoritative even when profile lookup fails.
- Add conservative People Registry fallback for a leading textual `@name`.
- Accept only exact registered identity matches; ambiguous/unregistered text is not promoted.
- Support emoji display names such as `@Proud🤍` by trying the exact alias first and a punctuation/emoji-cleaned alias second.
- Keep existing-task-first duplicate prevention unchanged.

## Regression example
`@Proud🤍 มีเรื่องไหมอยากให้ช่วยดู พี่ฝากเรื่อง "งานคอมฯ ศูนย์ข้อมูลความสงบฯ ภ.จว.ระนอง" ว่าเรากู้ไฟแนนซ์มากี่บาท`

With Proud present in People Registry, the message is eligible for NEW_TASK routing when there is no matching active task; if a matching task exists, it becomes a FOLLOW_UP instead.
