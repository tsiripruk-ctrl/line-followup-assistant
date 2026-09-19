# v0.6.46 – New Task Confirmation + Previous Message Recovery

## Problem fixed
A real new assignment could be misunderstood as a status/follow-up question or duplicate clarification. If the assistant then asked for clarification and the user replied only `งานใหม่`, the system analyzed the words `งานใหม่` by themselves and lost the original assignment context.

Production example:
- `@Gmaj7 @ตี๋ โครงการเทศบาลนครระยอง กล้องดับเป็นจำนวนมาก เนื่องจากไฟไหม้สาย ... ต้องให้ประสานให้อิฐเข้าไปดำเนินการแก้ไข`
- Old behavior: could ask `หมายถึงเรื่องไหนคะ`; replying `งานใหม่` did not create the task from the original message.

## Fixes
- Added deterministic structured NEW_TASK detection from action + context evidence.
- Added natural manager action wording such as `ต้องให้ประสาน`, `ต้องประสาน`, `ต้องให้เข้าไป`, `ให้ดำเนินการ`, `ให้แก้ไข`, `ให้ตรวจสอบ`, `ให้ติดตาม`, `ให้จัดการ`, `ให้ส่ง`, `ให้เตรียม`, `ให้นัด`, `ให้เช็ก/เช็ค`.
- Added short-lived per-user/group `conversation_states` context.
- Duplicate ambiguity now stores the original message, message ID, sender and LINE mentions before asking the user.
- When a pending clarification exists, `งานใหม่`, `เป็นงานใหม่`, `สร้างงานใหม่`, `เรื่องใหม่`, `ใช่`, `สร้างเลย` etc. confirm the original message instead of creating a task called `งานใหม่`.
- Explicit confirmation bypasses normal reclassification and duplicate auto-merge, then reprocesses the original message and creates an OPEN task.
- Negative answers such as `ไม่ใช่งานใหม่`, `ไม่ต้องสร้าง`, `แค่ถาม`, `ไม่ต้องติดตาม` clear the pending state without creating a task.
- A standalone `งานใหม่` opens an `AWAITING_NEW_TASK_DETAILS` state; the next substantive message becomes the forced new-task payload.
- Pending context expires automatically after 10 minutes.

## Important Thai question bug fixed
The old question detector used substring matching for `ไหม`, so the word `ไฟไหม้` could accidentally trigger STATUS_QUERY because it contains the letters `ไหม`.

v0.6.46 adds `has_question_signal()` so:
- `เกิดไฟไหม้สายกล้อง` → not a question
- `ไฟไหม้แล้วใช่ไหม` → question

This directly protects the Rayong incident example from being routed into the wrong query flow.

## Duplicate safety
A strong structured new assignment ignores weak resemblance to old tasks. Only very strong duplicate evidence may reuse an existing task. If duplicate evidence is ambiguous, the assistant shows the candidates and explicitly says the user can type `งานใหม่` to force a new task.

## Database
A new additive table is created automatically by SQLAlchemy on startup:
- `conversation_states`

No existing task, timeline, People Registry or PostgreSQL data is deleted or rewritten.

## Health flags
- `new_task_context_recovery: true`
- `new_task_confirmation_override: true`
- `structured_new_task_detection: true`
- `pending_clarification_state: true`

## Verification
- `python -m py_compile *.py`: PASS
- Full automated/regression suite: 96 passed
- Subtests: 14 passed
- Added regression coverage for structured Rayong assignment, `ไฟไหม้` question false-positive, confirmation/negative wording and conversation-state expiry/clear.

## Mobile / LINE smoke test after deploy
1. Send the full Rayong assignment message from the real LINE group.
   - Expected: it is treated as NEW_TASK; it must not ask `หมายถึงเรื่องไหนคะ` because of the word `ไฟไหม้`.
2. Create a deliberately similar open task first, then send another closely related assignment until the duplicate clarification appears.
   - Expected: the assistant shows candidate old tasks and says `ถ้าเป็นงานใหม่ พิมพ์ ‘งานใหม่’ ได้เลยค่ะ`.
3. Reply `งานใหม่`.
   - Expected: a new OPEN task is created from the full previous assignment, not from the literal words `งานใหม่`.
4. Repeat and reply `ใช่` instead.
   - Expected: same recovery behavior.
5. Repeat and reply `ไม่ใช่งานใหม่`.
   - Expected: no new task is created and pending state is cleared.
6. Send `งานใหม่` with no pending clarification.
   - Expected: assistant says `ได้ค่ะ ส่งรายละเอียดงานที่ต้องการติดตามมาได้เลยค่ะ`.
7. Send the task details in the next message.
   - Expected: the details are force-created as a new task.
8. Send `งานนี้เรียบร้อยหรือยัง`.
   - Expected: STATUS_QUERY; never NEW_TASK and never completion.
