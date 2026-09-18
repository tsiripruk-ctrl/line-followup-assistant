# v0.6.39 — Owner Control Center

Base: `line-followup-assistant-v0.6.38`

## Goals
Give the owner a private LINE control surface to:
- explain why a task was or was not followed up;
- inspect and repair the reminder queue;
- reschedule a specific follow-up;
- change follow-up wording/tone at runtime without a code deploy;
- preview the current reminder style before it reaches work groups.

All commands are handled only in the existing owner-private command path (`OWNER_LINE_USER_ID`).

## Main changes

### 1) Owner Follow-up Diagnostics
Supported private commands include:
- `ทำไมไม่ตาม FU-xxxxxx-xxxx`
- `ทำไมวันนี้ไม่ตาม MARCH`
- `ทำไมงานชุมแสงไม่ถูกตาม`
- `วันนี้มีงานไหนที่ควรตามแต่ยังไม่ได้ตาม`
- `ตรวจคิวติดตาม`
- `งานไหนหลุดจากคิวติดตาม`

The diagnostic response is based on live database state, including task status, `next_reminder_at`, today's reminder count, daily cap and LINE binding.

Reason codes include:
- `CLOSED`
- `MISSING_NEXT_REMINDER`
- `OUTSIDE_WORKING_HOURS`
- `WAITING_DAILY_LIMIT`
- `DAILY_LIMIT_REACHED`
- `FUTURE_COMMITMENT`
- `NOT_DUE_YET`
- `READY`

### 2) Queue repair / reschedule
Owner-only commands:
- `ซ่อมคิวติดตาม`
- `ซ่อมคิว FU-xxxxxx-xxxx`
- `เลื่อนติดตาม FU-xxxxxx-xxxx วันศุกร์`
- `เลื่อนติดตาม FU-xxxxxx-xxxx พรุ่งนี้ 14:00`

Queue repair is conservative: an existing schedule is not overwritten by the repair command. Manual rescheduling is explicit and recorded in the task timeline.

### 3) Runtime Follow-up Language Policy
A new `owner_preferences` table stores a JSON follow-up policy. It is created automatically by `Base.metadata.create_all()`; no destructive migration is used.

Supported private commands:
- `ดูรูปแบบการติดตามปัจจุบัน`
- `ตั้งโทนติดตาม: เป็นกันเอง กระชับ ไม่กดดัน`
- `ติดตามให้สั้นลง`
- `ติดตามให้นุ่มนวลขึ้น`
- `ติดตามแบบตรงประเด็น`
- `ตั้งความยาวติดตาม: 2 บรรทัด 180 ตัวอักษร`
- `ห้ามใช้คำว่า "ขออัปเดต"`
- `เลิกห้ามใช้คำว่า "ขออัปเดต"`
- `ทดลองข้อความติดตาม`
- `คืนค่ารูปแบบติดตาม`

Policy changes take effect on future reminders immediately and do not require a Render redeploy.

### 4) Policy is applied to reminder output
The live owner policy is applied to:
- context-aware reminders;
- waiting/in-progress/overdue reminders;
- pre-due reminders.

Task-state semantics are unchanged. The language policy cannot complete, reopen or retarget a task.

## Files changed
- `main.py`
- `service.py`
- `models.py`
- `tests/test_owner_control_policy.py` (new)
- `README.md`
- `RELEASE_v0.6.39.md` (new)

## Database change
New table only:

`owner_preferences`
- `id`
- `key` (unique)
- `value` (JSON text)
- `updated_at`

No existing table or column is removed or changed.

## Environment variables
None added.

## Verification performed before packaging
- `python -m py_compile` on all application `.py` files: PASS
- Existing + new automated regression suite: **71 passed, 0 failed**
- Existing subtests: **14 passed**
- Offline owner-command smoke test with stubbed unavailable external packages: PASS
  - `ทำไมไม่ตาม FU-...`
  - `ติดตามให้สั้นลง`
  - `ตรวจคิวติดตาม`
- ZIP integrity check: required before release packaging

The offline container does not include `apscheduler` or `openai`, so a full Render runtime/network test cannot be reproduced locally. Those packages remain declared in `requirements.txt`; the post-deploy LINE smoke test below is required before normal production use.

## Post-deploy manual test
1. Open `/health` and confirm `version = 0.6.39`.
2. Private chat: `ตรวจคิวติดตาม` — must return queue counts.
3. Private chat: `ทำไมไม่ตาม FU-xxxxxx-xxxx` — must explain the actual database reason.
4. Private chat: `ทดลองข้อความติดตาม` — must show current-policy previews only to the owner.
5. Private chat: `ติดตามให้สั้นลง`, then `ทดลองข้อความติดตาม` — preview must shorten immediately without deploy.
6. Private chat: `ห้ามใช้คำว่า "ขออัปเดต"`, then preview — phrase must not appear.
7. Private chat: `เลื่อนติดตาม FU-xxxxxx-xxxx วันศุกร์` — timeline must record `OWNER_FOLLOWUP_RESCHEDULED` and no reminder may be sent before the new time.
8. Work group: verify normal reminders still respect 08:30–17:30 and existing daily caps.

## Rollback
Redeploy `v0.6.38`. The new `owner_preferences` table can remain in PostgreSQL; older versions ignore it.

## Known limitations
- Runtime language control is intentionally deterministic. It changes tone, length and banned phrases; it does not send arbitrary owner instructions to an LLM for every reminder.
- `ทำไมงาน...ไม่ถูกตาม` uses task text search; if multiple active tasks share very similar titles, the owner may receive several diagnostics.
- Full LINE API + Render PostgreSQL integration must still pass the post-deploy smoke test.
