from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import Task
from config import settings

THAI_OPEN = {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"}

def task_code(task_id: int) -> str:
    return f"FU-{datetime.now().strftime('%y%m%d')}-{task_id:04d}"

def create_task(db: Session, group_id: str, source_message_id: str, extraction) -> Task:
    due = None
    if extraction.due_at_iso:
        try:
            parsed = dtparser.isoparse(extraction.due_at_iso)
            if parsed.tzinfo is not None:
                due = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                due = parsed
        except Exception:
            pass
    t = Task(
        task_code="TEMP",
        group_id=group_id,
        source_message_id=source_message_id,
        title=extraction.title or "งานติดตามจาก LINE",
        project=extraction.project,
        assignee_name=extraction.assignee_name,
        due_at=due,
        confidence=extraction.confidence,
        status="OPEN",
        next_reminder_at=due - timedelta(hours=3) if due else datetime.utcnow() + timedelta(days=1),
        auto_created=True,
    )
    db.add(t); db.flush(); t.task_code = task_code(t.id); db.commit(); db.refresh(t)
    return t

def open_tasks(db: Session, group_id: str | None = None):
    q = select(Task).where(Task.status.in_(THAI_OPEN)).order_by(Task.due_at.asc().nullslast(), Task.id.desc())
    if group_id:
        q = q.where(Task.group_id == group_id)
    return list(db.scalars(q).all())

def format_task(t: Task) -> str:
    due = (t.due_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M") if t.due_at else "ยังไม่ระบุ")
    who = t.assignee_name or "ยังไม่ระบุ"
    proj = f" | {t.project}" if t.project else ""
    return f"{t.task_code} {t.title}{proj}\nผู้รับผิดชอบ: {who}\nกำหนด: {due}\nสถานะ: {t.status}"
