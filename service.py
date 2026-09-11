from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models import Task, TaskEvent, Person, PersonAlias, SystemEvent
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
    """Choose the first reminder time without immediately chasing newly-created tasks.

    Adaptive policy:
    - > medium window remaining: remind `remind_before_due_hours` before due
    - between short/medium windows: remind `adaptive_medium_lead_minutes` before due
    - less than the short window: wait until the due time
    - already overdue: remind shortly after creation

    This prevents the old behavior where a task created after the normal pre-reminder
    point was followed up almost immediately (now + 1 minute).
    """
    now = utcnow()
    if not due:
        return now + timedelta(days=1)

    remaining = due - now
    if remaining.total_seconds() <= 0:
        return now + timedelta(minutes=1)

    short_window = timedelta(hours=settings.adaptive_short_notice_hours)
    medium_window = timedelta(hours=settings.adaptive_medium_notice_hours)

    if remaining > medium_window:
        return due - timedelta(hours=settings.remind_before_due_hours)
    if remaining >= short_window:
        return due - timedelta(minutes=settings.adaptive_medium_lead_minutes)
    return due


def create_task(
    db: Session, group_id: str, source_message_id: str, extraction,
    source_text: str | None = None, actor_name: str | None = None, actor_user_id: str | None = None,
    assignee_name_override: str | None = None, assignee_user_id: str | None = None,
) -> Task:
    due = parse_due(extraction.due_at_iso)
    raw_assignee = assignee_name_override or extraction.assignee_name
    canonical_assignee = resolve_or_register_assignee(db, raw_assignee)
    if assignee_user_id:
        person = bind_person_identity(db, canonical_assignee or raw_assignee or "ผู้รับผิดชอบ", assignee_user_id, raw_assignee)
        canonical_assignee = person.canonical_name
    else:
        person = get_person_by_alias(db, canonical_assignee or raw_assignee)
        if person and person.line_user_id:
            assignee_user_id = person.line_user_id
    t = Task(
        task_code="TEMP",
        group_id=group_id,
        source_message_id=source_message_id,
        title=extraction.title or "งานติดตามจาก LINE",
        project=extraction.project,
        assignee_name=canonical_assignee or raw_assignee,
        assignee_user_id=assignee_user_id,
        due_at=due,
        confidence=extraction.confidence,
        status="OPEN",
        next_reminder_at=first_reminder_at(due),
        auto_created=True,
    )
    db.add(t)
    db.flush()
    t.task_code = task_code(t.id)
    record_task_event(
        db, t, "CREATED", actor_name=actor_name, actor_user_id=actor_user_id,
        text=source_text or "สร้างงานจาก LINE", new_status="OPEN", commit=False,
    )
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


def choose_status_target(tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None, sender_user_id: str | None = None) -> Task | None:
    if not tasks:
        return None
    sender = normalize_name(sender_name)
    extracted_assignee = normalize_name(assignee_name)
    hint_low = (hint or "").lower().strip()

    # LINE userId is the strongest identity signal. Prefer it over display names.
    if sender_user_id:
        id_matches = [t for t in tasks if t.assignee_user_id == sender_user_id]
        if len(id_matches) == 1:
            return id_matches[0]
        if hint_low:
            for t in id_matches:
                if hint_low in t.title.lower() or t.title.lower() in hint_low:
                    return t
        if id_matches:
            return id_matches[0]

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




def clean_display_name(value: str | None) -> str:
    if not value:
        return ""
    x = " ".join(value.strip().split())
    for prefix in ("คุณ", "พี่", "น้อง", "นาย", "นาง", "น.ส.", "นางสาว"):
        if x.startswith(prefix):
            x = x[len(prefix):].strip()
            break
    return x


def get_person_by_alias(db: Session, name: str | None) -> Person | None:
    key = normalize_name(name)
    if not key:
        return None
    alias = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key))
    if not alias:
        return None
    return db.get(Person, alias.person_id)


def resolve_canonical_name(db: Session, name: str | None) -> str | None:
    if not name:
        return None
    person = get_person_by_alias(db, name)
    return person.canonical_name if person else clean_display_name(name)


def resolve_or_register_assignee(db: Session, name: str | None) -> str | None:
    if not name:
        return None
    person = get_person_by_alias(db, name)
    if person:
        return person.canonical_name
    canonical = clean_display_name(name)
    person = ensure_person(db, canonical)
    alias_key = normalize_name(name)
    existing = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == alias_key))
    if not existing and alias_key:
        db.add(PersonAlias(person_id=person.id, alias=name.strip(), normalized_alias=alias_key))
        db.flush()
    return person.canonical_name


def ensure_person(db: Session, canonical_name: str, line_user_id: str | None = None) -> Person:
    canonical_name = clean_display_name(canonical_name)
    person = db.scalar(select(Person).where(func.lower(Person.canonical_name) == canonical_name.lower()))
    if not person:
        person = Person(canonical_name=canonical_name, line_user_id=line_user_id or None)
        db.add(person)
        db.flush()
    elif line_user_id and not person.line_user_id:
        person.line_user_id = line_user_id
    key = normalize_name(canonical_name)
    if key and not db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key)):
        db.add(PersonAlias(person_id=person.id, alias=canonical_name, normalized_alias=key))
        db.flush()
    return person


