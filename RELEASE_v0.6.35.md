# v0.6.35 – Quoted Completion Ack & Stale Snapshot Consistency Fix

## Fixes
- Quote reply `เรียบร้อยแล้ว` now acknowledges based on the committed quoted-reply result, not stale AI `status_signal`.
- Prevents reminders from showing completion-like `progress_summary` such as `จ่ายเรียบร้อยแล้ว` while asking for more progress.
- No database migration.

## Safety retained
- Questions and negations never complete tasks.
- Milestone-only updates never complete whole tasks.
- Free-chat `เรียบร้อยแล้ว` without an exact quote remains conservative.
