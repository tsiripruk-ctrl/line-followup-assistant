# v0.6.38 — General Conversation & Clarification Guard

Base: v0.6.37

## Root cause
`STATUS_QUERY` was being used both for real task-status questions and ordinary Thai questions. When no task matched, the handler always replied with a project/task clarification. This exposed bot behavior in normal group conversation.

## Changes
- Unmatched generic STATUS_QUERY now stays silent.
- Explicit FOLLOW_UP / `ติดตามงาน:` may still ask a short natural clarification.
- Directed @mention work-question fallback remains unchanged.
- Existing task match and ambiguous-task choice logic remain unchanged.
- Added health flags for the new guard.

## No migration
No database schema change. No new environment variables.

## Manual tests
1. `ขอบอกว่าตัวที่เป็นแผงแยกสว่างกว่าจริงหรือเปล่า` -> no bot reply, no FU.
2. `จริงไหม จากสายตาเราดู` -> no bot reply, no FU.
3. `เรื่องมิเตอร์ถึงไหนแล้ว` with a confidently matching FU -> status response from that FU.
4. `ขออัปเดตเรื่องนี้หน่อย` with no match -> natural clarification is allowed.
5. `@พี่ตั้ม ค่าบริการซ่อมรถ เราได้ตีราคาซ่อมมาทั้งหมดแล้วถูกไหม?` with no existing match -> v0.6.37 directed-new-work behavior remains available.

## Rollback
Deploy v0.6.37. No DB rollback is required.