def bind_person_identity(db: Session, canonical_name: str, line_user_id: str, alias_name: str | None = None) -> Person:
    """Bind a stable LINE userId to the People Registry and learn aliases.

    LINE userId is treated as the strongest identity key. If a person with that
    userId already exists, new display names are added as aliases instead of
    creating a duplicate person.
    """
    canonical_name = clean_display_name(canonical_name) or clean_display_name(alias_name) or "ผู้รับผิดชอบ"
    person = db.scalar(select(Person).where(Person.line_user_id == line_user_id))
    if not person:
        person = db.scalar(select(Person).where(func.lower(Person.canonical_name) == canonical_name.lower()))
        if person and not person.line_user_id:
            person.line_user_id = line_user_id
        elif not person:
            person = Person(canonical_name=canonical_name, line_user_id=line_user_id)
            db.add(person)
            db.flush()
    for alias_value in {canonical_name, clean_display_name(alias_name)}:
        key = normalize_name(alias_value)
        if key and not db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key)):
            db.add(PersonAlias(person_id=person.id, alias=alias_value, normalized_alias=key))
    db.flush()
    return person


def set_person_alias(db: Session, alias_name: str, canonical_name: str) -> tuple[Person, int]:
    """Map alias_name to canonical_name and normalize existing task rows."""
    alias_name = clean_display_name(alias_name)
    canonical_name = clean_display_name(canonical_name)
    if not alias_name or not canonical_name:
        raise ValueError("alias and canonical name are required")
    person = ensure_person(db, canonical_name)
    alias_key = normalize_name(alias_name)
    existing = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == alias_key))
    previous_person_id = existing.person_id if existing else None
    if existing:
        existing.person_id = person.id
        existing.alias = alias_name
    else:
        db.add(PersonAlias(person_id=person.id, alias=alias_name, normalized_alias=alias_key))
    updated = 0
    for task in db.scalars(select(Task)).all():
        if normalize_name(task.assignee_name) == alias_key and task.assignee_name != person.canonical_name:
            old = task.assignee_name
            task.assignee_name = person.canonical_name
            record_task_event(
                db, task, "ASSIGNEE_NORMALIZED", actor_name="Owner",
                text=f"ปรับชื่อผู้รับผิดชอบจาก {old or '-'} → {person.canonical_name}", commit=False,
            )
            updated += 1
    if previous_person_id and previous_person_id != person.id:
        remaining = db.scalar(select(func.count(PersonAlias.id)).where(PersonAlias.person_id == previous_person_id)) or 0
        if remaining == 0:
            old_person = db.get(Person, previous_person_id)
            if old_person:
                old_person.active = False
    db.commit()
    return person, updated


def list_people(db: Session) -> list[dict]:
    people = list(db.scalars(select(Person).where(Person.active == True).order_by(Person.canonical_name.asc())).all())
    result = []
    for p in people:
        aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == p.id).order_by(PersonAlias.alias.asc())).all())
        result.append({
            "id": p.id,
            "canonical_name": p.canonical_name,
            "line_user_id": p.line_user_id,
            "aliases": [a.alias for a in aliases],
        })
    return result


def record_task_event(
    db: Session, task: Task, event_type: str, *, actor_name: str | None = None,
    actor_user_id: str | None = None, text: str | None = None,
    old_status: str | None = None, new_status: str | None = None, commit: bool = True,
) -> TaskEvent:
    ev = TaskEvent(
        task_id=task.id, event_type=event_type, actor_name=actor_name,
        actor_user_id=actor_user_id, text=text, old_status=old_status, new_status=new_status,
    )
    db.add(ev)
    if commit:
        db.commit()
        db.refresh(ev)
    return ev


def task_timeline(db: Session, task: Task) -> list[TaskEvent]:
    return list(db.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
    ).all())


def backfill_task_created_events(db: Session) -> int:
    """Create one baseline timeline row for older tasks that predate v0.5."""
    count = 0
    for task in db.scalars(select(Task)).all():
        exists = db.scalar(select(TaskEvent.id).where(TaskEvent.task_id == task.id).limit(1))
        if not exists:
            ev = TaskEvent(
                task_id=task.id, event_type="IMPORTED", actor_name="System",
                text="งานเดิมก่อนอัปเกรด v0.5", new_status=task.status,
                created_at=task.created_at or utcnow(),
            )
            db.add(ev)
            count += 1
    if count:
        db.commit()
    return count

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


def search_open_tasks(db: Session, query: str = "", project: str = "", assignee: str = "", status: str = ""):
    q = select(Task).order_by(Task.due_at.asc().nullslast(), Task.id.desc())
    if status:
        if status.upper() == "ACTIVE":
            q = q.where(Task.status.in_(OPEN_STATUSES))
        else:
            q = q.where(Task.status == status.upper())
    else:
        q = q.where(Task.status.in_(OPEN_STATUSES))
    if query:
        term = f"%{query.strip()}%"
        q = q.where((Task.title.ilike(term)) | (Task.project.ilike(term)) | (Task.assignee_name.ilike(term)))
    if project:
        q = q.where(Task.project.ilike(f"%{project.strip()}%"))
    if assignee:
        q = q.where(Task.assignee_name.ilike(f"%{assignee.strip()}%"))
    return list(db.scalars(q.limit(200)).all())


def task_stats(db: Session) -> dict:
    return brief_counts(db)
