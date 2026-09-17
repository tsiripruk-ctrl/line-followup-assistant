# v0.6.31 – Humanized Context-aware Follow-up

## Base
Built from `line-followup-assistant-v0.6.30` without rewriting the application architecture.

## Files changed
- `service.py` — state-driven reminder generator, waiting/appointment/next-action question selection, deterministic variation, Thai `เรียบร้อย` substring safety in progress extraction.
- `main.py` — version/health flags, reminder scan routes all normal reminders through the contextual generator, natural pre-due wording.
- `tests/test_humanized_followup.py` — 10 new regression tests.
- `tests/test_progress_context.py` — adjusted legacy expectation for safe first follow-up when no progress snapshot exists.
- `README.md` — v0.6.31 release notes.

## Database migration
None. v0.6.31 reuses the v0.6.30 fields:
- `progress_summary`
- `waiting_on`
- `next_action`
- `last_progress_at`

## New environment variables
None.

## Automated / regression result
`40 passed, 0 failed` using `pytest`.
`python -m py_compile *.py tests/*.py` passed.

## Runtime verification note
The verification container used to build this release does not have `apscheduler`, `openai`, or `psycopg` installed, so importing the entire FastAPI application in this container is not a valid Render-runtime test. All three dependencies remain declared in `requirements.txt`; Render must install them during deploy. Production acceptance still requires the smoke tests below.

## Main behavior changes
1. Reminder text is driven by Task state, not a generic template.
2. WAITING asks specifically about the unresolved dependency.
3. `next_action` / appointment checkpoints ask about the next step instead of restarting from the original task.
4. OVERDUE asks for the expected completion / blocker.
5. Tasks without progress use the original task safely and briefly.
6. Structured `progress_summary` is trusted only because it is written after exact task resolution; legacy timeline fallback still passes the cross-topic memory guard.
7. No reminder path contains the old phrases `ขออัปเดตเรื่อง...`, `เรื่องที่รออยู่มีความคืบหน้าเพิ่มเติมไหมคะ`, or `ถ้าเรียบร้อยแล้ว รบกวนแจ้ง...`.
8. Variation is deterministic inside the chosen state; it is not random sentence swapping.

## Example reminder scenarios
1. WAITING / Sales
   - State: เปิด PO แล้ว; รอเซลล์ตอบรับ
   - Example: `@MARCH คะ เรื่องเปิด PO กับ Futong งานศาลากลาง ล่าสุด: เปิด PO แล้ว แต่ทางเซลล์ยังไม่ตอบรับ\nตอนนี้ทางเซลล์ตอบกลับมาแล้วหรือยังคะ`

2. WAITING / Officer
   - State: ส่งเอกสารแล้ว; รอเจ้าหน้าที่ตอบ
   - Example: `@Proud คะ เรื่อง Flow Account ล่าสุด: ส่งเอกสารแล้วและกำลังรอเจ้าหน้าที่ตอบกลับ\nตอนนี้เจ้าหน้าที่ตอบกลับมาแล้วหรือยังคะ`

3. WAITING / Document
   - Example question: `ตอนนี้เอกสารที่รออยู่กลับมาแล้วหรือยังคะ`

4. WAITING / Approval
   - Example question: `ตอนนี้ขั้นตอนอนุมัติ/ลงนามผ่านแล้วหรือยังคะ`

5. WAITING / Goods
   - Example question: `ตอนนี้ของที่รออยู่เข้ามาแล้วหรือยังคะ`

6. Appointment / future checkpoint
   - Example: `@ตี คะ เรื่องเซ็นสัญญาโครงการ A วันนี้ถึงช่วงที่นัดไว้ตามอัปเดตล่าสุดแล้วค่ะ\nตอนนี้ดำเนินการเป็นอย่างไรบ้างคะ`

7. IN_PROGRESS
   - Example question: `ตอนนี้เหลือขั้นตอนไหนอีกบ้างคะ`

8. IN_PROGRESS repeat
   - Example question: `จากที่ทำต่อมา ตอนนี้ไปถึงขั้นตอนไหนแล้วคะ`

9. OVERDUE
   - Example question: `ตอนนี้คาดว่าจะเรียบร้อยได้ประมาณเมื่อไหร่คะ`

10. OPEN / no progress yet
   - Example: `@Proud คะ เรื่องตรวจสอบเบอร์ออฟฟิศใช้งานไม่ได้ ตอนนี้ไปถึงไหนแล้วคะ`

## Deploy on Render
1. Back up / keep the current v0.6.30 commit/tag for rollback.
2. Upload/commit the v0.6.31 source over the existing repository.
3. Do not change current environment variables.
4. Deploy latest commit on Render.
5. Wait for build/deploy success.
6. Open `/health` and verify `version = 0.6.31` and the new health flags.
7. Run the LINE smoke tests below before allowing normal reminder traffic.

## Manual LINE smoke tests
### Test A — WAITING Sales
Create: `@MARCH ช่วยเปิด PO Futong ให้หน่อย`
Reply: `เปิด PO แล้ว แต่เซลล์ยังไม่ตอบครับ`
Expected:
- Task stays open/waiting.
- Snapshot records completed PO + waiting Sales.
- Next reminder asks Sales response, not whether PO was opened.

### Test B — Future commitment
Reply: `Sales ตอบแล้ว นัดส่งวันศุกร์`
Expected:
- Snapshot is refreshed.
- Old `ยังไม่ตอบรับ` is not used as current state.
- `next_followup_at/next_reminder_at` follows the date-aware logic.
- No reminder before Friday.
- Friday reminder asks about the scheduled delivery/checkpoint.

### Test C — Ambiguous chat
Send: `เดี๋ยวดูให้อีกทีครับ`
Expected: no public group acknowledgement if no task can be resolved safely.

### Test D — Completion
Reply to the exact task reminder: `ดำเนินการเรียบร้อยทั้งหมดแล้วครับ`
Expected: only that task becomes COMPLETED, short acknowledgement, reminders stop.

### Test E — Cross-task isolation
Give the same assignee two unrelated open tasks. Update only one.
Expected: the other task's reminder must not contain the first task's progress/waiting text.

### Test F — Working hours
Expected: normal group reminders only 08:30–17:30 Asia/Bangkok, preserving v0.6.30 behavior.

## Rollback
Deploy the previous v0.6.30 commit/tag. There is no v0.6.31 database migration to reverse.

## Known issues / production cautions
- Full Render runtime integration was not reproducible in the offline verification container because some deployment dependencies are not installed there. Verify Render build logs and `/health` after deployment.
- Humanized wording is deterministic and rule/state-driven; it is intentionally not an LLM free-writing call on every reminder, to reduce hallucination and cross-task contamination.
- If a task's structured progress snapshot is already wrong from historical data, v0.6.31 will faithfully use that snapshot. Correct/clear contaminated legacy snapshots before relying on them for high-trust reminders.
