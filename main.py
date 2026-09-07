from datetime import datetime, timedelta
import asyncio
from fastapi import FastAPI, Request, HTTPException
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from db import Base, engine, SessionLocal
from models import Message, Task
from config import settings
from line_api import verify_signature, get_member_profile, push_text
from ai import extract_task
from service import create_task, open_tasks, format_task

app = FastAPI(title="LINE Follow-up Assistant", version="0.1.0")
scheduler = AsyncIOScheduler(timezone=settings.timezone)

@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    scheduler.add_job(reminder_scan, "interval", seconds=settings.reminder_check_seconds, id="reminder_scan", replace_existing=True)
    scheduler.start()

@app.get("/health")
def health():
    return {"ok": True, "service": "line-followup-assistant"}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-line-signature")
    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    payload = await request.json()
    for event in payload.get("events", []):
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            asyncio.create_task(process_message(event))
    return {"ok": True}

async def process_message(event: dict):
    msg = event["message"]
    source = event.get("source", {})
    source_type = source.get("type", "unknown")
    group_id = source.get("groupId") or source.get("userId") or source.get("roomId")
    user_id = source.get("userId")
    if not group_id:
        return
    display_name = None
    if source_type == "group" and user_id:
        display_name = await get_member_profile(group_id, user_id)
    text = msg.get("text", "").strip()
    with SessionLocal() as db:
        if db.scalar(select(Message).where(Message.line_message_id == msg["id"])):
            return
        db.add(Message(line_message_id=msg["id"], source_type=source_type, source_id=group_id, user_id=user_id, display_name=display_name, text=text))
        db.commit()

    # คำสั่งส่วนตัวของเจ้าของ: ไม่ส่งเข้ากลุ่ม
    if source_type == "user" and user_id == settings.owner_line_user_id:
        await handle_owner_command(user_id, text)
        return

    # MVP: วิเคราะห์เฉพาะข้อความกลุ่ม
    if source_type != "group":
        return
    extraction = await asyncio.to_thread(extract_task, text, display_name)
    if extraction.status_signal != "none":
        await try_update_task_from_status(group_id, extraction, text)
        return
    if extraction.is_task and extraction.confidence >= settings.auto_create_confidence:
        with SessionLocal() as db:
            create_task(db, group_id, msg["id"], extraction)
        # ตั้งใจเงียบ: ไม่ตอบในกลุ่มทันที

async def try_update_task_from_status(group_id: str, extraction, text: str):
    with SessionLocal() as db:
        tasks = open_tasks(db, group_id)
        if not tasks:
            return
        # MVP heuristic: เลือก task ล่าสุดที่ title/assignee ใกล้เคียง; ถ้าไม่เจอให้ล่าสุด
        target = tasks[0]
        hint = (extraction.related_task_hint or "").lower()
        for t in tasks:
            if hint and (hint in t.title.lower() or t.title.lower() in hint):
                target = t; break
            if extraction.assignee_name and t.assignee_name == extraction.assignee_name:
                target = t; break
        mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
        target.status = mapping.get(extraction.status_signal, target.status)
        target.notes = ((target.notes or "") + f"\n{datetime.now()}: {text}").strip()
        target.next_reminder_at = None if target.status == "COMPLETED" else datetime.utcnow() + timedelta(hours=6)
        db.commit()

async def handle_owner_command(user_id: str, text: str):
    low = text.lower().strip()
    if any(k in low for k in ["งานค้าง", "ต้องตาม", "วันนี้มีอะไร", "สรุปงาน"]):
        with SessionLocal() as db:
            tasks = open_tasks(db)
            if not tasks:
                await push_text(user_id, "ตอนนี้ไม่มีงานติดตามที่ค้างอยู่ครับ")
                return
            lines = ["สรุปงานติดตามที่ยังไม่ปิดครับ", ""]
            for t in tasks[:15]:
                lines.append(format_task(t)); lines.append("")
            await push_text(user_id, "\n".join(lines))
    else:
        await push_text(user_id, "ลองถามผมว่า ‘วันนี้มีอะไรต้องตาม’ หรือ ‘สรุปงานค้าง’ ได้เลยครับ")

async def reminder_scan():
    now = datetime.utcnow()
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]), Task.next_reminder_at != None, Task.next_reminder_at <= now)).all())
        for t in tasks:
            if t.due_at and now > t.due_at:
                t.status = "OVERDUE"
            prefix = f"{t.assignee_name}ครับ " if t.assignee_name else "รบกวนทีมครับ "
            if t.status == "OVERDUE":
                body = f"{prefix}ขออัปเดตเรื่อง ‘{t.title}’ หน่อยครับ เรื่องนี้เลยกำหนดแล้ว ถ้ายังติดอะไรอยู่แจ้งได้เลยครับ"
                t.next_reminder_at = now + timedelta(hours=4)
            else:
                body = f"{prefix}ขออัปเดตเรื่อง ‘{t.title}’ ให้{settings.owner_display_name}หน่อยครับ ถ้าเรียบร้อยแล้วแจ้งได้เลยครับ"
                t.next_reminder_at = now + timedelta(hours=8)
            t.reminder_count += 1
            t.last_reminded_at = now
            db.commit()
            try:
                await push_text(t.group_id, body)
            except Exception as e:
                print("push failed", e)
