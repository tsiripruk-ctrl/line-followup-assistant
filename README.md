# LINE Follow-up Assistant v0.3.1

AI เลขานุการติดตามงานจากกลุ่ม LINE แบบเงียบในกลุ่มและรายงานเจ้าของระบบทางแชตส่วนตัว

## ความสามารถ v0.3.1
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

ควรได้ version `0.3.1`

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


## Reliable Reminder Engine (v0.3.1)

เพื่อไม่ให้ Render Free sleep แล้วพลาดเวลาเตือน ให้ตั้ง `CRON_SECRET` ใน Render แล้วใช้ external scheduler เรียก:

`GET https://<your-service>.onrender.com/jobs/tick` ทุก 5 นาที

แนะนำส่ง secret ผ่าน header:

`X-Cron-Secret: <CRON_SECRET>`

ถ้าบริการ cron ส่ง header ไม่ได้ สามารถใช้ `?secret=<CRON_SECRET>` ได้ แต่ header ปลอดภัยกว่าเพราะ secret ไม่ไปอยู่ใน URL/log

`/jobs/tick` จะทำ 3 อย่างในคำขอเดียว:
- ปลุก Render Web Service
- ตรวจ Task ที่ถึงเวลาติดตามและส่ง LINE (ยังเคารพ Quiet Hours)
- Catch-up Daily Brief ถ้า service หลับตอน 07:30/18:30 แล้วเพิ่งถูกปลุกภายหลัง

ตรวจสถานะ scheduler ได้ที่ `/jobs/status` โดยใช้ secret แบบเดียวกัน

### สำคัญเรื่องฐานข้อมูล
SQLite บน Render Free เป็น filesystem ชั่วคราวและอาจสูญข้อมูลเมื่อ instance ถูกสร้างใหม่หรือ redeploy สำหรับใช้งานจริงควรตั้ง `DATABASE_URL` เป็น PostgreSQL ถาวร ระบบ v0.3.1 รองรับ PostgreSQL แล้ว (`postgresql://...`).


### ทดสอบหลัง Deploy
1. ตรวจ `/health` ต้องได้ `0.3.1`
2. ตั้ง `CRON_SECRET` ใน Render แล้ว Deploy ใหม่ (เมื่อมีค่านี้ scheduler ภายในจะปิดอัตโนมัติ เพื่อไม่ให้เตือนซ้ำ)
3. เรียก `/jobs/test` พร้อม `X-Cron-Secret` หนึ่งครั้ง คุณต้องได้รับ LINE ส่วนตัวว่า “ทดสอบ Reliable Reminder สำเร็จ”
4. ตั้ง external cron ให้เรียก `/jobs/tick` ทุก 5 นาทีด้วย header เดิม
5. ตรวจ `/jobs/status` เพื่อดูจำนวน Task ที่ถึงเวลาเตือนและสถานะ Quiet Hours

หมายเหตุ: `/jobs/tick` เคารพ Quiet Hours เสมอ งานที่ถึงกำหนดกลางคืนจะยังค้างเป็น due และส่งในการ tick แรกหลัง Quiet Hours จบ ไม่ถูกทิ้งหาย
