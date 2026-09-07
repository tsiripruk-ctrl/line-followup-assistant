from datetime import datetime, timedelta, timezone
import asyncio
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from db import Base, engine, SessionLocal
from models import Message, Task
from config import settings
from line_api import verify_signature, get_member_profile, push_text
from ai import extract_task
from service import (
    create_task, open_tasks, completed_tasks, get_task_by_code, tasks_due_today,
    overdue_tasks, format_task, choose_status_target, STATUS_THAI
)

app = FastAPI(title="LINE Follow-up Assistant", version="0.2.0")
scheduler = AsyncIOScheduler(timezone=settings.timezone)

@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    scheduler.add_job(reminder_scan, "interval", seconds=settings.reminder_check_seconds, id="reminder_scan", replace_existing=True)
    scheduler.start()
    print("LINE Follow-up Assistant v0.2 started")

@app.get("/health")
def health():
    return {"ok": True, "service": "line-followup-assistant", "version": "0.2.0"}

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
    try:
        await _process_message(event)
    except Exception as exc:
        print("process_message failed:", repr(exc))

async def _process_message(event: dict):
    msg = event["message"]
    source = event.get("source", {})
    source_type = source.get("type", "unknown")
    source_id = source.get("groupId") or source.get("userId") or source.get("roomId")
    user_id = source.get("userId")
    if not source_id:
        return

    display_name = None
    if source_type == "group" and user_id:
        display_name = await get_member_profile(source_id, user_id)

    text = msg.get("text", "").strip()
    with SessionLocal() as db:
        if db.scalar(select(Message).where(Message.line_message_id == msg["id"])):
            return
        db.add(Message(
            line_message_id=msg["id"], source_type=source_type, source_id=source_id,
            user_id=user_id, display_name=display_name, text=text
        ))
        db.commit()

    # First-time owner setup.
    if source_type == "user" and settings.owner_line_user_id.strip().upper() == "TEMP":
        await push_text(user_id, f"เชื่อมต่อสำเร็จครับ\nLINE User ID ของคุณคือ:\n{user_id}\n\nให้นำค่านี้ไปใส่ใน Render ที่ OWNER_LINE_USER_ID แล้ว Deploy ใหม่ครับ")
        return

    # Private command center for owner.
    if source_type == "user" and user_id == settings.owner_line_user_id:
        await handle_owner_command(user_id, text)
        return

    # Analyze only group text for tasks/status updates.
    if source_type != "group":
        return

    extraction = await asyncio.to_thread(extract_task, text, display_name)

    if extraction.status_signal != "none":
        changed = await try_update_task_from_status(source_id, display_name, extraction, text)
        if changed and settings.owner_line_user_id:
            await push_text(settings.owner_line_user_id, changed)
        return

    if extraction.is_task and extraction.confidence >= settings.auto_create_confidence:
        with SessionLocal() as db:
            task = create_task(db, source_id, msg["id"], extraction)
            confirmation = format_task(task)
        print("task created:", task.task_code, task.title)
        # Stay quiet in the group, but privately confirm to owner.
        if settings.owner_task_ack and settings.owner_line_user_id:
            await push_text(settings.owner_line_user_id, "ผมรับเรื่องติดตามจากกลุ่มแล้วครับ\n\n" + confirmation)

async def try_update_task_from_status(group_id: str, sender_name: str | None, extraction, text: str) -> str | None:
    with SessionLocal() as db:
        tasks = open_tasks(db, group_id)
        target = choose_status_target(tasks, sender_name, extraction.assignee_name, extraction.related_task_hint)
        if not target:
            return None

        mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
        new_status = mapping.get(extraction.status_signal)
        if not new_status:
            return None

        old_status = target.status
        target.status = new_status
        target.notes = ((target.notes or "") + f"\n{datetime.now()}: {sender_name or '-'}: {text}").strip()
        if new_status == "COMPLETED":
            target.next_reminder_at = None
        elif target.due_at and datetime.utcnow() > target.due_at:
            target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
        else:
            target.next_reminder_at = datetime.utcnow() + timedelta(hours=6)
        db.commit()
        db.refresh(target)

        if old_status == new_status and new_status != "COMPLETED":
            return None
        status_th = STATUS_THAI.get(new_status, new_status)
        return f"อัปเดตงานจากบทสนทนาในกลุ่มแล้วครับ\n\n{target.task_code} {target.title}\nผู้ตอบ: {sender_name or '-'}\nสถานะใหม่: {status_th}"

