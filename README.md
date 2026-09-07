# LINE Follow-up Assistant v0.3

AI เลขานุการติดตามงานจากกลุ่ม LINE แบบเงียบในกลุ่มและรายงานเจ้าของระบบทางแชตส่วนตัว

## ความสามารถ v0.3
- จับคำสั่งงานจากข้อความในกลุ่มและสร้าง FU Task อัตโนมัติ
- แจ้งเจ้าของทางแชตส่วนตัวเมื่อรับงานใหม่
- อ่านคำตอบในกลุ่มและเปลี่ยนสถานะ OPEN / IN_PROGRESS / WAITING / COMPLETED
- เตือนก่อนกำหนดและตามซ้ำเมื่อเลยกำหนด
- Escalation: เมื่อตามหลายครั้งแล้วยังไม่ปิด จะขอ ETA/สาเหตุและแจ้งเจ้าของส่วนตัว
- Quiet hours ป้องกันการตามงานช่วงกลางคืน
- Daily Brief อัตโนมัติ: เช้า 07:30 และเย็น 18:30 (ปรับได้จาก Environment)
- คำสั่งส่วนตัว: งานค้าง / วันนี้ / พรุ่งนี้ / เลยกำหนด / รอข้อมูล / งานที่ปิดแล้ว / สรุปเช้า / สรุปเย็น / ปิด FU-...
- `/jobs/*` endpoints สำหรับต่อ external cron ในกรณี host มีการ sleep

## Deploy บน Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`uvicorn main:app --host 0.0.0.0 --port $PORT`

หลัง Deploy ตรวจ:
`GET /health`

ควรได้ version `0.3.0`

## Environment ใหม่ที่แนะนำ
ค่าหลักเดิมยังใช้เหมือน v0.2 และเพิ่ม/ปรับได้ดังนี้:

- `ESCALATION_AFTER_REMINDERS=2`
- `DAILY_BRIEF_ENABLED=true`
- `MORNING_BRIEF_HOUR=7`
- `MORNING_BRIEF_MINUTE=30`
- `EVENING_BRIEF_HOUR=18`
- `EVENING_BRIEF_MINUTE=30`
- `CRON_SECRET=` (เว้นว่างได้ถ้ายังไม่ใช้ external cron)

## หมายเหตุเรื่อง Render Free
Scheduler ภายในทำงานเมื่อ Web Service กำลังรันอยู่ หากบริการ sleep การแจ้งเตือนอาจเลื่อนจน service ตื่นอีกครั้ง สำหรับใช้งานจริงแบบต้องตรงเวลา ให้ใช้ instance ที่ไม่ sleep หรือเรียก `/jobs/reminder` จาก scheduler ภายนอกโดยตั้ง `CRON_SECRET`.
