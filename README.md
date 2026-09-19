# LINE Follow-up Assistant v0.6.46

Built directly on v0.6.4. This release preserves PostgreSQL, Timeline, assignee normalization, LINE @mention assignment/follow-up, quoted-message matching, content-first safe task matching, reminders and daily briefs.

## New in v0.6.46 — New Task Confirmation + Previous Message Recovery

- Detects natural new assignments from action + project/problem/@mention context, including wording such as `ต้องให้ประสาน...`.
- Fixes the Thai question false-positive where `ไฟไหม้` could be mistaken for the question particle `ไหม`.
- Stores short-lived clarification context per LINE user/group.
- When the assistant is waiting for clarification, `งานใหม่` / `ใช่` creates the task from the **previous original message**, not from the short confirmation text.
- `ไม่ใช่งานใหม่` / `ไม่ต้องสร้าง` cancels the pending creation safely.
- Standalone `งานใหม่` asks for details and treats the next message as a forced new task.
- Adds the `conversation_states` table automatically on startup; existing data is preserved.

## Previous: v0.6.45 — Quoted Human Message Context Safety

- A quoted completion can no longer close a merely recent/unrelated task.
- Quoted human messages resolve from exact `TaskEvent.message_id` first, then from the quoted message text with a strict unique topic match.
- The unsafe same-user recent-task fallback is disabled for completion.
- If the quoted context cannot identify one task confidently, the system stays safe and does not close anything.


## New in v0.6.6 — People Registry Profile

- Full People Registry in the Dashboard.
- Stable LINE `userId` remains the strongest identity key.
- Stores and displays:
  - Canonical name
  - Call name (the name the OA should use in reminders)
  - Latest LINE display name
  - Role (`EMPLOYEE`, `OWNER`, `MANAGER`, `ADMIN`)
  - Active / inactive status
  - LINE identity binding status
  - Multiple aliases
- Learns the latest LINE display name automatically when a member speaks in the group or is identified by @mention.
- Owner can edit each person's profile from the Dashboard.
- Alias mapping remains available and existing tasks are normalized when a canonical name changes.
- Reminder wording uses the person's `call_name` while real LINE mentions still use the stable `line_user_id`.
- Ambiguous status replies remain conservative: no task is changed unless matching is confident. Exact quote/reply mapping still has highest priority.

## Database migration

v0.6.6 performs a non-destructive additive migration on startup. Existing `people` tables receive only these missing columns:

- `display_name`
- `call_name`
- `role`

No existing tasks or PostgreSQL data are deleted.

## Deploy over v0.6.4

1. Upload all files from this package over the existing GitHub repository.
2. Commit the changes.
3. Render → Deploy latest commit.
4. Open `/health` and confirm `version` is `0.6.5` and `people_registry` is `true`.
5. Open the existing Dashboard and scroll to **ทะเบียนผู้รับผิดชอบ (People Registry)**.
6. Edit canonical name, call name, role or active status as needed.

Recommended naming example:

- Canonical name: `ต้อง`
- Call name: `พี่ต้อง`
- LINE display name: `Tong Thanakrit`
- Aliases: `Tong Thanakrit`, `Tong`, `ต้อง`, `พี่ต้อง`
- Role: `OWNER`


## v0.6.7 — People Merge Button Hotfix
- Detects likely duplicate People Registry rows from canonical/display/alias overlap.
- Dashboard shows a green “รวมกับ …” button for suggested duplicates.
- Merge preserves the stable LINE userId, aliases, role, and active state.
- Existing tasks are normalized to the merged canonical person.
- Safety guard: two different bound LINE userIds cannot be merged.


## v0.6.7 hotfix
- Fixed People Registry merge buttons so names containing symbols/emoji cannot break inline JavaScript.
- Merge buttons now pass numeric IDs only and resolve names from the registry data.
- Added explicit button type and visible error handling for merge requests.


