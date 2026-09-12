from datetime import datetime, timedelta, timezone
import re
from difflib import SequenceMatcher
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


def _match_text(value: str | None) -> str:
    """Normalize Thai/English task text for conservative task matching."""
    if not value:
        return ""
    x = value.lower().strip()
    x = re.sub(r"[^0-9a-zA-Zก-๙]+", " ", x)
    return " ".join(x.split())


def _task_text_score(task: Task, hint: str | None) -> float:
    """Return 0..1 lexical similarity between a reply and a task.

    v0.6.3 gives strong weight to distinctive exact tokens (PostgreSQL, LG, TSP,
    model numbers, project codes, etc.) while keeping generic status language weak.
    This lets replies such as "PostgreSQL ตรวจสอบแล้ว ใช้งานได้ปกติค่ะ" match
    the PostgreSQL task even when the sender is the owner rather than the assignee.
    """
    h = _match_text(hint)
    if not h:
        return 0.0
    target = _match_text(" ".join(x for x in [task.title, task.project or ""] if x))
    if not target:
        return 0.0
    if h in target or target in h:
        return 0.95

    ht = {w for w in h.split() if len(w) >= 2}
    tt = {w for w in target.split() if len(w) >= 2}
    shared = ht & tt

    # Distinctive technical/project tokens are very strong evidence.
    # Latin/numeric tokens such as PostgreSQL, LG, TSP, CCTV, FU-... are useful
    # discriminators even when the surrounding Thai wording differs.
    distinctive = {
        w for w in shared
        if re.search(r"[a-zA-Z0-9]", w) and len(w) >= 2
    }
    if distinctive:
        # One exact technical token is enough for a strong content match.
        return 0.86 if len(distinctive) == 1 else 0.95

    # Longer Thai words can also provide useful evidence, but less aggressively.
    long_shared = {w for w in shared if len(w) >= 5}
    overlap = len(shared) / max(1, len(ht))
    seq = SequenceMatcher(None, h, target).ratio()
    score = 0.65 * overlap + 0.35 * seq
    if long_shared:
        score = max(score, min(0.82, 0.58 + 0.08 * len(long_shared)))
    return min(1.0, score)


def choose_status_target(tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None, sender_user_id: str | None = None) -> Task | None:
    """Safely match a human reply to one open task.

    v0.6.4 matching policy:
    1) Strong content/topic evidence wins first (PostgreSQL, LG, TSP, CCTV, project/model codes).
    2) LINE userId / assignee identity is only a tie-breaker or fallback when content is weak.
    3) Identity must never override a clearly different technical topic.
    4) If still ambiguous, return None rather than changing the wrong task.
    """
    if not tasks:
        return None

    sender = normalize_name(sender_name)
    extracted_assignee = normalize_name(assignee_name)

    rows = []
    for t in tasks:
        content_score = _task_text_score(t, hint)
        identity_score = 0.0
        identity_match = False
        if sender_user_id and t.assignee_user_id == sender_user_id:
            identity_score = 0.45
            identity_match = True
        elif sender and normalize_name(t.assignee_name) == sender:
            identity_score = 0.30
            identity_match = True
        elif extracted_assignee and normalize_name(t.assignee_name) == extracted_assignee:
            identity_score = 0.20

        rows.append({
            "task": t,
            "content": content_score,
            "identity": identity_score,
            "identity_match": identity_match,
            "combined": content_score + identity_score,
        })

    # CONTENT-FIRST RULE: if the reply clearly names a topic/technology/project,
    # select by content before considering ownership. This prevents a sender's
    # other assigned task from stealing a reply such as "PostgreSQL ...".
    strong = [r for r in rows if r["content"] >= 0.75]
    if strong:
        strong.sort(key=lambda r: (r["content"], r["identity"]), reverse=True)
        best = strong[0]
        second = strong[1] if len(strong) > 1 else None
        # If one topic is clearly stronger, use it. If two are equally strong,
        # allow identity to break the tie; otherwise refuse to guess.
        if second is None:
            return best["task"]
        if best["content"] - second["content"] >= 0.10:
            return best["task"]
        if best["identity"] > second["identity"]:
            return best["task"]
        return None

    # Medium content evidence can combine with identity, but still requires a
    # meaningful separation from other candidates.
    medium = [r for r in rows if r["content"] >= 0.35]
    if medium:
        medium.sort(key=lambda r: r["combined"], reverse=True)
        best = medium[0]
        second_score = medium[1]["combined"] if len(medium) > 1 else -1.0
        if best["combined"] >= 0.58 and (best["combined"] - second_score) >= 0.12:
            return best["task"]
        return None

    # Identity-only fallback is allowed only when the sender owns exactly one
    # open task in this group. If the sender owns multiple tasks, do not guess.
    sender_owned = [
        r for r in rows
        if (sender_user_id and r["task"].assignee_user_id == sender_user_id)
        or (sender and normalize_name(r["task"].assignee_name) == sender)
    ]
    if len(sender_owned) == 1:
        return sender_owned[0]["task"]

    return None




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
        person = Person(
            canonical_name=canonical_name, display_name=canonical_name, call_name=canonical_name,
            role="EMPLOYEE", line_user_id=line_user_id or None, active=True,
        )
        db.add(person)
        db.flush()
    elif line_user_id and not person.line_user_id:
        person.line_user_id = line_user_id
    if not person.call_name:
        person.call_name = person.canonical_name
    if not person.role:
        person.role = "EMPLOYEE"
    key = normalize_name(canonical_name)
    if key and not db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key)):
        db.add(PersonAlias(person_id=person.id, alias=canonical_name, normalized_alias=key))
        db.flush()
    return person


