# v0.6.33 – Explicit Command Prefix Routing Fix

## Root cause fixed
A message such as:

`งานใหม่: @J @Proud งานบางกอกเจมส์ ได้ส่งมอบงานพร้อมเล่มไปหรือยัง`

was classified as `STATUS_QUERY` because `หรือยัง` was evaluated before the explicit `งานใหม่:` command. The system then searched old tasks and could surface unrelated candidates.

## Fix
Explicit command prefixes are now hard routing signals:
- `งานใหม่:` / `มอบหมายงาน:` -> NEW_TASK
- `ติดตามงาน:` -> FOLLOW_UP
- `อัปเดตงาน:` -> PROGRESS_UPDATE
- `ปิดงาน:` -> COMPLETION_CONFIRMATION

The command prefix is removed before semantic extraction/matching so the actual task content is analyzed cleanly.

For `งานใหม่:` the system does not route into old-task status matching, even if the sentence contains question words such as `หรือยัง`.

## Multi-mention note
The current schema still has one primary assignee per Task. When a new task contains multiple LINE mentions, the first resolved mention remains the primary assignee and all explicit mentions are preserved in the Task timeline as `CO_ASSIGNEES_MENTIONED`. Full multi-assignee reminder delivery is a separate schema enhancement.

## Test results
- Full regression suite: 45 passed
- Python compile: passed
- ZIP integrity: passed

## Manual smoke tests
1. `งานใหม่: @Proud งานบางกอกเจมส์ ส่งมอบงานพร้อมเล่มไปหรือยัง`
   - Must create a new FU.
   - Must NOT answer with unrelated old tasks.
2. `ติดตามงาน: เรื่องมิเตอร์ชุมแสงถึงไหนแล้ว`
   - Must search/update an existing FU, not create a new one.
3. `อัปเดตงาน: ส่งเอกสารแล้ว แต่ยังรอเจ้าหน้าที่`
   - Must be progress/waiting, not completion.
4. `ปิดงาน: FU-xxxxxx-xxxx ดำเนินการเรียบร้อยทั้งหมดแล้ว`
   - May complete only the referenced/matched task.
