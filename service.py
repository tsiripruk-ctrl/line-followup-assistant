from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models import Task, SystemEvent
from config import settings

OPEN_STATUSES = {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"}
STATUS_THAI = {
    "OPEN": "รอดำเนินการ",
    "IN_PROGRESS": "กำลังดำเนินการ",
    "WAITING": "รอข้อมูล/บุคคลอื่น",
    "OVERDUE": "เลยกำหนด",
    "COMPLETED": "เสร็จแล้ว",
    "CANCELLED": "ยกเลิก",
}


def utcnow() -> datetime:
    return datetime.utcnow()


def local_now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def task_code(task_id: int) -> str:
    return f"FU-{local_now().strftime('%y%m%d')}-{task_id:04d}"


def parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dtparser.isoparse(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed.replace(tzinfo=ZoneInfo(settings.timezone)).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def first_reminder_at(due: datetime | None) -> datetime:
    now = utcnow()
    if not due:
        return now + timedelta(days=1)
    candidate = due - timedelta(hours=settings.remind_before_due_hours)
    return max(candidate, now + timedelta(minutes=1))


def create_task(db: Session, group_id: str, source_message_id: str, extraction) -> Task:
    due = parse_due(extraction.due_at_iso)
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
        next_reminder_at=first_reminder_at(due),
        auto_created=True,
    )
    db.add(t)
    db.flush()
    t.task_code = task_code(t.id)
    db.commit()
    db.refresh(t)
    return t


def open_tasks(db: Session, group_id: str | None = None):
    q = select(Task).where(Task.status.in_(OPEN_STATUSES)).order_by(Task.due_at.asc().nullslast(), Task.id.desc())
    if group_id:
        q = q.where(Task.group_id == group_id)
    return list(db.scalars(q).all())


def completed_tasks(db: Session, limit: int = 10):
    q = select(Task).where(Task.status == "COMPLETED").order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit)
    return list(db.scalars(q).all())


def get_task_by_code(db: Session, code: str) -> Task | None:
    return db.scalar(select(Task).where(Task.task_code == code.upper().strip()))


def _local_day_range(days_from_today: int = 0):
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz) + timedelta(days=days_from_today)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def tasks_due_today(db: Session):
    start_utc, end_utc = _local_day_range(0)
    q = select(Task).where(
        Task.status.in_(OPEN_STATUSES),
        Task.due_at >= start_utc,
        Task.due_at < end_utc,
    ).order_by(Task.due_at.asc())
    return list(db.scalars(q).all())


def tasks_due_tomorrow(db: Session):
    start_utc, end_utc = _local_day_range(1)
    q = select(Task).where(
        Task.status.in_(OPEN_STATUSES),
        Task.due_at >= start_utc,
        Task.due_at < end_utc,
    ).order_by(Task.due_at.asc())
    return list(db.scalars(q).all())


def overdue_tasks(db: Session):
    now = utcnow()
    q = select(Task).where(Task.status.in_(OPEN_STATUSES), Task.due_at != None, Task.due_at < now).order_by(Task.due_at.asc())
    return list(db.scalars(q).all())


def waiting_tasks(db: Session):
    q = select(Task).where(Task.status == "WAITING").order_by(Task.due_at.asc().nullslast(), Task.id.desc())
    return list(db.scalars(q).all())


def completed_today(db: Session):
    start_utc, end_utc = _local_day_range(0)
    q = select(Task).where(
        Task.status == "COMPLETED",
        Task.updated_at >= start_utc,
        Task.updated_at < end_utc,
    ).order_by(Task.updated_at.desc())
    return list(db.scalars(q).all())


def format_due_local(t: Task) -> str:
    if not t.due_at:
        return "ยังไม่ระบุ"
    return t.due_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M")


def format_task(t: Task) -> str:
    who = t.assignee_name or "ยังไม่ระบุ"
    proj = f" | {t.project}" if t.project else ""
    status = STATUS_THAI.get(t.status, t.status)
    return f"{t.task_code} {t.title}{proj}\nผู้รับผิดชอบ: {who}\nกำหนด: {format_due_local(t)}\nสถานะ: {status}"


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    x = value.strip().lower()
    for prefix in ("คุณ", "พี่", "น้อง", "นาย", "นาง", "น.ส.", "นางสาว"):
        if x.startswith(prefix):
            x = x[len(prefix):].strip()
    return "".join(x.split())


def choose_status_target(tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None) -> Task | None:
    if not tasks:
        return None
    sender = normalize_name(sender_name)
    extracted_assignee = normalize_name(assignee_name)
    hint_low = (hint or "").lower().strip()

    if sender:
        matches = [t for t in tasks if normalize_name(t.assignee_name) == sender]
        if len(matches) == 1:
            return matches[0]
        if hint_low:
            for t in matches:
                if hint_low in t.title.lower() or t.title.lower() in hint_low:
                    return t
        if matches:
            return matches[0]

    if extracted_assignee:
        for t in tasks:
            if normalize_name(t.assignee_name) == extracted_assignee:
                return t
    if hint_low:
        for t in tasks:
            title = t.title.lower()
            if hint_low in title or title in hint_low:
                return t
    return tasks[0]


def event_exists(db: Session, event_key: str) -> bool:
    return db.scalar(select(SystemEvent).where(SystemEvent.event_key == event_key)) is not None


def record_event(db: Session, event_key: str) -> None:
    if not event_exists(db, event_key):
        db.add(SystemEvent(event_key=event_key))
        db.commit()


def brief_counts(db: Session) -> dict:
    return {
        "open": len(open_tasks(db)),
        "today": len(tasks_due_today(db)),
        "tomorrow": len(tasks_due_tomorrow(db)),
        "overdue": len(overdue_tasks(db)),
        "waiting": len(waiting_tasks(db)),
        "completed_today": len(completed_today(db)),
    }
