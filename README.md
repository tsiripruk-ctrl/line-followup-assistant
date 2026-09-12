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