def bind_person_identity(db: Session, canonical_name: str, line_user_id: str, alias_name: str | None = None) -> Person:
    """Bind a stable LINE userId and learn the latest display name without duplicating people."""
    raw_display = (alias_name or canonical_name or "").strip() or None
    canonical_name = clean_display_name(canonical_name) or clean_display_name(alias_name) or "ผู้รับผิดชอบ"
    person = db.scalar(select(Person).where(Person.line_user_id == line_user_id))
    if not person:
        person = db.scalar(select(Person).where(func.lower(Person.canonical_name) == canonical_name.lower()))
        if person and not person.line_user_id:
            person.line_user_id = line_user_id
        elif not person:
            person = Person(
                canonical_name=canonical_name, display_name=raw_display, call_name=canonical_name,
                role="EMPLOYEE", line_user_id=line_user_id, active=True,
            )
            db.add(person)
            db.flush()
    if raw_display:
        person.display_name = raw_display
    if not person.call_name:
        person.call_name = person.canonical_name
    if not person.role:
        person.role = "EMPLOYEE"
    person.active = True
    for alias_value in {canonical_name, clean_display_name(alias_name), raw_display}:
        key = normalize_name(alias_value)
        if key and not db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key)):
            db.add(PersonAlias(person_id=person.id, alias=str(alias_value).strip(), normalized_alias=key))
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


def update_person_profile(
    db: Session, person_id: int, *, canonical_name: str, call_name: str | None = None,
    role: str = "EMPLOYEE", active: bool = True, display_name: str | None = None,
) -> tuple[Person, int]:
    person = db.get(Person, person_id)
    if not person:
        raise ValueError("person not found")
    new_canonical = clean_display_name(canonical_name)
    if not new_canonical:
        raise ValueError("canonical name is required")
    duplicate = db.scalar(select(Person).where(
        func.lower(Person.canonical_name) == new_canonical.lower(), Person.id != person.id
    ))
    if duplicate:
        raise ValueError("canonical name is already used by another person")

    old_canonical = person.canonical_name
    old_key = normalize_name(old_canonical)
    person.canonical_name = new_canonical
    person.call_name = (call_name or new_canonical).strip()
    person.role = (role or "EMPLOYEE").strip().upper()
    person.active = bool(active)
    if display_name is not None and display_name.strip():
        person.display_name = display_name.strip()

    for alias_value in {old_canonical, new_canonical, person.display_name}:
        key = normalize_name(alias_value)
        if key:
            existing = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key))
            if not existing:
                db.add(PersonAlias(person_id=person.id, alias=str(alias_value).strip(), normalized_alias=key))
            elif existing.person_id == person.id:
                existing.alias = str(alias_value).strip()

    updated = 0
    if old_key != normalize_name(new_canonical):
        for task in db.scalars(select(Task)).all():
            if normalize_name(task.assignee_name) == old_key:
                old = task.assignee_name
                task.assignee_name = new_canonical
                if person.line_user_id and not task.assignee_user_id:
                    task.assignee_user_id = person.line_user_id
                record_task_event(
                    db, task, "ASSIGNEE_PROFILE_UPDATED", actor_name="Owner",
                    text=f"ปรับชื่อผู้รับผิดชอบจาก {old or '-'} → {new_canonical}", commit=False,
                )
                updated += 1
    db.commit()
    db.refresh(person)
    return person, updated



