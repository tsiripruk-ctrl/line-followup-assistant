# LINE Follow-up Assistant (MVP)

AI เลขานุการติดตามงานในกลุ่ม LINE: อ่านข้อความจาก LINE OA, ตรวจจับคำสั่งงาน, บันทึก Task แบบเงียบ, เตือนกลับเข้ากลุ่มเมื่อถึงเวลา และให้เจ้าของถามสรุปงานค้างทางแชตส่วนตัว

## สิ่งที่ MVP นี้ทำได้
- รับ LINE webhook และตรวจสอบ `x-line-signature`
- อ่านข้อความ text ในกลุ่มที่ OA ถูกเชิญเข้า
- ดึงชื่อสมาชิกกลุ่มจาก LINE API
- ใช้ OpenAI Structured Output จำแนกข้อความเป็น task / status update
- บันทึกข้อความและ task ใน SQLite
- ไม่ตอบทุกข้อความในกลุ่ม
- Reminder engine ส่งข้อความตามงานกลับเข้ากลุ่ม
- รับคำสั่งจาก LINE ส่วนตัวของเจ้าของ เช่น `วันนี้มีอะไรต้องตาม`, `สรุปงานค้าง`

## 1) เตรียม LINE
1. สร้าง LINE Official Account และ Messaging API channel
2. ใน LINE Developers Console เปิด **Allow bot to join group chats**
3. เปิด **Use webhook**
4. เก็บ Channel secret และ Channel access token
5. เชิญ OA เข้า “กลุ่มทดลอง” เพียง 1 กลุ่มก่อน

> LINE จำกัด Official Account ในกลุ่มหนึ่งได้ครั้งละ 1 OA

## 2) เตรียม OpenAI
สร้าง API key และใส่ใน `.env` ค่าเริ่มต้นใช้ `gpt-5.6-luna` เพื่อลดต้นทุนสำหรับงานอ่านข้อความจำนวนมาก

## 3) ติดตั้ง
```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

แก้ `.env`:
```env
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
OPENAI_API_KEY=...
OWNER_LINE_USER_ID=Uxxxxxxxx
OWNER_DISPLAY_NAME=พี่ต้อง
```

## 4) รัน
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
เปิด `http://localhost:8000/health` ต้องได้ `{"ok": true, ...}`

## 5) ทำให้ LINE เข้า webhook ได้
สำหรับทดสอบ local ใช้ tunnel ที่ให้ HTTPS public URL แล้วตั้ง webhook เป็น:
`https://YOUR_PUBLIC_URL/webhook`

จาก LINE Developers กด Verify webhook

## 6) วิธีหา OWNER_LINE_USER_ID
แอด OA เป็นเพื่อน แล้วส่งข้อความหา OA หนึ่งครั้ง จาก log/webhook จะได้ `source.userId` ของบัญชีคุณ จากนั้นนำไปใส่ `.env`

## พฤติกรรมที่ตั้งใจไว้
ข้อความกลุ่ม:
`ต้นครับ พรุ่งนี้ช่วยเช็กของปากน้ำประแสว่าของเข้าได้กี่รายการ แล้วแจ้งผมด้วย`

ระบบจะเก็บเป็น task เงียบ ๆ ไม่ตอบทันที หากถึงเวลายังไม่ปิด task ระบบจะส่งข้อความประมาณ:
`ต้นครับ ขออัปเดตเรื่อง ‘เช็กสถานะสินค้าปากน้ำประแส’ ให้พี่ต้องหน่อยครับ ถ้าเรียบร้อยแล้วแจ้งได้เลยครับ`

เมื่อมีข้อความ:
`เรียบร้อยครับ ของเข้าแล้ว 12 รายการ เหลือ 3 รายการรอ Supplier`
AI จะพยายามเปลี่ยนสถานะ task ที่เกี่ยวข้องเป็น WAITING/COMPLETED ตามบริบท

## ข้อจำกัดของ MVP 0.1
- matching ข้อความตอบกลับกับ task ยังเป็น heuristic; รอบถัดไปควรเพิ่ม conversation/thread matching และ embedding
- ยังไม่มีหน้า Dashboard
- ยังไม่มีปุ่มยืนยัน/แก้ไข task
- ยังไม่อ่านข้อความรูป/ไฟล์/voice
- SQLite เหมาะกับ pilot; production ควรเปลี่ยน PostgreSQL
- ควรเพิ่ม audit log, role/permission และ data-retention policy ก่อนใช้กับหลายกลุ่ม