## v0.6.8
- Replaced People Merge inline JavaScript button with a server-rendered confirmation flow.
- Merge now works even when browser inline JavaScript/onclick is blocked or stale.
- Added health flag `people_merge_nojs: true`.

## v0.6.9
- People Registry: add/remove multiple aliases directly from Edit Person.
- Alias matching preserves call forms such as `มาช` and `พี่มาช` as separate saved aliases while remaining backward compatible.
- Prevents the same alias from being assigned to two different people.


## v0.6.10 — Dashboard JavaScript Hotfix
- Fixed a JavaScript parse error caused by unescaped line breaks in the People Merge confirmation message.
- Restores all Dashboard JavaScript actions, including Edit Person, Alias management, task status actions, and timeline buttons.


## v0.6.11
- Added Task Resolution Engine scoring visibility and safer ambiguity handling.
- Added Thai semantic-core matching for short status replies (e.g. จ่าย/ชำระค่าประกัน).
- Added People Registry context assignment for explicit wording such as “ให้ต้องเป็นผู้รับผิดชอบ”.
- Keeps @mention and LINE userId as highest-confidence identity signals.
- Ambiguous status updates show plausible candidate tasks to the owner but never auto-close one.
- Existing LINE message-id deduplication remains enabled.


## v0.6.13 hotfix
- Adds deterministic Thai status fallback so concise updates such as `จ่ายค่าประกันเรียบร้อย` are treated as `completed` even when the LLM returns `status_signal=none`.
- Adds a concise group acknowledgement when a status update is recognized but cannot be matched safely to an open Task.
- Keeps safe matching: ambiguous updates never auto-close a random Task.


## v0.6.13 hotfix
- Fast local status path runs before OpenAI for obvious Thai updates such as `จ่ายค่าประกันเรียบร้อย`.
- Obvious status updates no longer depend on OpenAI availability/latency.
- Unmatched status acknowledgement falls back from LINE reply to group push, preventing silent failures when replyToken expires.
- `/health` exposes `fast_local_status_path` and `ai_failure_status_fallback`.


## v0.6.15 hotfix
- Business concept matching for operational status replies (e.g. `จ่ายค่าประกันเรียบร้อย` ↔ `ต่อประกันรถ`).
- Status DB update is transaction-guarded and logs exact commit/failure stage.
- Owner notification failures can no longer make a successfully committed task look like an update failure.
- Ambiguous matches remain safe: the system refuses to auto-close when multiple plausible tasks exist.
- Fixed a real `NameError` in `choose_status_target()` (`sender` was referenced before assignment on weak/unmatched content). This was one direct cause of the generic "received update but could not update Task" message.

## v0.6.16
- เพิ่ม task-history matching เพื่อใช้ข้อความต้นฉบับของ Task ช่วยจับสถานะ
- เพิ่ม strict cross-group recovery สำหรับ owner/ผู้รับผิดชอบ เมื่อ Task ถูกสร้างคนละกลุ่ม
- เพิ่ม candidate score logging เพื่อวิเคราะห์เหตุผลที่จับ/ไม่จับ Task
- คง safety rule: ถ้ามีหลาย Task ที่เกี่ยวข้องเท่า ๆ กัน จะไม่ปิดงานโดยเดา


## v0.6.17 hotfix
- Adds business-first status resolution before generic similarity tie handling.
- Resolves `จ่ายค่าประกันเรียบร้อย` to a single open `ต่อประกันรถ` task.
- Uses recent TaskEvent history for business-concept matching.
- If multiple vehicle-insurance tasks are open, it refuses to guess.
- If duplicate tasks represent the same concept/project/assignee, it chooses the newest duplicate deterministically.
- Adds health flags: `business_first_status_resolution`, `vehicle_insurance_resolution`, `duplicate_business_task_resolution`.

## v0.6.18 — Task State Continuity / Context-aware Follow-up