def merge_people(db: Session, person_a_id: int, person_b_id: int) -> tuple[Person, int]:
    """Safely merge two People Registry rows into one identity.

    Rules:
    - Never auto-merge two different bound LINE user IDs.
    - If only one row has LINE userId, that bound row survives so the stable identity is preserved.
    - For a bound + unbound duplicate, the unbound row's canonical name becomes the final
      canonical name. This matches the common flow: LINE display-name row + manually-created Thai name.
    - Aliases and existing task assignments are moved to the survivor.
    """
    if person_a_id == person_b_id:
        raise ValueError("cannot merge the same person")
    a = db.get(Person, person_a_id)
    b = db.get(Person, person_b_id)
    if not a or not b:
        raise ValueError("person not found")

    if a.line_user_id and b.line_user_id and a.line_user_id != b.line_user_id:
        raise ValueError("ไม่สามารถรวมได้ เพราะทั้งสองรายชื่อผูกกับ LINE ID คนละบัญชี")

    # Stable LINE-bound identity always survives. If neither is bound, keep person_a.
    if a.line_user_id and not b.line_user_id:
        survivor, duplicate = a, b
        final_canonical = b.canonical_name
    elif b.line_user_id and not a.line_user_id:
        survivor, duplicate = b, a
        final_canonical = a.canonical_name
    else:
        survivor, duplicate = a, b
        final_canonical = a.canonical_name

    final_canonical = clean_display_name(final_canonical) or survivor.canonical_name
    old_survivor_canonical = survivor.canonical_name
    duplicate_canonical = duplicate.canonical_name

    # Collect all names before deleting/moving aliases.
    name_values = {
        old_survivor_canonical, duplicate_canonical,
        survivor.display_name, duplicate.display_name,
        survivor.call_name, duplicate.call_name,
    }
    survivor_aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == survivor.id)).all())
    duplicate_aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == duplicate.id)).all())
    for alias in survivor_aliases + duplicate_aliases:
        name_values.add(alias.alias)

    # Free unique canonical name first if the desired canonical currently belongs to duplicate.
    duplicate.canonical_name = f"__merged__{duplicate.id}__"
    db.flush()
    survivor.canonical_name = final_canonical

    # Prefer the actual LINE display name from the bound survivor.
    if not survivor.display_name:
        survivor.display_name = duplicate.display_name
    if not survivor.call_name or normalize_name(survivor.call_name) == normalize_name(old_survivor_canonical):
        survivor.call_name = final_canonical

    # Keep the strongest role and active state.
    role_rank = {"EMPLOYEE": 1, "MANAGER": 2, "ADMIN": 3, "OWNER": 4}
    survivor_role = (survivor.role or "EMPLOYEE").upper()
    duplicate_role = (duplicate.role or "EMPLOYEE").upper()
    survivor.role = survivor_role if role_rank.get(survivor_role, 1) >= role_rank.get(duplicate_role, 1) else duplicate_role
    survivor.active = bool(survivor.active or duplicate.active)
    if not survivor.line_user_id and duplicate.line_user_id:
        survivor.line_user_id = duplicate.line_user_id

    # Re-point or de-duplicate aliases.
    existing_by_key = {
        x.normalized_alias: x
        for x in db.scalars(select(PersonAlias).where(PersonAlias.person_id == survivor.id)).all()
    }
    for alias in duplicate_aliases:
        current = existing_by_key.get(alias.normalized_alias)
        if current:
            db.delete(alias)
        else:
            alias.person_id = survivor.id
            existing_by_key[alias.normalized_alias] = alias

    # Ensure all useful identity strings are aliases of the survivor.
    for value in name_values | {final_canonical}:
        value = clean_display_name(value)
        key = normalize_name(value)
        if not key:
            continue
        existing = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key))
        if not existing:
            db.add(PersonAlias(person_id=survivor.id, alias=value, normalized_alias=key))
        elif existing.person_id in {survivor.id, duplicate.id}:
            existing.person_id = survivor.id
            existing.alias = value

    # Normalize task assignments that referred to either identity or either LINE ID.
    old_keys = {normalize_name(v) for v in name_values if normalize_name(v)}
    line_ids = {x for x in {a.line_user_id, b.line_user_id} if x}
    updated = 0
    for task in db.scalars(select(Task)).all():
        name_match = normalize_name(task.assignee_name) in old_keys
        id_match = bool(task.assignee_user_id and task.assignee_user_id in line_ids)
        if name_match or id_match:
            old_name = task.assignee_name
            changed = False
            if task.assignee_name != final_canonical:
                task.assignee_name = final_canonical
                changed = True
            if survivor.line_user_id and task.assignee_user_id != survivor.line_user_id:
                task.assignee_user_id = survivor.line_user_id
                changed = True
            if changed:
                record_task_event(
                    db, task, "ASSIGNEE_PERSON_MERGED", actor_name="Owner",
                    text=f"รวมผู้รับผิดชอบ {old_name or '-'} → {final_canonical}", commit=False,
                )
                updated += 1

    # Remove duplicate row after its aliases have been moved.
    db.delete(duplicate)
    db.commit()
    db.refresh(survivor)
    return survivor, updated


