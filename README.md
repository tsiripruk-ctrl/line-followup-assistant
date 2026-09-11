# LINE Follow-up Assistant v0.6.0

## New in v0.6.0 — Mention-based Assignee Identity

This release makes assignee identity LINE-aware instead of relying only on display-name text.

### What changed

- Parses LINE webhook `message.mention.mentionees[]`.
- If a task message explicitly @mentions one user, that LINE user becomes the primary assignee.
- Stores the assignee's stable LINE `userId` in `Task.assignee_user_id`.
- Learns group participants into the People Registry from their LINE `userId` and display name.
- If a later task names a known person without @mention, the system can reuse their stored LINE `userId`.
- Reply matching now prefers `assignee_user_id` over display-name matching.
- Reminder messages use LINE `textV2` mention substitution when `assignee_user_id` is known, so the responsible person receives a real @mention.
- Falls back to the existing plain-name reminder if LINE mention sending is unavailable.
- Owner acknowledgement says whether the responsible person's LINE identity has been linked.

### Recommended task style

For the most accurate assignment, use LINE's mention UI:

`@Proud วันนี้ 15:30 ช่วยเช็กสถานะ LG แล้วแจ้งพี่ด้วย`

Priority order:
1. Explicit LINE @mention with userId
2. Known People Registry alias/name
3. AI-extracted assignee name
4. Unassigned task if no reliable assignee exists

### Notes

- A LINE mention webhook may omit the mentioned user's `userId` when that user's profile-consent conditions do not allow it. In that case the app falls back to visible mention/name matching.
- v0.6.0 supports one primary assignee per task. If multiple users are mentioned, the first matching/explicit user is treated as primary.
- Existing v0.5.x PostgreSQL tables are reused. No destructive migration is required because `assignee_user_id` already exists on `tasks`.

## Deploy

1. Upload these files over the existing GitHub repository.
2. Commit changes.
3. Render → Deploy latest commit.
4. Check `/health` and confirm `version` is `0.6.0`.
5. Test in a LINE group by @mentioning one responsible person in a new task.
