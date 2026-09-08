# LINE Follow-up Assistant v0.4.0

v0.4 เพิ่ม **Web Command Center** สำหรับเจ้าของระบบ โดยไม่เปลี่ยน workflow LINE เดิม

## ความสามารถหลัก
- LINE group → AI จับคำสั่งงาน → Task
- Reminder ผ่าน `/jobs/tick` และ External Cron
- Daily brief เช้า/เย็น
- อ่านคำตอบในกลุ่มแล้วอัปเดตสถานะงาน
- Owner commands ทาง LINE
- **Web Dashboard** ดูงานค้าง/วันนี้/เลยกำหนด/รอข้อมูล/ปิดวันนี้/พรุ่งนี้
- Filter ตามคำค้น, โครงการ, ผู้รับผิดชอบ, สถานะ
- ปิดงานหรือเปลี่ยนเป็น “รอข้อมูล” จาก Dashboard
- API สำหรับต่อยอดระบบ Project Assistant

## Environment ใหม่
เพิ่มใน Render:

```env
DASHBOARD_TOKEN=<ตั้งรหัสสุ่มยาว 30-50 ตัวอักษร>
```

อย่าใช้รหัสเดียวกับ `CRON_SECRET` และอย่าเผยแพร่ค่า token

## เปิด Dashboard
หลัง Deploy:

```text
https://line-followup-assistant.onrender.com/dashboard?token=YOUR_DASHBOARD_TOKEN
```

## Owner LINE commands
- `สรุปงานค้าง`
- `วันนี้มีอะไรต้องตาม`
- `พรุ่งนี้มีอะไรต้องตาม`
- `งานเลยกำหนด`
- `งานรอข้อมูล`
- `งานที่ปิดแล้ว`
- `งานของ ต้น`
- `โครงการ ปากน้ำประแส`
- `ค้นหา กล้อง`
- `สรุปเช้า`
- `สรุปเย็น`
- `ปิด FU-xxxxxx-xxxx`

## Health check
`/health` จะแสดง version, scheduler mode, dashboard enabled และชนิดฐานข้อมูล โดยไม่เปิดเผย secret

## Production note
ถ้ายังใช้ `sqlite:///./followup.db` บน Render Free ข้อมูลอาจไม่ถาวรเมื่อ instance ถูกสร้างใหม่ ควรย้ายเป็น Render PostgreSQL แล้วตั้ง `DATABASE_URL` ก่อนใช้งานจริงระยะยาว
