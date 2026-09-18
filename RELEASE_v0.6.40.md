# LINE Follow-up Assistant v0.6.40

## Owner Status Correction & Reopen Tracking

### ปัญหาที่แก้
Owner Diagnostics อธิบายได้ว่า Task ไม่ถูกตามเพราะสถานะ `COMPLETED` แต่ก่อนหน้านี้คำสั่งธรรมชาติ เช่น `ให้ติดตามต่อ เพราะยังไม่เสร็จ` ยังไม่สามารถแก้สถานะและนำงานกลับเข้าคิวได้

### พฤติกรรมใหม่
- `ทำไมไม่ตาม FU-...` จะจำ Task ล่าสุดไว้ใน Owner Private Context
- หลังจากนั้นพิมพ์ `ให้ติดตามต่อ เพราะยังไม่เสร็จ` ได้โดยไม่ต้องใส่ FU ซ้ำ
- รองรับ `ติดตามต่อ FU-... เพราะยังไม่เสร็จ`, `เปิดงาน FU-...`, `เปิดใหม่ FU-...`
- หาก Task ถูกปิดผิด ระบบจะย้อนกลับไปสถานะ active ก่อน `COMPLETED` จาก Timeline เมื่อมีข้อมูล เช่น WAITING/IN_PROGRESS
- ถ้าไม่มีประวัติและเลยกำหนดแล้ว จะกลับเป็น `OVERDUE`; ถ้ายังไม่เลยกำหนดจะเป็น `IN_PROGRESS`
- ตั้ง `next_reminder_at` กลับเข้าเวลาทำงาน 08:30–17:30
- ไม่ลบ Timeline เดิม แต่เพิ่ม `OWNER_STATUS_CORRECTION` และ `REMINDER_REACTIVATED`
- แก้ Progress Snapshot ที่มีข้อความลักษณะ `เรียบร้อย/เสร็จ/ปิดงาน` ให้สะท้อนว่า Owner ยืนยันงานยังไม่เสร็จ

### Database
ไม่มี schema migration ใหม่ ใช้ `owner_preferences` เดิมเก็บ `owner_last_task_code`

### Environment Variables
ไม่มีค่าใหม่

### Automated Tests
- Existing regression tests: 71
- New owner reopen tests: 3
- Total: 74 passed
- Subtests: 14 passed
- `py_compile`: PASS
- ZIP integrity: PASS

### Manual Test หลัง Deploy
1. ส่งส่วนตัว `ทำไมไม่ตาม FU-260911-0013`
2. ระบบต้องบอกว่าสถานะเสร็จแล้ว/ไม่อยู่ในคิว (ถ้ายังเป็นข้อมูลเดิม)
3. ส่ง `ให้ติดตามต่อ เพราะยังไม่เสร็จ`
4. ระบบต้องตอบว่าเปิดงานกลับมาติดตามต่อแล้ว พร้อมสถานะใหม่และเวลาติดตามครั้งถัดไป
5. ส่ง `ทำไมไม่ตาม FU-260911-0013` อีกครั้ง
6. สถานะต้องไม่เป็น COMPLETED แล้ว และเหตุผลต้องสะท้อนคิวใหม่
7. เปิด Dashboard/Timeline ต้องเห็น `OWNER_STATUS_CORRECTION` และ `REMINDER_REACTIVATED`

### Rollback
Deploy v0.6.39 กลับได้ทันที ไม่มี schema ใหม่ต้องย้อน

### Known Issues
ถ้าผู้รับผิดชอบยังไม่ผูก LINE ระบบยังติดตามได้ด้วยชื่อข้อความ แต่ไม่สามารถ @Mention LINE account โดยตรงจนกว่าจะผูก People Registry
