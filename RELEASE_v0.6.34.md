# LINE Follow-up Assistant v0.6.34

## Quoted Reply Completion Override

ฐาน: v0.6.33

### ปัญหาที่แก้
เมื่อผู้ใช้กด Reply ตรง Reminder ของ Task แล้วตอบสั้น ๆ เช่น `เรียบร้อยแล้ว` ระบบเดิมยังไม่ปิดงาน เพราะ Completion Safety ตั้งใจบล็อกคำสั้น ๆ ในข้อความลอย ๆ

### พฤติกรรมใหม่
- Exact `quotedMessageId` + `เรียบร้อยแล้ว` / `เสร็จแล้ว` / `จบแล้ว` / `ปิดได้เลย` => COMPLETED
- Question เช่น `เรียบร้อยหรือยัง` => ไม่ปิด
- Negation เช่น `ยังไม่เรียบร้อย` => ไม่ปิด
- Milestone เช่น `ส่งเอกสารแล้ว`, `เปิด PO แล้ว` => ไม่ปิด Task หลัก
- ข้อความ `เรียบร้อยแล้ว` ที่ไม่ได้ Reply Task โดยตรง => ยังไม่ปิดอัตโนมัติ

### Health flags
- `quoted_reply_completion_override: true`
- `quoted_reply_short_completion: true`

### Tests
- Python compile: PASS
- Unit/regression: 36 passed, 0 failed
- ZIP integrity: PASS

### Rollback
Deploy v0.6.33 กลับได้ทันที ไม่มี DB migration ใหม่