- Follow-up reminders now continue from the latest human progress update instead of repeating the original task wording.
- Uses existing `TaskEvent` history, so old tasks benefit immediately without a destructive database migration.
- Stores a compact `TASK_MEMORY_UPDATED` event for every new status reply.
- `WAITING` updates are followed up after 24 hours instead of every 6 hours to reduce repetitive chasing of external dependencies.
- Example: Flow Account follow-up remembers that documents were sent, the reply was off-topic, and staff are waiting for the officer to respond.
- Example: Futong PO follow-up remembers that the PO was already opened and only sales acceptance is pending.
- `/health` exposes `task_state_continuity`, `context_aware_reminders`, and `waiting_followup_memory`.


## v0.6.20 — Quote Reply Identity + Continuity Hotfix

- Fixes duplicate-People unique constraint failures when a quoted task stores an old LINE display name but People Registry already owns that LINE userId under a canonical name.
- Quote replies now resolve Person by stable LINE userId first and never create a duplicate person for the same account.
- Quote replies now write `TASK_MEMORY_UPDATED`, so future reminders continue from the latest human update.
- `WAITING` quote replies snooze for 24 hours.
- Mixed updates such as “เปิด PO เรียบร้อยแล้ว แต่เซลล์ยังไม่ตอบรับ” stay `WAITING` instead of being incorrectly closed as `COMPLETED`.
- Adds explicit completion phrases such as “ส่งเรียบร้อย” and “ดำเนินการเสร็จแล้ว”.


## v0.6.20
- Exact LINE quote-replies update the linked Task before optional People Registry enrichment.
- Identity/Alias conflicts can no longer roll back a correctly quoted status update.
- Added logs: `quoted task CORE update failed` and `quoted reply identity enrichment skipped`.

## v0.6.21 — Working-hours & Humanized Follow-up Policy

- Group follow-up runs every day but only during **08:30–17:30 Asia/Bangkok**.
- No group reminder is sent before 08:30 or from 17:30 onward; overdue reminders wait for the next working window.
- Default daily cap: **2 follow-ups per task/day**, and **1/day** for `WAITING` tasks.
- Reminder bursts are spread across scheduler ticks instead of sending many messages to the same group at once.
- Next reminders are automatically clamped to working hours; tasks that hit the daily cap resume the next day from 08:30 with deterministic staggering.
- Context-aware reminders and Task Memory from v0.6.18+ remain enabled.
- Default owner briefs are aligned to **08:30** and **17:30**.

New optional environment variables:
`FOLLOWUP_START_HOUR=8`, `FOLLOWUP_START_MINUTE=30`, `FOLLOWUP_END_HOUR=17`, `FOLLOWUP_END_MINUTE=30`,
`MAX_FOLLOWUPS_PER_TASK_PER_DAY=2`, `WAITING_MAX_FOLLOWUPS_PER_DAY=1`, `MAX_FOLLOWUPS_PER_SCAN=2`, `MAX_GROUP_FOLLOWUPS_PER_SCAN=1`.

## v0.6.22 – Task Context Integrity Guard

This release addresses cross-task context contamination observed in group follow-ups.

- Treats the original LINE assignment (`CREATED` event) as source truth when an AI-generated task title points to a different topic.
- Prevents substantive updates (GPS, meter, camera, Flow Account, PO, fiber, insurance, etc.) from being attached to a task by assignee identity alone.
- Uses identity-only fallback only for genuinely generic short status replies such as `เรียบร้อยแล้ว` when there is exactly one eligible task.
- Excludes arbitrary `STATUS_REPLY` / `TASK_MEMORY_UPDATED` events from future task matching; only the original assignment and exact quoted replies may enrich matching context.
- Rejects cross-topic task memory before generating reminders.
- Reminder text falls back to the original assignment wording when the stored task title conflicts with the source message.
- New tasks receive a source-vs-title integrity check at creation time to reduce mixed-context titles from entering the database.

