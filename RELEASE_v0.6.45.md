# v0.6.45 – Quoted Human Message Context Safety Fix

## Problem fixed
A short completion reply such as `เรียบร้อย` / `เสร็จเรียบร้อย` could close the wrong task when the user quoted a **human message** rather than a task-linked assistant message.

The unsafe path in v0.6.44 was the legacy recency fallback: when `quotedMessageId` was not linked to a task, the system could choose the only recently referenced task for the same user. That can be unrelated to the quoted conversation.

Example failure reproduced from production:
- quoted conversation: `เรื่องจ่ายค่าประกันรถ ตอนนี้ไปถึงไหนแล้วคะ`
- reply: `เรียบร้อย`
- unrelated recent task: `ส่งจดหมายร้องเรียนค้ามันให้พี่ก้อย`
- old behavior: unrelated task could be completed.

## New resolution order
Quoted replies are now resolved only from evidence belonging to the **quoted message itself**:
1. `OutboundTaskMessage.line_message_id` → exact task
2. `Task.source_message_id` → exact original task
3. `TaskEvent.message_id` → exact task event for the quoted human message
4. Stored `Message.text` of the quoted human message → high-confidence unique topic match (>= 0.85)
5. Otherwise: do not close any task

The old same-user / recent-task fallback is disabled for quote completion.

## Safety behavior
- Quote insurance question + `เรียบร้อย` → closes the insurance task only when the quoted message resolves to that task.
- Quote unrelated/generic human message + `เรียบร้อย` → no automatic closure.
- Recent activity on another task cannot steal the quoted completion.
- Question / negation / milestone safety from previous releases remains unchanged.

## Health flags
- `quoted_human_message_task_resolution: true`
- `quoted_event_message_id_resolution: true`
- `quoted_text_semantic_resolution: true`
- `unsafe_recent_quote_fallback_disabled: true`
- `legacy_status_quote_recovery: false`

## Database / environment
- No database migration.
- No new environment variables.

## Verification
- `python -m py_compile *.py`: PASS
- Automated/regression tests: 89 passed
- Subtests: 14 passed
- Added regression tests for quoted human messages and wrong-task prevention.

## Manual smoke test after Render deploy
1. In the group, ask: `เรื่องจ่ายค่าประกันรถ ตอนนี้ไปถึงไหนแล้วคะ` and let the assistant/task system process it.
2. Reply/quote that exact human question with `เรียบร้อย`.
3. Expected: the insurance task closes; an unrelated task must remain unchanged.
4. Repeat while another unrelated task was queried recently. Expected: insurance still closes, unrelated task remains open.
5. Quote a generic human message such as `จริงไหมครับ` and reply `เรียบร้อย`. Expected: no task closes.
6. Quote a task-linked assistant reminder and reply `เรียบร้อย`. Expected: exact task closes as before.

## Recovery for a task already closed incorrectly
Use Owner Private Control:
`เปิดงาน FU-xxxxxx-xxxx`
or
`ติดตามต่อ FU-xxxxxx-xxxx เพราะยังไม่เสร็จ`

This reopens the wrongly completed task without deleting its timeline history.
