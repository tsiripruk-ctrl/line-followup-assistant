# LINE Follow-up Assistant v0.6.7

Built directly on v0.6.4. This release preserves PostgreSQL, Timeline, assignee normalization, LINE @mention assignment/follow-up, quoted-message matching, content-first safe task matching, reminders and daily briefs.

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