`/health` exposes:
- `task_context_integrity_guard`
- `cross_topic_memory_guard`
- `source_truth_reminders`
- `identity_only_substantive_match_disabled`

## v0.6.23 - Trust Recovery / Multi-topic Update Guard

เป้าหมายของรุ่นนี้คือหยุดข้อความเชิงเทคนิคที่ทำให้ผู้ใช้ในกลุ่มรู้สึกว่าเป็นบอต และป้องกันการนำข้อมูลหลายโครงการในข้อความเดียวไปปนกับ Task เดียว

### การเปลี่ยนแปลงหลัก
- ไม่แสดงคำว่า `Task`, `จับคู่ไม่ชัดเจน`, `ระบบประมวลผลผิดพลาด` ในกลุ่ม LINE อีกต่อไป
- ถ้าระบบยังไม่มั่นใจ จะตอบเพียงข้อความธรรมชาติสั้น ๆ เช่น `ขอบคุณค่ะ รับข้อมูลไว้แล้วนะคะ`
- รายละเอียดการจับคู่/ข้อผิดพลาดจะส่งเฉพาะเจ้าของระบบทาง Private LINE
- เพิ่ม Multi-topic Update Guard: ข้อความยาวที่อัปเดตหลายงาน เช่น ชุมแสง / ประแส / Fiber จะถูกแยกเป็นส่วนก่อนจับคู่
- แต่ละส่วนต้องมี content match ที่แรงพอกับงานเดิมจึงจะถูกบันทึก
- ห้ามใช้เพียงชื่อผู้ส่งเพื่อเอาข้อมูลเฉพาะเรื่องไปผูกกับงานอื่น
- ปรับคำตอบรับให้สั้น เป็นธรรมชาติ และไม่ใช้ถ้อยคำซ้ำแบบระบบ

### Post-deploy test
1. เปิด `/health` ต้องเห็น `version: 0.6.23`, `trust_recovery_mode: true`, `multi_topic_update_guard: true`.
2. ส่งข้อความที่ไม่ชัดว่าอยู่ Task ไหน เช่น `เรื่องนี้ส่งข้อมูลให้แล้วครับ` ต้องไม่เห็นคำว่า Task/จับคู่/ระบบผิดพลาดในกลุ่ม.
3. ส่งข้อความอัปเดตหลายเรื่องในข้อความเดียว โดยแยกย่อหน้า ชุมแสง / ประแส / Fiber. ในกลุ่มควรได้คำตอบรับเพียงครั้งเดียว และ owner DM ต้องแสดงว่าส่วนไหนผูกได้หรือยังไม่ผูก.
4. ส่งอัปเดตเรื่อง GPS จากคนที่มีหลายงาน ระบบต้องไม่เอาไปต่อกับงานกล้อง/มิเตอร์อื่นเพียงเพราะเป็นคนเดียวกัน.
5. Quote Reply ที่ Reminder เดิมแล้วพิมพ์ `เรียบร้อยแล้ว` ต้องยังปิดงานตรงข้อความที่ Quote ได้ตามเดิม.

## v0.6.24 - Quiet Context Learning / Recent Reminder Continuity

- Quiet-by-default in LINE groups: ambiguous updates no longer trigger canned acknowledgements.
- Progress-only comments are learned silently; the bot speaks mainly on explicit status changes or exact quote replies.
- Recent Reminder Context: a natural reply shortly after a reminder can resolve to that task even without LINE quote/reply, but only with sender/topic safety guards.
- Multi-topic messages are split/learned privately without a repetitive public acknowledgement.
- Unmatched details remain in Message history and are reported privately to the owner; they are not forced into a random task.
- Cross-topic integrity guard remains active.

