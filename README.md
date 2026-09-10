# LINE Follow-up Assistant v0.5.1

v0.5 เพิ่ม **Task Timeline + Conversation History + Assignee Normalization** บนฐานเดิมของ v0.4 โดยไม่ลบ Task เก่า

## ความสามารถใหม่

### 1) Task Timeline
ทุก Task มีประวัติแบบ append-only เช่น
- สร้างงานจาก LINE
- ข้อความตอบ/อัปเดตจากผู้รับผิดชอบ
- เปลี่ยนสถานะจากบทสนทนา
- Reminder ที่ระบบส่ง
- เปลี่ยนสถานะจาก Dashboard
- ปิดงานจาก LINE ส่วนตัว
- การปรับชื่อผู้รับผิดชอบให้เป็นมาตรฐาน

Dashboard มีปุ่ม **ประวัติ** ต่อ Task เพื่อเปิด Timeline โดยไม่ออกจากหน้า Command Center

ใน LINE ส่วนตัวใช้คำสั่ง:

```text
ประวัติ FU-260909-0006
```

### 2) Conversation History
AI แยกข้อความที่เป็นการตอบ/อัปเดตงานเดิม แม้ยังไม่เปลี่ยนสถานะ และบันทึกเป็น `COMMENT` ใน Timeline

### 3) Assignee Normalization
ระบบตัดคำนำหน้าพื้นฐาน เช่น `พี่ต้น` → `ต้น` อัตโนมัติ และสามารถรวมชื่อข้ามรูปแบบได้ เช่น

```text
Tong Thanakrit = ต้น
```

ทำได้ 2 ทาง:
- Dashboard → ส่วน **จัดการชื่อผู้รับผิดชอบ**
- LINE ส่วนตัว:

```text
รวมชื่อ Tong Thanakrit = ต้น
```

เมื่อรวมชื่อ ระบบจะปรับ Task เดิมที่ใช้ alias นั้นให้เป็นชื่อมาตรฐาน และบันทึกเหตุการณ์ลง Timeline

## Upgrade จาก v0.4

1. อัปโหลดไฟล์ v0.5 ทับ Repository เดิม
2. Commit
3. Render → Deploy latest commit
4. `/health` ต้องขึ้น `version: 0.5.0`
5. เปิด Dashboard เดิม

ระบบใช้ `Base.metadata.create_all()` เพื่อสร้างตารางใหม่:
- `task_events`
- `people`
- `person_aliases`

**ไม่ drop ตาราง Task เดิม** และมี backfill สร้าง event `IMPORTED` ให้ Task เก่าที่มีอยู่แล้ว

## PostgreSQL
รองรับ Render Datastore URL โดย `db.py` จะแปลงอัตโนมัติ:
- `postgres://...`
- `postgresql://...`

เป็น `postgresql+psycopg://...` สำหรับ psycopg v3

ใช้ `Datastore URL` ของ Render ใน Environment Variable ชื่อ:

```text
DATABASE_URL
```

## Environment เดิม
v0.5 ไม่ต้องเพิ่ม secret ใหม่ ใช้ค่าจาก v0.4 ต่อได้ เช่น:

```env
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
OPENAI_API_KEY=...
OWNER_LINE_USER_ID=...
CRON_SECRET=...
DASHBOARD_TOKEN=...
DATABASE_URL=...
```

## Health check

```text
/health
```

ตัวอย่าง:

```json
{
  "ok": true,
  "service": "line-followup-assistant",
  "version": "0.5.0",
  "scheduler": "external",
  "dashboard": true,
  "database": "postgresql",
  "timeline": true,
  "assignee_normalization": true
}
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
- `ประวัติ FU-xxxxxx-xxxx`
- `รวมชื่อ Tong Thanakrit = ต้น`
- `สรุปเช้า`
- `สรุปเย็น`
- `ปิด FU-xxxxxx-xxxx`

## หมายเหตุ
Timeline ใหม่จะเก็บรายละเอียดเต็มตั้งแต่ v0.5 เป็นต้นไป ส่วน Task เก่าจะมี baseline `IMPORTED` และยังคงข้อมูล notes เดิมไว้


## v0.5.1 Adaptive Reminder Policy

แก้ปัญหาเพิ่งสร้างงานแล้ว OA ตามทันที เพราะเวลาการเตือนล่วงหน้าเดิมผ่านไปแล้ว

กติกาใหม่:
- เหลือมากกว่า 3 ชั่วโมง: เตือนล่วงหน้า 3 ชั่วโมง
- เหลือ 1–3 ชั่วโมง: เตือนก่อนกำหนด 30 นาที
- เหลือน้อยกว่า 1 ชั่วโมง: รอถึงเวลาครบกำหนด
- งานที่ครบกำหนดไปแล้วตอนสร้าง: เริ่มติดตามหลังสร้างประมาณ 1 นาที
- การเตือนก่อนกำหนดจะไม่เพิ่ม `reminder_count` และไม่ถูกนับเป็น escalation

ตัวอย่าง: สร้างงาน 13:23 กำหนด 15:30 -> เตือนล่วงหน้าเวลา 15:00 และถ้ายังไม่ปิดงาน จะตรวจอีกครั้งเมื่อถึง 15:30
