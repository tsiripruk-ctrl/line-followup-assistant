# v0.6.37 – Mentioned New-Work Question Routing

## Problem fixed
A message can open a brand-new follow-up even when phrased as a question, for example:
`@พี่ตั้ม ค่าบริการซ่อมรถ เราได้ตีราคาซ่อมมาทั้งหมดแล้วถูกไหม?`
Previously `STATUS_QUERY` routing tried to find an old task and, when none matched, asked for a project name.

## New rule
For a real LINE @mention + work-topic question:
1. Search existing tasks first.
2. If a confident existing task matches, treat it as STATUS_QUERY.
3. If multiple tasks match, ask a natural clarification.
4. If no task matches, the question itself becomes a NEW_TASK.
5. Project name is optional and is never required just to create this task.

## Safety
- `@พี่ตั้ม เรื่องค่าซ่อมรถถึงไหนแล้ว` remains a follow-up/status query.
- Casual questions such as `@พี่ตั้ม กินข้าวหรือยัง` never become tasks.
- Existing task matching always has priority over creating a new task.
- Passive conversation guard remains enabled.

## Database / env
No migration. No new environment variables.