### Mobile test checklist
1. `/health` must show `version: 0.6.24`, `silent_ambiguity_mode: true`, `recent_reminder_context: true`.
2. Send an unrelated operational sentence: the group should receive no canned bot reply.
3. Reply naturally (without quote) within 3 hours after one reminder for your assigned task; a clear status such as `ส่งแล้ว` should update that recent task.
4. If two recent reminders belong to the same person, a substantive reply must match by topic; otherwise it stays silent and does not change either task.
5. Send a long multi-project update; no repetitive public acknowledgement should appear, and no segment may contaminate a different task.


## v0.6.25 - Verified Quiet Group Policy

- Removed the remaining public fallback message `ขอบคุณค่ะ รับข้อมูลไว้แล้วนะคะ` from exception handling.
- Routine `in_progress` / `none` updates no longer receive canned public acknowledgements.
- Public acknowledgements are now limited to meaningful state changes: `COMPLETED` and `WAITING`.
- Processing errors and ambiguous matches remain silent in the group and are sent only to the owner diagnostics.
- Health flags now reflect implemented behavior: `quiet_ack_policy` and `public_exception_fallback_disabled`.

Post-deploy smoke test:
1. `/health` => `version: 0.6.25`, `quiet_ack_policy: true`, `public_exception_fallback_disabled: true`.
2. Send a vague update such as `เดี๋ยวเช็กให้อีกทีครับ` => group must stay silent.
3. Send an in-progress update matched to one task => group must stay silent; task memory/status may update internally.
4. Quote-reply `เรียบร้อยแล้ว` to a reminder => exact task becomes COMPLETED and a short acknowledgement is allowed.
5. Force/observe any ambiguous or processing-error path => no technical/canned message in group; owner receives diagnostics privately.

## v0.6.26 - Non-Task Leave Guard + Owner Delete

- Added deterministic Task Creation Guard before the LLM.
- Leave/attendance notices such as `ขอลากิจ 2 วัน`, `ลาป่วย`, `ไม่เข้าทำงาน`, `ขอกลับก่อน` are ignored and do not create FU tasks.
- Real action requests about leave paperwork remain task-eligible, e.g. `ช่วยทำใบลาให้บอส`, `ส่งใบลาให้ HR`, `อนุมัติการลาให้...`.
- Added owner private command `ลบ FU-xxxxxx-xxxx` to permanently remove a mistaken task and its timeline/outbound mappings.
- `/health` exposes `task_creation_guard`, `leave_notice_filter`, and `owner_hard_delete_command`.

### Regression test

Run `python -m unittest discover -s tests -v`. Expected: all task-creation guard tests pass.

## v0.6.27 — Completion Safety & Duplicate Follow-up Fix

Base: v0.6.26. This release is intentionally numbered v0.6.27 (not v0.6.3) to preserve monotonic versioning.

### What changed
- Added deterministic intent layer: STATUS_QUERY, FOLLOW_UP, PROGRESS_UPDATE, COMPLETION_CONFIRMATION, NOT_COMPLETED, CANCEL_REQUEST, OTHER.
- Hard safety: questions and negations never complete a task.
- Bare `เรียบร้อย`, `เสร็จแล้ว`, `ส่งแล้ว` do not close a parent task by themselves.
- Milestone updates such as `ส่ง Datasheet ... แล้ว` remain progress updates.
- Completion requires strong confirmation and confident task matching.
- Before creating a new FU, active tasks are searched first; high-confidence matches append a FOLLOW_UP event instead of creating a duplicate.
- LINE message id remains the idempotency key in `messages` to protect against webhook retries.
- `task_events` gains nullable `message_id` and `confidence` columns via an automatic additive migration on startup.
- Added structured debug logs: `[INTENT]`, `[TASK_MATCH]`, `[ACTION]`, `[COMPLETION_BLOCKED]`.

### Database migration
No destructive migration. On startup, `ensure_task_event_schema()` adds only:
- `task_events.message_id VARCHAR(128) NULL`
- `task_events.confidence FLOAT NULL`

### New environment variables
None.

