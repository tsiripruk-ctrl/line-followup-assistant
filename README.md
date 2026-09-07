# LINE Follow-up Assistant v0.2

AI secretary for LINE group follow-up.

## v0.2 features
- Detect follow-up tasks from LINE group messages.
- Quiet in the group when a task is created; privately acknowledge the owner.
- Automatic reminders before/after due time.
- Quiet hours (default 20:00-07:00 Asia/Bangkok).
- Detect replies such as "กำลังทำ", "รอ supplier", "เรียบร้อยแล้ว" and update task status.
- Stop reminders automatically when completed.
- Owner private commands:
  - `สรุปงานค้าง`
  - `วันนี้มีอะไรต้องตาม`
  - `งานเลยกำหนด`
  - `งานที่ปิดแล้ว`
  - `ปิด FU-xxxxxx-xxxx`

## Render
Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Health check:

`/health`

LINE webhook:

`/webhook`

## Important: background reminders on Render Free
The in-process scheduler only runs while the web service is awake. A free/sleeping instance is suitable for testing, but it is not reliable for exact business reminders. For production, use an always-on instance or an external scheduled trigger/worker.

## Environment variables
Copy `.env.example` values into Render Environment. Never commit real secrets to GitHub.
