# RELEASE v0.6.42 — Natural Owner Policy Rule Routing

## Fixed
- `เวลางานเลยกำหนด ให้ถามวันที่คาดว่าจะเสร็จ` was previously routed to the overdue task-list command.
- `เวลางานติดปัญหา ให้ถามว่าติดตรงไหน` previously fell through to the generic help menu.
- Owner natural-language wording rules now have precedence over generic keyword commands.

## New runtime policy commands
- `เวลางานเลยกำหนด ให้ถามวันที่คาดว่าจะเสร็จ`
- `เวลางานติดปัญหา ให้ถามว่าติดตรงไหน`
- `เวลางานรอคนตอบ ให้ถามว่าตอบกลับหรือยัง`
- `เวลางานรอเอกสาร ให้ถามว่าเอกสารกลับมาหรือยัง`
- `เวลางานรออนุมัติ ให้ถามว่าอนุมัติหรือยัง`
- `เวลางานรอของ ให้ถามว่าของเข้าหรือยัง`
- `ดูกติกาคำถามติดตาม`
- `ล้างกติกาคำถามติดตาม`

## Safety
State-specific wording changes affect Message Generation only. They cannot change task intent, matching, completion safety, status or due dates.

## Database
No schema migration. Reuses `owner_preferences.followup_policy` JSON and adds `state_questions` inside the JSON document.

## Environment variables
None.

## Verification
- `python -m py_compile`: PASS
- Automated/regression tests: 51 passed, 0 failed
- New state-rule tests: 6 passed
- ZIP integrity: PASS
- Full FastAPI import was not run in the offline verification container because `apscheduler` is not installed there; Render installs it from `requirements.txt`.

## Manual tests after deploy
1. `เวลางานเลยกำหนด ให้ถามวันที่คาดว่าจะเสร็จ`
   Expected: confirm policy update; MUST NOT show overdue task list.
2. `เวลางานติดปัญหา ให้ถามว่าติดตรงไหน`
   Expected: confirm policy update; MUST NOT show help menu.
3. `ดูกติกาคำถามติดตาม`
   Expected: show both saved rules.
4. Preview an overdue task.
   Expected question: `ตอนนี้คาดว่าจะเรียบร้อยได้ประมาณเมื่อไหร่คะ` (subject to owner tone transformation).
5. Preview a task whose current progress says it is blocked.
   Expected question: `ตอนนี้ยังติดตรงส่วนไหนอยู่ไหมคะ`.
6. `งานเลยกำหนด`
   Expected: still lists overdue tasks normally; it is not mistaken for a policy command.

## Rollback
Redeploy v0.6.41. The extra `state_questions` JSON key is ignored by older code and does not require database rollback.