### Automated tests
Run:
```bash
python -m unittest discover -s tests -v
```

### Deploy on Render
1. Back up the current v0.6.26 source/commit.
2. Upload v0.6.27 files over the repository.
3. Commit and push.
4. Deploy latest commit on Render.
5. Open `/health` and verify `version=0.6.27` plus the new safety flags.
6. Run the LINE manual tests below before normal use.

### Manual LINE smoke tests
1. Create one clear test task.
2. Send `งานนี้เรียบร้อยหรือยัง` → must NOT complete.
3. Send `ยังไม่เรียบร้อยครับ` → must NOT complete.
4. Send `ขออัปเดตเรื่องนี้หน่อย` → must NOT create a new FU.
5. Send `ส่ง Datasheet แล้ว` → must remain progress, not complete the parent task.
6. Send `ดำเนินการเรียบร้อยแล้วครับ` → may complete only when the task is matched confidently.
7. Open Dashboard Timeline and verify STATUS_QUERY / FOLLOW_UP / PROGRESS_UPDATE / COMPLETION_CONFIRMATION / STATUS_CHANGE events.
8. Re-send the same LINE webhook `message_id` in a controlled test → must process once.

### Rollback
If any smoke test fails, redeploy the previous known-good v0.6.26 commit. The two new task_event columns are nullable and may remain in the database; v0.6.26 ignores them.

### Known limitation
Full Render/LINE network behavior cannot be reproduced in an offline development container. Source compilation, SQLite startup/migration, matching tests, and regression tests are completed locally; real LINE webhook + PostgreSQL behavior must still pass the post-deploy smoke test before production use.

## v0.6.29 — Human-Directed Request Routing Fix

Base: v0.6.27.

Fixes a routing bug where a message directed to a colleague, such as
`@Proud ตอนนี้เบอร์ออฟฟิตใช้งานไม่ได้หรือเปล่า ฝากตรวจสอบที`, could be
misclassified as a STATUS_QUERY and answered using an unrelated existing task.

Changes:
- explicit actionable @mention requests are treated as task assignments, even when the sentence also contains a question clause;
- status/follow-up queries use the mentioned assignee as a tie-breaker, not the sender's identity;
- task query matching threshold is raised to 0.80;
- unrelated old tasks cannot answer a human-directed operational question based on sender identity alone;
- explicit follow-up phrases are classified before generic question patterns.

Post-deploy checks:
1. `/health` reports `version=0.6.28`.
2. `@Proud ... ฝากตรวจสอบที` must not produce status text from an unrelated old task.
3. `@Proud เรื่อง Flow Account ถึงไหนแล้ว` may query the matching Proud task only when topic evidence is strong.
4. `@MARCH ช่วยตามเรื่องมิเตอร์ให้หน่อย` must be FOLLOW_UP, not a new duplicate task.


## v0.6.29 – Date-aware Follow-up Scheduling
- When a progress reply contains an explicit future checkpoint such as `วันศุกร์`, `พรุ่งนี้`, `มะรืน` or a numeric date, `next_reminder_at` is moved to that checkpoint instead of following up again the next day.
- Thai weekdays are resolved in `Asia/Bangkok` and default to the start of the work window (08:30) unless the user includes an explicit time.
- Adds `FOLLOW_UP_SCHEDULED` timeline events for traceability.
- Completion safety and all v0.6.28 routing/duplicate protections are preserved.

## v0.6.32 — Progressive Task Context & Progress Snapshot

เป้าหมายของรุ่นนี้คือให้การติดตามแต่ละงาน "ต่อเนื่องจากความก้าวหน้าล่าสุด" แทนการถามซ้ำจากชื่อ Task เดิมทุกครั้ง