def duplicate_person_suggestions(db: Session, people: list[Person] | None = None) -> dict[int, list[int]]:
    """Suggest likely duplicate rows based on alias/display/canonical overlap.

    Suggestions are advisory only; no automatic merge is performed.
    Two rows with different bound LINE IDs are never suggested as duplicates.
    """
    people = people or list(db.scalars(select(Person)).all())
    aliases_by_person: dict[int, set[str]] = {}
    def identity_keys(value: str | None) -> set[str]:
        if not value:
            return set()
        # Keep the normal registry key and a punctuation/emoji-insensitive key.
        # Example: "Proud🤍" and "Proud" should be recognized as a likely duplicate.
        normal = normalize_name(value)
        compact = _match_text(value).replace(" ", "")
        return {k for k in {normal, compact} if k}

    for p in people:
        keys = set()
        for value in (p.canonical_name, p.display_name, p.call_name):
            keys |= identity_keys(value)
        for alias in db.scalars(select(PersonAlias).where(PersonAlias.person_id == p.id)).all():
            keys |= identity_keys(alias.alias)
        aliases_by_person[p.id] = keys

    result: dict[int, list[int]] = {p.id: [] for p in people}
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            if a.line_user_id and b.line_user_id and a.line_user_id != b.line_user_id:
                continue
            if aliases_by_person[a.id] & aliases_by_person[b.id]:
                result[a.id].append(b.id)
                result[b.id].append(a.id)
    return result

def list_people(db: Session, include_inactive: bool = True) -> list[dict]:
    q = select(Person).order_by(Person.active.desc(), Person.canonical_name.asc())
    if not include_inactive:
        q = q.where(Person.active == True)
    people = list(db.scalars(q).all())
    suggestions = duplicate_person_suggestions(db, people)
    by_id = {p.id: p for p in people}
    result = []
    for p in people:
        aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == p.id).order_by(PersonAlias.alias.asc())).all())
        result.append({
            "id": p.id,
            "canonical_name": p.canonical_name,
            "display_name": p.display_name,
            "call_name": p.call_name or p.canonical_name,
            "role": p.role or "EMPLOYEE",
            "line_user_id": p.line_user_id,
            "line_bound": bool(p.line_user_id),
            "active": bool(p.active),
            "aliases": [a.alias for a in aliases],
            "duplicate_suggestions": [
                {"id": sid, "canonical_name": by_id[sid].canonical_name, "line_bound": bool(by_id[sid].line_user_id)}
                for sid in suggestions.get(p.id, []) if sid in by_id
            ],
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