async def handle_owner_command(user_id: str, text: str):
    raw = text.strip()
    low = raw.lower()

    # Manual completion: ปิด FU-260907-0001
    if low.startswith("ปิด ") or low.startswith("เสร็จ "):
        parts = raw.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        with SessionLocal() as db:
            task = get_task_by_code(db, code)
            if not task:
                await push_text(user_id, f"ไม่พบงาน {code} ครับ")
                return
            task.status = "COMPLETED"
            task.next_reminder_at = None
            db.commit()
            await push_text(user_id, f"ปิดงาน {task.task_code} เรียบร้อยครับ\n{task.title}")
        return

    if "เลยกำหนด" in low or "เกินกำหนด" in low:
        with SessionLocal() as db:
            tasks = overdue_tasks(db)
        await send_task_list(user_id, "งานที่เลยกำหนด", tasks)
        return

    if "วันนี้" in low:
        with SessionLocal() as db:
            tasks = tasks_due_today(db)
        await send_task_list(user_id, "งานที่ครบกำหนดวันนี้", tasks)
        return

    if "เสร็จแล้ว" in low or "งานที่ปิด" in low:
        with SessionLocal() as db:
            tasks = completed_tasks(db, 10)
        await send_task_list(user_id, "งานที่ปิดล่าสุด", tasks)
        return

    if any(k in low for k in ["งานค้าง", "ต้องตาม", "สรุปงาน", "ทั้งหมด"]):
        with SessionLocal() as db:
            tasks = open_tasks(db)
        await send_task_list(user_id, "สรุปงานติดตามที่ยังไม่ปิด", tasks)
        return

    await push_text(user_id,
        "สั่งผมได้แบบนี้ครับ\n"
        "• สรุปงานค้าง\n"
        "• วันนี้มีอะไรต้องตาม\n"
        "• งานเลยกำหนด\n"
        "• งานที่ปิดแล้ว\n"
        "• ปิด FU-xxxxxx-xxxx"
    )

async def send_task_list(user_id: str, title: str, tasks: list[Task]):
    if not tasks:
        await push_text(user_id, f"{title}: ไม่มีรายการครับ")
        return
    lines = [f"{title}ครับ", ""]
    for t in tasks[:15]:
        lines.append(format_task(t))
        lines.append("")
    if len(tasks) > 15:
        lines.append(f"และมีอีก {len(tasks)-15} รายการ")
    await push_text(user_id, "\n".join(lines))

def in_quiet_hours() -> bool:
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    start, end = settings.quiet_hour_start, settings.quiet_hour_end
    if start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end

async def reminder_scan():
    if in_quiet_hours():
        return
    now = datetime.utcnow()
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(
            Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
            Task.next_reminder_at != None,
            Task.next_reminder_at <= now,
        ).order_by(Task.next_reminder_at.asc())).all())

        for t in tasks:
            try:
                if t.due_at and now > t.due_at:
                    t.status = "OVERDUE"

                prefix = f"{t.assignee_name}ครับ " if t.assignee_name else "รบกวนทีมครับ "
                if t.status == "OVERDUE":
                    body = (
                        f"{prefix}ขออัปเดตเรื่อง ‘{t.title}’ หน่อยครับ "
                        f"เรื่องนี้เลยกำหนดแล้ว ถ้ายังติดอะไรอยู่แจ้งไว้ได้เลยครับ"
                    )
                    t.next_reminder_at = now + timedelta(hours=settings.reminder_repeat_hours)
                elif t.status == "WAITING":
                    body = (
                        f"{prefix}ขออัปเดตเรื่อง ‘{t.title}’ หน่อยครับ "
                        f"เรื่องที่รออยู่ตอนนี้มีความคืบหน้าเพิ่มเติมไหมครับ"
                    )
                    t.next_reminder_at = now + timedelta(hours=6)
                elif t.status == "IN_PROGRESS":
                    body = (
                        f"{prefix}ขออัปเดตความคืบหน้าเรื่อง ‘{t.title}’ ให้{settings.owner_display_name}หน่อยครับ "
                        f"ถ้าเรียบร้อยแล้วแจ้งได้เลยครับ"
                    )
                    t.next_reminder_at = now + timedelta(hours=6)
                else:
                    body = (
                        f"{prefix}ขออัปเดตเรื่อง ‘{t.title}’ ให้{settings.owner_display_name}หน่อยครับ "
                        f"ถ้าเรียบร้อยแล้วแจ้งได้เลยครับ"
                    )
                    t.next_reminder_at = now + timedelta(hours=settings.reminder_repeat_hours)

                await push_text(t.group_id, body)
                t.reminder_count += 1
                t.last_reminded_at = now
                db.commit()
                print("reminder sent:", t.task_code, t.status)
            except Exception as exc:
                db.rollback()
                print("reminder push failed:", t.task_code, repr(exc))