เพิ่มข้อมูลแบบ task-local ในตาราง `tasks` (migration แบบ additive เท่านั้น):
- `progress_summary` — สรุปอัปเดตล่าสุดที่เชื่อถือได้ของงานนี้
- `waiting_on` — ประเด็นที่กำลังรอ ถ้ามี
- `next_action` — ขั้นตอน/นัดหมายถัดไปที่ผู้รับผิดชอบแจ้งไว้
- `last_progress_at` — เวลาที่อัปเดตความก้าวหน้าล่าสุด

หลักการ:
- อัปเดต Progress Snapshot เฉพาะหลังระบบจับ Task ได้แล้วเท่านั้น จึงไม่ใช้ snapshot เป็นตัวเดา Task และลดการปนบริบทข้ามงาน
- Snapshot ใหม่ "แทนที่" snapshot เก่า ไม่สะสมข้อความขัดแย้งกันไปเรื่อย ๆ
- Reminder ใช้โครง `งานเดิม → ล่าสุด → สิ่งที่ยังรอ/ขั้นตอนถัดไป → คำถามรอบนี้`
- Task เก่าที่ยังไม่มี snapshot จะ fallback ไปใช้ timeline เดิม
- Dashboard แสดง "ล่าสุด" และ "ถัดไป" ใต้ชื่องานเพื่อเห็นความก้าวหน้าได้ทันที

ตัวอย่าง:
`เปิด PO ให้ Futong เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับ เดี๋ยวจะติดตามอีกที`
จะทำให้ Reminder รอบถัดไปอ้างถึงว่าเปิด PO แล้วและกำลังรอเซลล์ ไม่ย้อนกลับไปถามว่าเปิด PO แล้วหรือยัง

Automated regression tests: `30 passed` ณ ตอน build รุ่นนี้ (รวม intent, duplicate follow-up, date-aware scheduling, human-directed routing และ progress context)


## v0.6.32 – Humanized Context-aware Follow-up

- Reminder text is generated from the task's trusted progress snapshot, waiting dependency, next action and appointment state.
- Generic repeated phrases such as `ขออัปเดตเรื่อง...` and `เรื่องที่รออยู่มีความคืบหน้าเพิ่มเติมไหมคะ` are removed from the reminder path.
- Message style is state-driven (waiting / next action / appointment / overdue / in-progress / first follow-up), with deterministic variation only inside the selected state.
- Reminder messages remain concise (normally 1–2 lines) and never expose technical system language in the work group.
- Existing working-hour, daily-cap, date-aware, identity, quiet-mode and task-context-integrity protections remain intact.
- No database migration or new environment variable is required for this release.


## v0.6.32 – Reminder Queue Self-Healing
- Repairs active OPEN/IN_PROGRESS/WAITING/OVERDUE tasks whose `next_reminder_at` became NULL.
- Existing future commitments are preserved; only missing schedules are repaired.
- Past-due WAITING tasks become eligible for follow-up in the current work window instead of being silently skipped forever.
- Adds `REMINDER_SCHEDULE_REPAIRED` to the task timeline for traceability.


## v0.6.38 — General Conversation & Clarification Guard

- Generic group questions no longer trigger the fallback “ขอชื่อโครงการ...” prompt.
- Unmatched `STATUS_QUERY` is silent by default.
- Clarification is allowed only for explicit follow-up intent/prefix or an explicit FU id.
- Existing-task matching still runs first, so real questions such as “เรื่องมิเตอร์ถึงไหนแล้ว” can resolve normally when there is a confident match.
- Directed @mention work questions from v0.6.37 remain supported.
- Quiet-by-default is preserved: ordinary conversation is never converted into a new FU merely because it contains question words such as “ไหม/หรือเปล่า”.

## v0.6.39 — Owner Control Center

เพิ่มการควบคุมและตรวจสอบระบบจาก LINE ส่วนตัวของ Owner โดยไม่ต้องเปิด Render/Dashboard ทุกครั้ง

