# v0.6.44 – Task-linked Reply Identity & Bare Quote Completion

## Root cause fixed
A task-specific status response such as `เรื่อง จ่ายค่าประกันรถ ยังอยู่ระหว่างติดตามค่ะ` was sent with LINE reply/push but its returned LINE message id was not stored in `outbound_task_messages`. If the user quoted that assistant message and replied `เรียบร้อย`, `quotedMessageId` therefore could not resolve back to the task. Separately, the quoted-completion whitelist did not include the bare natural word `เรียบร้อย`.

## Changes
- Every single-task STATUS_QUERY response is now linked to the exact Task via `OutboundTaskMessage`.
- Existing-task FOLLOW_UP / duplicate-follow-up responses are linked too.
- Exact quote replies accept natural whole-task confirmations including `เรียบร้อย`, `เสร็จ`, `เสร็จเรียบร้อย`, polite suffix variants, and the existing longer forms.
- Question, negation, waiting and milestone safety still have absolute priority.
- `ส่งเอกสารแล้ว` remains progress only and cannot close the parent task.
- No database migration and no new environment variables.

## Health flags
- `task_linked_outbound_reply: true`
- `bare_quoted_completion: true`
- `status_reply_quote_completion: true`

## Manual test
1. Ask status of an OPEN task so the assistant replies e.g. `เรื่อง ... ยังอยู่ระหว่างติดตามค่ะ`.
2. Quote/reply to that newly generated assistant message with `เรียบร้อย`.
3. Expected: exact task => COMPLETED, `next_reminder_at` cleared, no unrelated-task diagnostic.
4. Repeat with `เสร็จเรียบร้อย` => COMPLETED.
5. Quote/reply `เรียบร้อยหรือยัง` => must NOT complete.
6. Quote/reply `ยังไม่เรียบร้อย` => must NOT complete.
7. Quote/reply `ส่งเอกสารแล้ว` => progress only, must NOT complete.

## Important
Messages sent before v0.6.44 may not have their outbound LINE message id stored. For a clean smoke test, first request the task status after deploying v0.6.44, then quote the new assistant response.

## Legacy recovery
For task-specific assistant status responses sent before v0.6.44, where the outbound LINE id was never stored, a bare completion quote can recover only when the same human referenced exactly one active task in that group within the previous 30 minutes. If there is more than one candidate, the system refuses to guess.