### Follow-up diagnostics
- `ทำไมไม่ตาม FU-xxxxxx-xxxx`
- `ทำไมวันนี้ไม่ตาม MARCH`
- `ทำไมงานชุมแสงไม่ถูกตาม`
- `ตรวจคิวติดตาม`
- `งานไหนหลุดจากคิวติดตาม`
- `ซ่อมคิว FU-xxxxxx-xxxx`
- `ซ่อมคิวติดตาม`
- `เลื่อนติดตาม FU-xxxxxx-xxxx วันศุกร์`

### Runtime language controls
- `ดูรูปแบบการติดตามปัจจุบัน`
- `ตั้งโทนติดตาม: เป็นกันเอง กระชับ ไม่กดดัน`
- `ติดตามให้สั้นลง`
- `ติดตามให้นุ่มนวลขึ้น`
- `ติดตามแบบตรงประเด็น`
- `ตั้งความยาวติดตาม: 2 บรรทัด 180 ตัวอักษร`
- `ห้ามใช้คำว่า "ขออัปเดต"`
- `ทดลองข้อความติดตาม`
- `คืนค่ารูปแบบติดตาม`

การตั้งค่าถูกเก็บใน `owner_preferences` และมีผลกับ Reminder รอบถัดไปทันทีโดยไม่ต้อง Deploy ใหม่ ส่วนสถานะงาน, Task Matching, Completion Safety, Quiet-by-default, working hours 08:30–17:30 และ daily follow-up limits ยังคงทำงานตามเดิม


## v0.6.41 – Owner Status Correction & Reopen Tracking

เพิ่มคำสั่ง Owner Private สำหรับแก้สถานะงานที่ถูกปิดผิด และเปิดคิวติดตามต่อโดยไม่ลบประวัติเดิม

ตัวอย่าง:
- `ทำไมไม่ตาม FU-260911-0013`
- `ให้ติดตามต่อ เพราะยังไม่เสร็จ` (ใช้กับงานล่าสุดที่เพิ่งตรวจ)
- `ติดตามต่อ FU-260911-0013 เพราะยังไม่เสร็จ`
- `เปิดงาน FU-260911-0013`

ระบบจะคืนสถานะก่อนถูกปิดเมื่อหาได้จาก Timeline, ตั้ง `next_reminder_at` ใหม่ในเวลางาน, และบันทึก `OWNER_STATUS_CORRECTION` / `REMINDER_REACTIVATED`.


## v0.6.42 – Natural Owner Policy Rule Routing

แก้บั๊กคำสั่ง Owner Private ที่เป็นภาษาธรรมชาติ เช่น:
- `เวลางานเลยกำหนด ให้ถามวันที่คาดว่าจะเสร็จ`
- `เวลางานติดปัญหา ให้ถามว่าติดตรงไหน`

ก่อนหน้านี้คำสั่งแรกถูกตีความเป็นคำสั่งดู “งานเลยกำหนด” และคำสั่งที่สองหลุดไปหน้า Help เพราะ parser ยังไม่รู้จัก policy rule แบบตามสถานะ

รุ่นนี้เพิ่ม State Question Rules ซึ่งทำงานก่อน generic owner commands และมีผลกับ Reminder รอบถัดไปทันทีโดยไม่ต้อง Deploy ใหม่:
- overdue
- blocked
- waiting_response
- waiting_document
- waiting_approval
- waiting_goods
- appointment
- in_progress

คำสั่งเพิ่มเติม:
- `ดูกติกาคำถามติดตาม`
- `ล้างกติกาคำถามติดตาม`

กติกานี้มีผลเฉพาะ Message Generation ไม่แก้ Intent, Task Matching หรือ Task Status


## v0.6.44 – Task-linked Reply Identity & Bare Quote Completion
- Task-specific status/follow-up responses now persist their LINE message id so later quote replies resolve the exact task.
- Exact quote reply `เรียบร้อย` / `เสร็จเรียบร้อย` can close the linked task, while questions/negations/milestones remain blocked.
