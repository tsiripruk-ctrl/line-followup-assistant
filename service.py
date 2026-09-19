from datetime import datetime, timedelta, timezone
import re
import json
from difflib import SequenceMatcher
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, delete, or_
from sqlalchemy.orm import Session
from models import Message, Task, TaskEvent, Person, PersonAlias, SystemEvent, OutboundTaskMessage, OwnerPreference, ConversationState
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





# v0.6.46 short-lived conversation state --------------------------------------
def save_conversation_state(
    db: Session, group_id: str, user_key: str, state_type: str, payload: dict,
    *, ttl_minutes: int = 10, commit: bool = True,
) -> ConversationState:
    """Upsert one short-lived state per user/group for clarification recovery."""
    now = utcnow()
    row = db.scalar(select(ConversationState).where(
        ConversationState.group_id == group_id, ConversationState.user_key == user_key
    ))
    if row:
        row.state_type = state_type
        row.payload_json = json.dumps(payload or {}, ensure_ascii=False)
        row.expires_at = now + timedelta(minutes=max(1, int(ttl_minutes)))
        row.updated_at = now
    else:
        row = ConversationState(
            group_id=group_id, user_key=user_key, state_type=state_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            expires_at=now + timedelta(minutes=max(1, int(ttl_minutes))),
        )
        db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def get_conversation_state(db: Session, group_id: str, user_key: str) -> tuple[ConversationState | None, dict]:
    """Return a live state and parsed payload; expired/corrupt states are discarded."""
    row = db.scalar(select(ConversationState).where(
        ConversationState.group_id == group_id, ConversationState.user_key == user_key
    ))
    if not row:
        return None, {}
    if row.expires_at and row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        return None, {}
    try:
        payload = json.loads(row.payload_json or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    return row, payload


def clear_conversation_state(db: Session, group_id: str, user_key: str, *, commit: bool = True) -> int:
    result = db.execute(delete(ConversationState).where(
        ConversationState.group_id == group_id, ConversationState.user_key == user_key
    ))
    if commit:
        db.commit()
    return int(result.rowcount or 0)



def link_outbound_task_message(
    db: Session, *, line_message_id: str | None, task_id: int, group_id: str,
    message_kind: str = "TASK_REPLY", commit: bool = True,
) -> OutboundTaskMessage | None:
    """Persist the LINE message id of one assistant message that refers to one task.

    This is what makes a later LINE quote/reply an exact task identifier.  It is
    deliberately idempotent because LINE/API retries may return/reuse the same id.
    """
    if not line_message_id:
        return None
    line_message_id = str(line_message_id)
    existing = db.scalar(select(OutboundTaskMessage).where(
        OutboundTaskMessage.line_message_id == line_message_id
    ))
    if existing:
        return existing
    row = OutboundTaskMessage(
        line_message_id=line_message_id, task_id=task_id, group_id=group_id,
        message_kind=message_kind,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row





def resolve_quoted_task_context(
    db: Session, group_id: str, quoted_message_id: str, *, min_text_confidence: float = 0.85,
) -> tuple[Task | None, str, float]:
    """Resolve a quoted LINE message to one active task without recency guessing.

    Resolution order is deliberately evidence-first:
      1) assistant outbound message explicitly linked to a task,
      2) original task source message id,
      3) a task event recorded from the exact quoted human message id,
      4) semantic/topic match using the exact quoted human message text.

    The old v0.6.44 fallback that selected a merely *recent* task owned/referenced by
    the same human is intentionally excluded because it can close an unrelated task.
    """
    qid = str(quoted_message_id or "").strip()
    if not qid:
        return None, "missing_quoted_message_id", 0.0

    # 1) Strongest evidence: this is one of the assistant's task-linked messages.
    link = db.scalar(select(OutboundTaskMessage).where(
        OutboundTaskMessage.line_message_id == qid,
        OutboundTaskMessage.group_id == group_id,
    ))
    if link:
        task = db.get(Task, link.task_id)
        if task and task.group_id == group_id and task.status in OPEN_STATUSES:
            return task, "outbound_task_message", 1.0

    # 2) Original message that created the task.
    task = db.scalar(select(Task).where(
        Task.source_message_id == qid,
        Task.group_id == group_id,
        Task.status.in_(OPEN_STATUSES),
    ))
    if task:
        return task, "task_source_message", 1.0

    # 3) Exact human message already recorded in this task's timeline.
    rows = db.scalars(
        select(Task)
        .join(TaskEvent, TaskEvent.task_id == Task.id)
        .where(
            Task.group_id == group_id,
            Task.status.in_(OPEN_STATUSES),
            TaskEvent.message_id == qid,
        )
        .order_by(TaskEvent.created_at.desc(), Task.id.desc())
    ).all()
    unique = []
    seen = set()
    for candidate in rows:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        unique.append(candidate)
    if len(unique) == 1:
        return unique[0], "task_event_message_id", 1.0
    if len(unique) > 1:
        return None, "ambiguous_task_event_message_id", 0.0

    # 4) The quoted message was written by a human and exists in message history.
    # Match from the quoted text itself, never from a different recent task.
    quoted = db.scalar(select(Message).where(
        Message.line_message_id == qid,
        Message.source_id == group_id,
    ))
    quoted_text = (quoted.text or "").strip() if quoted else ""
    if quoted_text:
        target, score, ambiguous = find_task_for_explicit_query(
            db, group_id, quoted_text, sender_name=None, assignee_name=None, assignee_user_id=None
        )
        if target and target.status in OPEN_STATUSES and not ambiguous and float(score or 0.0) >= float(min_text_confidence):
            return target, "quoted_human_message_text", float(score or 0.0)
        if ambiguous:
            return None, "ambiguous_quoted_human_message_text", float(score or 0.0)
        return None, "unmatched_quoted_human_message_text", float(score or 0.0)

    return None, "unmapped_quoted_message", 0.0

def recent_explicit_query_task(
    db: Session, group_id: str, user_id: str | None, *, within_minutes: int = 30,
) -> Task | None:
    """Conservative recovery for legacy task-specific assistant replies.

    Before v0.6.44 some STATUS_QUERY/FOLLOW_UP assistant responses were sent without
    persisting their outbound LINE message id.  If the same human immediately quotes
    one of those old responses, recover only when that human has referenced exactly
    one active task in this group during the recent window.
    """
    if not user_id:
        return None
    cutoff = utcnow() - timedelta(minutes=max(1, int(within_minutes)))
    rows = db.scalars(
        select(Task)
        .join(TaskEvent, TaskEvent.task_id == Task.id)
        .where(
            Task.group_id == group_id,
            Task.status.in_(OPEN_STATUSES),
            TaskEvent.actor_user_id == user_id,
            TaskEvent.event_type.in_(["STATUS_QUERY", "FOLLOW_UP"]),
            TaskEvent.created_at >= cutoff,
        )
        .order_by(TaskEvent.created_at.desc())
    ).all()
    unique = []
    seen = set()
    for task in rows:
        if task.id in seen:
            continue
        seen.add(task.id)
        unique.append(task)
    return unique[0] if len(unique) == 1 else None
THAI_WEEKDAY_INDEX = {
    "จันทร์": 0, "อังคาร": 1, "พุธ": 2, "พฤหัส": 3, "พฤหัสบดี": 3,
    "ศุกร์": 4, "เสาร์": 5, "อาทิตย์": 6,
}

def extract_followup_commitment_at(text: str | None, *, now_local: datetime | None = None) -> datetime | None:
    """Extract an explicit future follow-up commitment from a Thai progress update.

    Returns a naive UTC datetime suitable for ``Task.next_reminder_at``.
    This is deliberately conservative: it only reacts to explicit day/date language
    such as ``วันศุกร์``, ``ศุกร์นี้``, ``พรุ่งนี้`` or a numeric Thai date.
    The reminder is scheduled for the configured start of the workday (08:30 by default)
    unless an explicit HH:MM time is present.
    """
    if not text:
        return None
    x = " ".join(str(text).replace("\n", " ").split())
    local = now_local or local_now()
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(settings.timezone))
    else:
        local = local.astimezone(ZoneInfo(settings.timezone))

    target_date = None
    if "มะรืน" in x:
        target_date = (local + timedelta(days=2)).date()
    elif "พรุ่งนี้" in x:
        target_date = (local + timedelta(days=1)).date()
    elif "วันนี้" in x:
        target_date = local.date()
    else:
        # Explicit Thai weekday. Use the next occurrence; if today is that weekday,
        # plain ``วันศุกร์`` means the next future Friday rather than immediately now.
        weekday_match = re.search(r"(?:วัน)?(จันทร์|อังคาร|พุธ|พฤหัสบดี|พฤหัส|ศุกร์|เสาร์|อาทิตย์)(?:นี้|หน้า)?", x)
        if weekday_match:
            wd = THAI_WEEKDAY_INDEX[weekday_match.group(1)]
            days = (wd - local.weekday()) % 7
            if days == 0:
                days = 7
            target_date = (local + timedelta(days=days)).date()

    # Numeric date such as 18/9, 18/09/69, 18/09/2569. This overrides weekday.
    date_match = re.search(r"(?<!\d)([0-3]?\d)[/\-]([01]?\d)(?:[/\-](\d{2,4}))?(?!\d)", x)
    if date_match:
        day, month = int(date_match.group(1)), int(date_match.group(2))
        raw_year = date_match.group(3)
        if raw_year:
            year = int(raw_year)
            if year >= 2400:
                year -= 543
            elif year < 100:
                year += 2000
        else:
            year = local.year
        try:
            candidate = datetime(year, month, day).date()
            if not raw_year and candidate < local.date():
                candidate = datetime(year + 1, month, day).date()
            target_date = candidate
        except ValueError:
            pass

    if target_date is None:
        return None

    hour = settings.followup_start_hour
    minute = settings.followup_start_minute
    # Respect an explicit time, e.g. ``วันศุกร์ 14:00`` or ``ศุกร์ 14.30 น.``
    time_match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?:\s*น\.?)?", x)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))

    target_local = datetime.combine(target_date, datetime.min.time()).replace(
        hour=hour, minute=minute, second=0, microsecond=0, tzinfo=ZoneInfo(settings.timezone)
    )
    # Never schedule outside the permitted work window. Clamp to the start of work.
    start_minutes = settings.followup_start_hour * 60 + settings.followup_start_minute
    end_minutes = settings.followup_end_hour * 60 + settings.followup_end_minute
    target_minutes = target_local.hour * 60 + target_local.minute
    if target_minutes < start_minutes:
        target_local = target_local.replace(hour=settings.followup_start_hour, minute=settings.followup_start_minute)
    elif target_minutes >= end_minutes:
        target_local = (target_local + timedelta(days=1)).replace(
            hour=settings.followup_start_hour, minute=settings.followup_start_minute
        )

    return target_local.astimezone(timezone.utc).replace(tzinfo=None)

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
    resolved_title = extraction.title or "งานติดตามจาก LINE"
    # Guard against cross-message AI extraction. If the generated title points to a
    # different operational topic than the actual source message, preserve the source
    # wording instead of creating a confidently wrong task title.
    if source_text:
        src_anchors = _topic_anchors(source_text)
        title_anchors = _topic_anchors(resolved_title)
        if src_anchors and title_anchors and not (src_anchors & title_anchors):
            resolved_title = _safe_source_excerpt(source_text, 160) or resolved_title

    t = Task(
        task_code="TEMP",
        group_id=group_id,
        source_message_id=source_message_id,
        title=resolved_title,
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



def delete_task_by_code(db: Session, code: str) -> Task | None:
    """Hard-delete one tracked task and its bot-owned child records.

    Used only by an explicit owner command. Child rows are deleted explicitly so
    behavior is consistent on both PostgreSQL and SQLite test environments.
    """
    task = get_task_by_code(db, code)
    if not task:
        return None
    # Keep a detached snapshot of the identifying fields for the confirmation.
    task_id = task.id
    db.execute(delete(OutboundTaskMessage).where(OutboundTaskMessage.task_id == task_id))
    db.execute(delete(TaskEvent).where(TaskEvent.task_id == task_id))
    db.delete(task)
    db.commit()
    return task

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


def normalize_alias_key(value: str | None) -> str:
    """Alias storage key. Preserve honorifics so มาช / พี่มาช can both be saved.

    Matching remains backward compatible with legacy normalize_name keys.
    """
    if not value:
        return ""
    return "".join(value.strip().lower().split())


def _match_text(value: str | None) -> str:
    """Normalize Thai/English task text for conservative task matching."""
    if not value:
        return ""
    x = value.lower().strip()
    x = re.sub(r"[^0-9a-zA-Zก-๙]+", " ", x)
    return " ".join(x.split())


STATUS_NOISE_TERMS = (
    "เรียบร้อยแล้ว", "เรียบร้อย", "เสร็จแล้ว", "เสร็จ", "แล้ว", "สำเร็จแล้ว", "สำเร็จ",
    "ดำเนินการแล้ว", "ดำเนินการ", "ตรวจสอบแล้ว", "ตรวจแล้ว", "อัปเดต", "update",
    "กำลังดำเนินการ", "กำลังทำ", "กำลังเช็ก", "กำลังตรวจสอบ", "รอข้อมูล", "รออนุมัติ",
    "รับทราบ", "ค่ะ", "ครับ", "คะ", "นะคะ", "นะครับ",
    # Follow-up/query noise: strip these before duplicate-task comparison.
    "ขออัปเดต", "อัปเดตหน่อย", "ช่วยตาม", "ตามเรื่อง", "ติดตาม", "ช่วยถาม",
    "ถึงไหนแล้ว", "เป็นยังไงบ้าง", "เป็นอย่างไรบ้าง", "หน่อย", "ด้วย", "เรื่อง",
)



BUSINESS_CONCEPT_TERMS = {
    # Operational phrases that describe the same business job with different verbs.
    # Keep these narrow: concept matching is only one signal and never overrides ambiguity.
    "insurance": (
        "ประกันรถ", "ประกันภัยรถ", "ประกันภัย", "ต่อประกัน", "ต่ออายุประกัน",
        "เบี้ยประกัน", "ค่าประกัน", "ชำระค่าประกัน", "จ่ายค่าประกัน",
    ),
    "vehicle_tax": ("ภาษีรถ", "ต่อภาษีรถ", "ภาษีประจำปี", "ต่อทะเบียนรถ"),
    "compulsory_insurance": ("พรบ", "พ.ร.บ", "พ.ร.บ.", "ต่อพรบ", "ต่อ พ.ร.บ."),
}


def _business_concepts(value: str | None) -> set[str]:
    compact = _match_text(value).replace(" ", "")
    if not compact:
        return set()
    out: set[str] = set()
    for concept, terms in BUSINESS_CONCEPT_TERMS.items():
        for term in terms:
            needle = _match_text(term).replace(" ", "")
            if needle and needle in compact:
                out.add(concept)
                break
    return out
ACTION_SYNONYMS = (
    ("ชำระ", "จ่าย"),
    ("โอนเงิน", "จ่าย"),
    ("ชำระเงิน", "จ่าย"),
    ("เช็ค", "ตรวจ"),
    ("เช็ก", "ตรวจ"),
    ("ตรวจสอบ", "ตรวจ"),
)


def _semantic_core(value: str | None) -> str:
    """Compact task text after removing generic status wording and normalizing common actions.

    Thai usually has no spaces between words, so token-only matching is not enough for
    short replies such as "จ่ายค่าประกันเรียบร้อย" vs "ชำระค่าประกันสัญญา".
    """
    x = _match_text(value).replace(" ", "")
    if not x:
        return ""
    for term in sorted(STATUS_NOISE_TERMS, key=len, reverse=True):
        x = x.replace(_match_text(term).replace(" ", ""), "")
    for src, dst in ACTION_SYNONYMS:
        x = x.replace(_match_text(src).replace(" ", ""), _match_text(dst).replace(" ", ""))
    return x




# Topic anchors are deliberately operational and conservative. They are used to
# prevent a status update from one job contaminating another job owned by the same
# person. This is not a classifier; it is a conflict guard.
TOPIC_ANCHOR_TERMS = {
    "gps": ("gps", "ปักหมุด", "พิกัด", "coordinate", "coordinates"),
    "meter": ("มิเตอร์", "meter"),
    "camera": ("กล้อง", "cctv", "camera"),
    "flow_account": ("flow account", "flowaccount", "tsp"),
    "purchase_order": ("เปิด po", "po", "พีโอ", "futong", "ฟู่ตง"),
    "fiber": ("สายไฟเบอร์", "ไฟเบอร์", "fiber", "fibre"),
    "insurance": ("ประกัน", "เบี้ยประกัน", "พรบ", "พ.ร.บ"),
    "postgresql": ("postgresql", "postgres", "ฐานข้อมูล"),
    "email": ("อีเมล", "email", "เมล"),
    "drawing": ("drawing", "แบบติดตั้ง", "แบบการติดตั้ง", "วาดแบบ"),
}


def _topic_anchors(value: str | None) -> set[str]:
    compact = _match_text(value).replace(" ", "")
    if not compact:
        return set()
    anchors: set[str] = set()
    for name, terms in TOPIC_ANCHOR_TERMS.items():
        for term in terms:
            needle = _match_text(term).replace(" ", "")
            if needle and needle in compact:
                anchors.add(name)
                break
    # Exact latin/numeric tokens are useful anchors for product/system/project names.
    normalized = _match_text(value)
    for tok in normalized.split():
        if re.search(r"[a-zA-Z0-9]", tok) and len(tok) >= 3 and tok not in {"update", "status"}:
            anchors.add(f"token:{tok}")
    return anchors


def _created_task_text(db: Session, task: Task) -> str:
    try:
        ev = db.scalar(
            select(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.event_type == "CREATED")
            .order_by(TaskEvent.id.asc())
            .limit(1)
        )
        return (ev.text or "").strip() if ev else ""
    except Exception:
        return ""


def _base_task_text(db: Session, task: Task) -> str:
    """Stable task context: title/project plus the original assignment only.

    Do not include arbitrary later STATUS_REPLY/TASK_MEMORY events here; those may be
    exactly the contaminated data we are trying to detect.
    """
    return " ".join(x for x in [task.title or "", task.project or "", _created_task_text(db, task)] if x)


def _is_generic_status_only(value: str | None) -> bool:
    """True only for short replies with no independent topic information.

    Generic replies may safely use identity/quoted context. Substantive updates such
    as 'ยังไม่ได้ปักหมุด GPS...' must never be attached by assignee identity alone.
    """
    x = _match_text(value)
    if not x:
        return True
    if _topic_anchors(value):
        return False
    core = _semantic_core(value)
    generic_cores = {
        "", "ส่ง", "จ่าย", "ตรวจ", "ทำ", "ปิด", "เปิด", "โอเค", "ok", "done",
        "ยังไม่", "ยังไม่ได้", "รอ", "กำลังทำ", "กำลังตรวจ",
    }
    if core in generic_cores:
        return True
    return len(core) <= 8


def _memory_relevant_to_task(db: Session, task: Task, memory_text: str | None) -> bool:
    """Reject task-memory that contains a clearly different operational topic."""
    if not memory_text:
        return False
    base = _base_task_text(db, task)
    base_anchors = _topic_anchors(base)
    mem_anchors = _topic_anchors(memory_text)
    if base_anchors and mem_anchors and not (base_anchors & mem_anchors):
        return False
    # When both sides have no known anchors, require at least modest lexical relation
    # unless the reply is generic status wording.
    if not base_anchors and not mem_anchors and not _is_generic_status_only(memory_text):
        proxy = type("TaskProxy", (), {"title": base, "project": None})()
        if _task_text_score(proxy, memory_text) < 0.25:
            return False
    return True


def _safe_source_excerpt(text: str | None, max_len: int = 110) -> str:
    if not text:
        return ""
    x = " ".join(str(text).replace("\n", " ").split()).strip()
    # Trim common follow-up filler while keeping the actual assignment wording.
    for phrase in ("มาตอนนี้เค้าอัพเดทอะไรยังไงบ้าง", "ตอนนี้เค้าอัพเดทอะไรยังไงบ้าง", "เห็นเค้าตามเรื่องกันอยู่"):
        x = x.replace(phrase, "").strip(" ,-:|.")
    if len(x) > max_len:
        x = x[: max_len - 1].rstrip() + "…"
    return x


def task_reference_label(db: Session, task: Task) -> str:
    """Prefer the original assignment when the AI-generated title conflicts with it."""
    source = _created_task_text(db, task)
    if not source:
        return task.title
    title_anchors = _topic_anchors(task.title)
    source_anchors = _topic_anchors(source)
    if title_anchors and source_anchors and not (title_anchors & source_anchors):
        return _safe_source_excerpt(source) or task.title
    return task.title

def _compact_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        # A meaningful Thai core contained in the task title is strong evidence.
        shorter = min(len(a), len(b))
        return 0.92 if shorter >= 5 else 0.72
    return SequenceMatcher(None, a, b).ratio()


def resolve_assignee_from_text(db: Session, text: str | None) -> Person | None:
    """Resolve an assignee from explicit assignment language using People Registry aliases.

    This deliberately does NOT treat every occurrence of an alias as an assignment.
    It is designed for ambiguous Thai names such as "ต้อง":
      - "ให้ต้องเป็นผู้รับผิดชอบ" -> person ต้อง
      - "งานนี้ต้องส่งวันนี้"      -> no person match
    """
    raw = " ".join((text or "").strip().split())
    if not raw:
        return None
    compact = normalize_alias_key(raw)
    aliases = list(db.scalars(select(PersonAlias)).all())
    # Prefer longer aliases first so "พี่ต้อง" wins over "ต้อง".
    aliases.sort(key=lambda a: len(normalize_alias_key(a.alias)), reverse=True)
    for row in aliases:
        alias = normalize_alias_key(row.alias)
        if not alias or len(alias) < 2:
            continue
        patterns = (
            f"ให้{alias}เป็นผู้รับผิดชอบ",
            f"มอบหมายให้{alias}",
            f"มอบให้{alias}",
            f"ผู้รับผิดชอบ{alias}",
            f"{alias}เป็นผู้รับผิดชอบ",
            f"ฝาก{alias}ช่วย",
            f"ฝาก{alias}",
            f"{alias}ช่วย",
            f"{alias}รับผิดชอบ",
            f"{alias}รับเรื่อง",
        )
        if any(pat in compact for pat in patterns):
            person = db.get(Person, row.person_id)
            if person and person.active:
                return person
    return None

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

    # Thai semantic-core comparison strips generic status words and normalizes
    # common action synonyms (e.g. ชำระ -> จ่าย). This makes concise updates
    # such as "จ่ายค่าประกันเรียบร้อย" match "ชำระค่าประกันสัญญา" reliably.
    h_core = _semantic_core(hint)
    t_core = _semantic_core(" ".join(x for x in [task.title, task.project or ""] if x))
    core_score = _compact_similarity(h_core, t_core)
    if core_score >= 0.90:
        return 0.91

    # Business-concept bridge: operational replies often use a different verb from
    # the task title. Example: "จ่ายค่าประกันเรียบร้อย" and "ต่อประกันรถ" are
    # the same insurance job even though their literal strings are not close.
    # A shared concept is strong enough only to produce a candidate; if multiple
    # open tasks share the concept, choose_status_target() will refuse to guess.
    h_concepts = _business_concepts(hint)
    t_concepts = _business_concepts(" ".join(x for x in [task.title, task.project or ""] if x))
    if h_concepts & t_concepts:
        return max(0.80, core_score)

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
    # Sequence similarity over the compact semantic core is a conservative
    # fallback for Thai phrases that differ only by action wording/status suffixes.
    if core_score >= 0.62 and min(len(h_core), len(t_core)) >= 4:
        score = max(score, min(0.84, 0.55 + 0.30 * core_score))
    return min(1.0, score)


def rank_status_targets(tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None, sender_user_id: str | None = None) -> list[dict]:
    """Return candidate tasks with transparent content/identity scores for review."""
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
    return sorted(rows, key=lambda r: (r["content"], r["combined"], r["identity"]), reverse=True)


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
    rows = rank_status_targets(tasks, sender_name, assignee_name, hint, sender_user_id)

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





def _task_business_text(db: Session, task: Task) -> str:
    """Return title/project plus recent event text for deterministic business matching."""
    parts = [task.title or "", task.project or ""]
    try:
        event_texts = list(db.scalars(
            select(TaskEvent.text)
            .where(TaskEvent.task_id == task.id, TaskEvent.text.is_not(None))
            .order_by(TaskEvent.id.desc())
            .limit(8)
        ).all())
        parts.extend(x for x in event_texts if x)
    except Exception:
        # History enrichment must never break status matching.
        pass
    return " ".join(x for x in parts if x)


def _is_vehicle_insurance_text(value: str | None) -> bool:
    compact = _match_text(value).replace(" ", "")
    if not compact:
        return False
    insurance = any(k in compact for k in ("ประกัน", "เบี้ยประกัน", "พรบ", "พ.ร.บ"))
    vehicle = any(k in compact for k in ("รถ", "รถยนต์", "รถกระบะ", "vios", "fortuner", "revo", "dmax", "d-max"))
    return insurance and vehicle


def _business_status_target(db: Session, tasks: list[Task], hint: str | None, sender_user_id: str | None = None) -> tuple[Task | None, str | None]:
    """Deterministically resolve short operational updates before generic similarity.

    This is intentionally conservative but understands business concepts. For example,
    "จ่ายค่าประกันเรียบร้อย" can close the single open task "ต่อประกันรถ" even
    though the verbs differ. It never chooses between two distinct vehicle-insurance
    tasks because that could close the wrong vehicle/project.
    """
    msg_concepts = _business_concepts(hint)
    if not msg_concepts or not tasks:
        return None, None

    candidates: list[tuple[Task, set[str], str]] = []
    for t in tasks:
        business_text = _task_business_text(db, t)
        concepts = _business_concepts(business_text)
        if msg_concepts & concepts:
            candidates.append((t, concepts, business_text))

    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0][0], "single_business_concept"

    # Prefer a task bound to the sender only when that narrows to exactly one.
    if sender_user_id:
        owned = [c for c in candidates if c[0].assignee_user_id == sender_user_id]
        if len(owned) == 1:
            return owned[0][0], "single_business_concept_owned"
        if len(owned) > 1:
            candidates = owned

    # Insurance-specific bridge. A generic payment update such as
    # "จ่ายค่าประกันเรียบร้อย" is often the completion signal for "ต่อประกันรถ".
    # Select it only when there is exactly one open vehicle-insurance task.
    if "insurance" in msg_concepts:
        vehicle = [c for c in candidates if _is_vehicle_insurance_text(c[2])]
        if len(vehicle) == 1:
            return vehicle[0][0], "unique_vehicle_insurance"
        if len(vehicle) > 1:
            # Two different open vehicle-insurance jobs are genuinely ambiguous.
            return None, "ambiguous_vehicle_insurance"

    # If all candidates are operational duplicates for the same project/assignee and
    # same concept family, choose the newest record. This handles accidental duplicate
    # task creation without letting a different project steal the update.
    projects = {normalize_name(c[0].project) for c in candidates if c[0].project}
    assignees = {c[0].assignee_user_id or normalize_name(c[0].assignee_name) for c in candidates if (c[0].assignee_user_id or c[0].assignee_name)}
    concept_sets = {tuple(sorted(c[1])) for c in candidates}
    if len(projects) <= 1 and len(assignees) <= 1 and len(concept_sets) == 1:
        newest = max((c[0] for c in candidates), key=lambda t: (t.created_at, t.id))
        return newest, "duplicate_business_task_newest"

    return None, "ambiguous_business_concept"



def recent_reminder_context_target(
    db: Session,
    group_id: str,
    sender_user_id: str | None,
    sender_name: str | None,
    text: str | None,
    max_minutes: int = 180,
) -> Task | None:
    """Resolve a human update against a recently reminded task without guessing.

    Humans often answer a reminder naturally without using LINE's quote feature.
    We therefore look at recent assistant reminder messages in the same group, but
    only use this continuity signal when it is safe:
      * prefer tasks explicitly assigned to the sender's LINE user id;
      * a generic status reply may bind only when exactly one recent task remains;
      * a substantive update still needs topic/content evidence and a clear margin.

    This gives the bot conversational continuity while preventing one employee's
    unrelated update from contaminating another open task.
    """
    if not group_id:
        return None
    cutoff = datetime.utcnow() - timedelta(minutes=max_minutes)
    try:
        sent = list(db.scalars(
            select(OutboundTaskMessage)
            .where(
                OutboundTaskMessage.group_id == group_id,
                OutboundTaskMessage.created_at >= cutoff,
            )
            .order_by(OutboundTaskMessage.created_at.desc())
            .limit(30)
        ).all())
    except Exception:
        return None

    seen: set[int] = set()
    candidates: list[Task] = []
    for row in sent:
        if row.task_id in seen:
            continue
        t = db.get(Task, row.task_id)
        if not t or t.status in {"COMPLETED", "CANCELLED"}:
            continue
        seen.add(t.id)
        candidates.append(t)

    if not candidates:
        return None

    # Sender identity is a narrowing signal, never a substitute for topic evidence.
    if sender_user_id:
        owned = [t for t in candidates if t.assignee_user_id == sender_user_id]
        if owned:
            candidates = owned
    elif sender_name:
        ns = normalize_name(sender_name)
        owned = [t for t in candidates if normalize_name(t.assignee_name) == ns]
        if owned:
            candidates = owned

    if _is_generic_status_only(text):
        return candidates[0] if len(candidates) == 1 else None

    scored: list[tuple[float, Task]] = []
    for t in candidates:
        base = _base_task_text(db, t)
        proxy = type("TaskProxy", (), {"title": base, "project": None})()
        score = _task_text_score(proxy, text)
        # A cross-topic memory must never be rescued by recency alone.
        if not _memory_relevant_to_task(db, t, text):
            score = 0.0
        scored.append((score, t))
    scored.sort(key=lambda x: (x[0], x[1].id), reverse=True)
    if not scored or scored[0][0] < 0.45:
        return None
    if len(scored) > 1 and scored[1][0] >= 0.35 and (scored[0][0] - scored[1][0]) < 0.15:
        return None
    return scored[0][1]


def rank_status_targets_with_history(db: Session, tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None, sender_user_id: str | None = None) -> list[dict]:
    """Rank tasks using title/project plus recent task-event text.

    This recovers cases where AI shortened a task title and the useful wording only
    exists in the original CREATED event, e.g. task title "ต่อประกันรถ" while the
    original assignment mentioned "ชำระ/จ่ายค่าประกันรถ".
    """
    sender = normalize_name(sender_name)
    extracted_assignee = normalize_name(assignee_name)
    rows = []
    for t in tasks:
        title_text = " ".join(x for x in [t.title, t.project or ""] if x)
        title_score = _task_text_score(t, hint)
        event_texts = list(db.scalars(
            select(TaskEvent.text)
            .where(
                TaskEvent.task_id == t.id,
                TaskEvent.text.is_not(None),
                TaskEvent.event_type.in_(["CREATED", "QUOTED_STATUS_REPLY", "QUOTED_COMMENT_REPLY"]),
            )
            .order_by(TaskEvent.id.desc())
            .limit(8)
        ).all())
        history_text = " ".join(x for x in event_texts if x)
        history_score = 0.0
        if history_text:
            proxy = type("TaskProxy", (), {"title": history_text, "project": None})()
            history_score = _task_text_score(proxy, hint)
        content_score = max(title_score, min(0.94, history_score))

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
            "title_content": title_score,
            "history_content": history_score,
            "identity": identity_score,
            "identity_match": identity_match,
            "combined": content_score + identity_score,
        })
    return sorted(rows, key=lambda r: (r["content"], r["combined"], r["identity"], r["task"].id), reverse=True)


def choose_status_target_with_history(db: Session, tasks: list[Task], sender_name: str | None, assignee_name: str | None, hint: str | None, sender_user_id: str | None = None) -> Task | None:
    """Safer resolver that also considers the task's original conversation history."""
    if not tasks:
        return None
    rows = rank_status_targets_with_history(db, tasks, sender_name, assignee_name, hint, sender_user_id)

    # Business-first deterministic resolver for short operational updates. It runs
    # before generic threshold/tie logic, so a single open "ต่อประกันรถ" can be
    # resolved from "จ่ายค่าประกันเรียบร้อย" without being blocked by unrelated
    # lexical ties. Ambiguous business cases still return to the safe generic logic.
    business_target, business_reason = _business_status_target(db, tasks, hint, sender_user_id)
    if business_target:
        print("business status target:", business_target.task_code, business_target.title, business_reason)
        return business_target
    if business_reason:
        print("business status unresolved:", business_reason, repr(hint))

    strong = [r for r in rows if r["content"] >= 0.75]
    if strong:
        best = strong[0]
        second = strong[1] if len(strong) > 1 else None
        if second is None:
            return best["task"]
        if best["content"] - second["content"] >= 0.10:
            return best["task"]
        if best["identity"] > second["identity"]:
            return best["task"]
        return None

    medium = [r for r in rows if r["content"] >= 0.35]
    if medium:
        best = medium[0]
        second_score = medium[1]["combined"] if len(medium) > 1 else -1.0
        if best["combined"] >= 0.58 and (best["combined"] - second_score) >= 0.12:
            return best["task"]
        return None

    # Identity-only fallback is intentionally limited to generic status replies.
    # A substantive update (e.g. GPS/meter/camera details) must have content evidence
    # or an exact quote-reply link; otherwise it is safer to leave the task unchanged.
    if _is_generic_status_only(hint):
        sender = normalize_name(sender_name)
        sender_owned = [
            r for r in rows
            if (sender_user_id and r["task"].assignee_user_id == sender_user_id)
            or (sender and normalize_name(r["task"].assignee_name) == sender)
        ]
        if len(sender_owned) == 1:
            return sender_owned[0]["task"]
    return None



def latest_status_reply(db: Session, task: Task) -> TaskEvent | None:
    """Return the latest human progress/status reply for a task."""
    return db.scalar(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task.id,
            TaskEvent.event_type.in_(["STATUS_REPLY", "QUOTED_STATUS_REPLY", "QUOTED_COMMENT_REPLY", "TASK_MEMORY_UPDATED"]),
            TaskEvent.text.is_not(None),
        )
        .order_by(TaskEvent.id.desc())
        .limit(1)
    )


def summarize_progress_update(text: str | None, max_len: int = 180) -> str:
    """Create a concise Thai follow-up memory from a human progress update.

    This is intentionally deterministic so reminders retain context even when the AI
    service is unavailable. It removes common filler while preserving completed steps
    and what is still being waited on.
    """
    if not text:
        return ""
    x = " ".join(str(text).replace("\n", " ").split()).strip()
    # Trim conversational filler without changing operational meaning.
    for prefix in ("อัปเดตค่ะ", "อัปเดตครับ", "แจ้งค่ะ", "แจ้งครับ"):
        if x.startswith(prefix):
            x = x[len(prefix):].strip(" :-")
    # High-value operational patterns: preserve the completed step and the current dependency.
    low = x.lower()
    if ("ส่งเอกสาร" in x and ("เมลตอบ" in x or "อีเมลตอบ" in x)
            and ("ไม่ตรงประเด็น" in x or "ไม่ตรง" in x)
            and ("รอเค้าตอบ" in x or "รอเขาตอบ" in x or "รอตอบ" in x)):
        return "ส่งเอกสารแล้ว ได้รับอีเมลตอบกลับแต่ยังไม่ตรงประเด็น และกำลังรอเจ้าหน้าที่ตอบกลับ"
    if (("เปิด po" in low or "เปิด พีโอ" in x)
            and ("เรียบร้อย" in x or "แล้ว" in x)
            and ("เซลล์" in x or "sales" in low)
            and ("ไม่ตอบรับ" in x or "ยังไม่ตอบ" in x)):
        return "เปิด PO เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับและกำลังรอติดตามอีกครั้ง"

    # Normalize a few phrases so the next reminder sounds like continuity rather than a quote dump.
    replacements = [
        ("เดี๋ยวจะติดตามอีกที", "กำลังรอติดตามอีกครั้ง"),
        ("เดี๋ยวติดตามอีกที", "กำลังรอติดตามอีกครั้ง"),
        ("รอเค้าตอบเมลกลับมา", "กำลังรอเจ้าหน้าที่ตอบกลับ"),
        ("รอเขาตอบเมลกลับมา", "กำลังรอเจ้าหน้าที่ตอบกลับ"),
        ("รอเค้าตอบกลับ", "กำลังรอการตอบกลับ"),
        ("รอเขาตอบกลับ", "กำลังรอการตอบกลับ"),
    ]
    for a,b in replacements:
        x=x.replace(a,b)
    if len(x) > max_len:
        x = x[:max_len-1].rstrip() + "…"
    return x



def _progress_clause(text: str | None, markers: tuple[str, ...], max_len: int = 140) -> str:
    """Extract one operational clause containing one of ``markers``.

    This is intentionally conservative and used only after a message has already been
    matched to an exact task. It does not perform task matching itself.
    """
    if not text:
        return ""
    clean = " ".join(str(text).replace("\n", " ").split()).strip()
    if not clean:
        return ""
    # Thai chat often separates steps with commas / แต่ / แล้ว / ส่วน. Keep enough
    # context around the matching clause without manufacturing facts.
    chunks = [c.strip(" ,;:-") for c in re.split(r"[.!?\n]|(?:\s+แต่|\s+ส่วน|\s+จากนั้น|\s+แล้วก็)\s*", clean) if c.strip(" ,;:-")]
    for chunk in chunks:
        low = chunk.lower()
        # Thai substring safety: the waiting marker "รอ" must not fire merely
        # because it appears inside the completion word "เรียบร้อย".
        marker_text = low.replace("เรียบร้อย", "")
        if any(m.lower() in marker_text for m in markers):
            if len(chunk) > max_len:
                return chunk[:max_len - 1].rstrip() + "…"
            return chunk
    return ""


def derive_progress_snapshot(text: str | None, status: str | None = None) -> dict[str, str]:
    """Build a task-local progress snapshot from a trusted matched update.

    The snapshot is deliberately descriptive rather than a guessed percentage. It
    captures what the assignee most recently said, what the work is waiting on, and
    an explicit next action/checkpoint when one is present.
    """
    summary = summarize_progress_update(text)
    if not summary:
        return {"summary": "", "waiting_on": "", "next_action": ""}

    waiting = _progress_clause(
        text,
        (
            "รอ", "ยังไม่ตอบ", "ยังไม่ได้", "ไม่ตอบรับ", "ติดปัญหา", "ติดขัด",
            "รออนุมัติ", "รอข้อมูล", "รอเจ้าหน้าที่", "รอเซลล์", "รอทาง",
        ),
    )
    next_action = _progress_clause(
        text,
        (
            "เดี๋ยว", "จะติดตาม", "จะดำเนิน", "จะเข้า", "จะส่ง", "จะตรวจ", "จะเช็ก",
            "นัด", "ต้อง", "ขั้นต่อไป", "พรุ่งนี้", "มะรืน", "วันจันทร์", "วันอังคาร",
            "วันพุธ", "วันพฤหัส", "วันศุกร์", "วันเสาร์", "วันอาทิตย์",
        ),
    )

    # A WAITING task can legitimately have no explicit dependency noun. Preserve the
    # human wording rather than inventing one.
    if status == "WAITING" and not waiting:
        waiting = summary if any(k in summary for k in ("รอ", "ยังไม่", "ติด")) else ""

    return {"summary": summary, "waiting_on": waiting, "next_action": next_action}


def update_task_progress_snapshot(
    db: Session,
    task: Task,
    text: str | None,
    *,
    actor_name: str | None = None,
    actor_user_id: str | None = None,
    message_id: str | None = None,
    confidence: float | None = None,
    commit: bool = False,
) -> dict[str, str]:
    """Replace the task's latest progress snapshot with a trusted matched update.

    Important: callers must resolve the task first. This function never searches for
    a task and therefore cannot mix context between tasks by itself.
    """
    snap = derive_progress_snapshot(text, task.status)
    if not snap["summary"]:
        return snap
    task.progress_summary = snap["summary"]
    task.waiting_on = snap["waiting_on"] or None
    task.next_action = snap["next_action"] or None
    task.last_progress_at = utcnow()
    record_task_event(
        db, task, "PROGRESS_SNAPSHOT_UPDATED",
        actor_name=actor_name, actor_user_id=actor_user_id,
        text=snap["summary"], old_status=task.status, new_status=task.status,
        message_id=message_id, confidence=confidence, commit=False,
    )
    if commit:
        db.commit()
        db.refresh(task)
    return snap


def task_progress_context(task: Task) -> str:
    """Human-readable current progress for dashboard / owner summaries."""
    parts: list[str] = []
    if task.progress_summary:
        parts.append(f"ล่าสุด: {task.progress_summary}")
    if task.waiting_on and task.waiting_on != task.progress_summary:
        parts.append(f"กำลังรอ: {task.waiting_on}")
    if task.next_action and task.next_action not in (task.progress_summary, task.waiting_on):
        parts.append(f"ขั้นตอนถัดไป: {task.next_action}")
    return "\n".join(parts)


def _clip_message_piece(value: str | None, max_len: int = 92) -> str:
    if not value:
        return ""
    x = " ".join(str(value).replace("\n", " ").split()).strip(" ,-:|.")
    if len(x) > max_len:
        return x[: max_len - 1].rstrip() + "…"
    return x


def _normalize_waiting_phrase(value: str | None) -> str:
    x = _clip_message_piece(value, 80)
    if not x:
        return ""
    # Keep the operational noun, not a nested phrase such as "รอ (รอเซลล์...)".
    x = re.sub(r"^(?:ตอนนี้)?\s*(?:กำลัง)?\s*รอ\s*", "", x).strip()
    return x


def _appointment_like(value: str | None) -> bool:
    x = (value or "").lower()
    return any(k in x for k in (
        "นัด", "พรุ่งนี้", "มะรืน", "วันจันทร์", "วันอังคาร", "วันพุธ",
        "วันพฤหัส", "วันศุกร์", "วันเสาร์", "วันอาทิตย์",
    )) or bool(re.search(r"\b\d{1,2}[/:]\d{1,2}(?:[/:]\d{2,4})?\b", x))



def normalize_followup_tone_instruction(value: str | None) -> str:
    """Normalize an owner-entered tone instruction without changing its meaning.

    Removes invisible characters and obvious accidental repeated-letter noise such as
    ``sssธรรมชาติ`` while preserving legitimate English/Thai style instructions.
    """
    x = str(value or "")
    x = x.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    # Remove obvious repeated-key noise (e.g. sss, aaa) when typed as a standalone
    # token or directly before Thai text. Do not remove normal English words.
    x = re.sub(r"(?<![A-Za-z])([A-Za-z])\1{1,5}(?=[ก-๙])", "", x)
    x = re.sub(r"\b([A-Za-z])\1{1,5}\b", "", x)
    x = re.sub(r"[ \t]+", " ", x)
    x = re.sub(r" ?\n ?", " ", x)
    # Repair common Thai spacing introduced by accidental keyboard noise removal.
    x = re.sub(r"เป็น\s+ธรรมชาติ", "เป็นธรรมชาติ", x)
    x = re.sub(r"เหมือน\s+เลขานุการ", "เหมือนเลขานุการ", x)
    return x.strip(" :,-")


def infer_followup_tone(value: str | None) -> tuple[str, str, dict]:
    """Map a free-form owner instruction to a runtime tone without losing the text.

    Returns (tone_code, cleaned_instruction, derived_policy_changes).
    The original meaning is preserved in ``custom_instruction`` and remains visible
    in owner diagnostics instead of being collapsed to a preset label.
    """
    desc = normalize_followup_tone_instruction(value)
    low = desc.lower()
    if any(k in low for k in ("เลขานุการ", "ธรรมชาติ", "เหมือนคน", "ไม่เหมือน bot", "ไม่เหมือนบอต")):
        tone = "secretary_natural"
    elif "นุ่ม" in low or "ไม่กดดัน" in low:
        tone = "soft"
    elif "ตรง" in low:
        tone = "direct"
    elif "สั้น" in low or "กระชับ" in low:
        tone = "concise"
    else:
        tone = "friendly_professional"

    changes: dict = {}
    if "สั้น" in low or "กระชับ" in low:
        changes.update({"max_lines": 2, "max_chars": 160})
    if "ไม่เกิน 1 บรรทัด" in low or "หนึ่งบรรทัด" in low:
        changes.update({"max_lines": 1, "max_chars": min(changes.get("max_chars", 200), 160)})
    return tone, desc, changes


DEFAULT_FOLLOWUP_POLICY = {
    "tone": "friendly_professional",
    "max_lines": 2,
    "max_chars": 200,
    "avoid_phrases": [],
    "overdue_style": "ask_expected_completion",
    "waiting_style": "specific_dependency",
    "custom_instruction": "",
    # Owner-editable state-specific questions. These affect wording only, never Task state.
    "state_questions": {},
}


def get_followup_policy(db: Session) -> dict:
    """Return owner-controlled follow-up language policy with safe defaults."""
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == "followup_policy"))
    policy = dict(DEFAULT_FOLLOWUP_POLICY)
    if row and row.value:
        try:
            loaded = json.loads(row.value)
            if isinstance(loaded, dict):
                policy.update({k: v for k, v in loaded.items() if k in policy})
        except Exception:
            pass
    try:
        policy["max_lines"] = max(1, min(4, int(policy.get("max_lines") or 2)))
    except Exception:
        policy["max_lines"] = 2
    try:
        policy["max_chars"] = max(80, min(400, int(policy.get("max_chars") or 200)))
    except Exception:
        policy["max_chars"] = 200
    if not isinstance(policy.get("avoid_phrases"), list):
        policy["avoid_phrases"] = []
    policy["avoid_phrases"] = [str(x).strip() for x in policy["avoid_phrases"] if str(x).strip()][:30]
    allowed_states = {"overdue", "blocked", "waiting_response", "waiting_document", "waiting_approval", "waiting_goods", "appointment", "in_progress", "general"}
    raw_questions = policy.get("state_questions")
    if not isinstance(raw_questions, dict):
        raw_questions = {}
    policy["state_questions"] = {
        str(k): str(v).strip()[:240]
        for k, v in raw_questions.items()
        if str(k) in allowed_states and str(v).strip()
    }
    return policy


def save_followup_policy(db: Session, policy: dict) -> dict:
    clean = dict(DEFAULT_FOLLOWUP_POLICY)
    clean.update({k: v for k, v in (policy or {}).items() if k in clean})
    # Reuse the validator/normalizer through a temporary serialization round.
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == "followup_policy"))
    payload = json.dumps(clean, ensure_ascii=False)
    if row:
        row.value = payload
        row.updated_at = utcnow()
    else:
        row = OwnerPreference(key="followup_policy", value=payload)
        db.add(row)
    db.commit()
    return get_followup_policy(db)


def patch_followup_policy(db: Session, **changes) -> dict:
    policy = get_followup_policy(db)
    policy.update({k: v for k, v in changes.items() if k in DEFAULT_FOLLOWUP_POLICY})
    return save_followup_policy(db, policy)


def reset_followup_policy(db: Session) -> dict:
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == "followup_policy"))
    if row:
        db.delete(row)
        db.commit()
    return dict(DEFAULT_FOLLOWUP_POLICY)


def apply_followup_policy(db: Session, text: str) -> str:
    """Apply owner language controls without changing task-state semantics."""
    policy = get_followup_policy(db)
    out = str(text or "").strip()
    tone = str(policy.get("tone") or "friendly_professional")
    instruction = normalize_followup_tone_instruction(policy.get("custom_instruction"))
    instruction_low = instruction.lower()

    # Custom instructions are behavioral traits, not merely a display label.
    # A natural-secretary request therefore affects the generated wording while
    # keeping task matching/state transitions completely untouched.
    natural_secretary = tone == "secretary_natural" or any(
        k in instruction_low for k in ("เลขานุการ", "ธรรมชาติ", "เหมือนคน")
    )
    soft_trait = tone == "soft" or "นุ่ม" in instruction_low or "ไม่กดดัน" in instruction_low
    direct_trait = tone == "direct" or "ตรงประเด็น" in instruction_low
    concise_trait = tone == "concise" or "กระชับ" in instruction_low or "สั้น" in instruction_low

    if natural_secretary:
        # Remove UI/report-like labels and prefer conversational transitions.
        out = out.replace("ล่าสุด: ", "ล่าสุด ")
        out = out.replace("วันนี้ถึงช่วงที่นัดไว้ตามอัปเดตล่าสุดแล้วค่ะ", "วันนี้ถึงวันที่นัดไว้แล้วค่ะ")
        out = out.replace("ตอนนี้สิ่งที่รออยู่ขยับไปถึงไหนแล้วคะ", "ตอนนี้เรื่องที่รออยู่ไปถึงไหนแล้วคะ")
    if soft_trait:
        out = out.replace("เลยกำหนดแล้วค่ะ", "เห็นว่ากำหนดเดิมผ่านแล้วนะคะ")
        out = out.replace("ตอนนี้คาดว่าจะเรียบร้อยได้ประมาณเมื่อไหร่คะ", "พอจะประเมินได้ไหมคะว่าน่าจะเรียบร้อยประมาณเมื่อไหร่")
    if direct_trait:
        out = out.replace("ล่าสุด: ", "")
        out = out.replace("วันนี้ถึงช่วงที่นัดไว้ตามอัปเดตล่าสุดแล้วค่ะ", "วันนี้ถึงวันที่นัดไว้แล้วค่ะ")
    if concise_trait:
        out = out.replace(" วันนี้ถึงช่วงที่นัดไว้ตามอัปเดตล่าสุดแล้วค่ะ", " วันนี้ถึงวันที่นัดไว้แล้วค่ะ")

    for phrase in policy.get("avoid_phrases") or []:
        if phrase and phrase in out:
            out = out.replace(phrase, "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip(" \n-,:;")

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    max_lines = int(policy.get("max_lines") or 2)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    out = "\n".join(lines)
    max_chars = int(policy.get("max_chars") or 200)
    if len(out) > max_chars:
        clipped = out[:max_chars - 1].rstrip()
        # Prefer clipping at a whitespace boundary so Thai/English mixed text stays readable.
        if " " in clipped[-30:]:
            clipped = clipped.rsplit(" ", 1)[0]
        out = clipped.rstrip(" ,:;-_") + "…"
    return out

STATE_QUESTION_LABELS = {
    "overdue": "งานเลยกำหนด",
    "blocked": "งานติดปัญหา",
    "waiting_response": "งานรอการตอบกลับ",
    "waiting_document": "งานรอเอกสาร",
    "waiting_approval": "งานรออนุมัติ",
    "waiting_goods": "งานรอของ/สินค้า",
    "appointment": "งานถึงวันนัด",
    "in_progress": "งานกำลังดำเนินการ",
    "general": "งานทั่วไป",
}

_STATE_RULE_ALIASES = (
    ("waiting_response", ("รอคนตอบ", "รอการตอบกลับ", "รอตอบกลับ", "รอเจ้าหน้าที่ตอบ", "รอเซลล์ตอบ")),
    ("waiting_document", ("รอเอกสาร", "รอหนังสือ", "รอใบตรวจรับ")),
    ("waiting_approval", ("รออนุมัติ", "รออนุญาต", "รอเซ็น", "รอลงนาม")),
    ("waiting_goods", ("รอของ", "รอสินค้า", "รออุปกรณ์")),
    ("appointment", ("ถึงวันนัด", "วันนัด", "ถึงวันที่นัด", "ถึงกำหนดนัด")),
    ("overdue", ("เลยกำหนด", "เกินกำหนด")),
    ("blocked", ("ติดปัญหา", "มีปัญหา", "ติดขัด", "แก้ไม่ได้")),
    ("in_progress", ("กำลังดำเนินการ", "กำลังทำ", "อยู่ระหว่างดำเนินการ")),
)


def _normalize_owner_question_text(state_key: str, instruction: str) -> str:
    """Turn a natural owner instruction into a short Thai question.

    The owner may specify semantics ("ถามวันที่คาดว่าจะเสร็จ") instead of exact
    copy. Common semantics are normalized; quoted/exact questions are preserved.
    """
    x = " ".join((instruction or "").strip().strip('"\'“”').split())
    x = re.sub(r"^(?:ว่า|เรื่อง)\s*", "", x).strip()
    low = x.lower()
    if not x:
        return ""
    if state_key == "overdue" and any(k in low for k in ("วันที่คาดว่าจะเสร็จ", "คาดว่าจะเสร็จ", "เสร็จเมื่อไหร่", "เสร็จวันไหน", "เมื่อไหร่จะเสร็จ")):
        return "ตอนนี้คาดว่าจะเรียบร้อยได้ประมาณเมื่อไหร่คะ"
    if state_key == "blocked" and any(k in low for k in ("ติดตรงไหน", "ติดปัญหาตรงไหน", "ปัญหาคืออะไร", "ติดขัดตรงไหน")):
        return "ตอนนี้ยังติดตรงส่วนไหนอยู่ไหมคะ"
    if state_key == "waiting_response" and any(k in low for k in ("ตอบหรือยัง", "ตอบกลับหรือยัง", "ตอบมาไหม")):
        return "ตอนนี้ทางนั้นตอบกลับมาแล้วหรือยังคะ"
    if state_key == "waiting_document" and any(k in low for k in ("เอกสารได้หรือยัง", "เอกสารมาหรือยัง", "เอกสารกลับมาหรือยัง")):
        return "ตอนนี้เอกสารที่รออยู่กลับมาแล้วหรือยังคะ"
    if state_key == "waiting_approval" and any(k in low for k in ("อนุมัติหรือยัง", "ผ่านหรือยัง", "เซ็นหรือยัง")):
        return "ตอนนี้ขั้นตอนอนุมัติ/ลงนามผ่านแล้วหรือยังคะ"
    if state_key == "waiting_goods" and any(k in low for k in ("ของมาหรือยัง", "ของเข้าหรือยัง", "สินค้าเข้าหรือยัง")):
        return "ตอนนี้ของที่รออยู่เข้ามาแล้วหรือยังคะ"
    # If owner provided a real question, keep it nearly verbatim.
    if x.endswith(("?", "ไหม", "ไหมคะ", "หรือยัง", "หรือยังคะ", "เมื่อไหร่", "เมื่อไหร่คะ", "ตรงไหน", "ตรงไหนคะ")):
        if not x.endswith(("คะ", "ค่ะ", "ครับ", "?")):
            x += "คะ"
        return x
    # Otherwise convert a short semantic phrase into a polite question.
    if x.startswith("ถาม"):
        x = x[3:].strip()
    if not x:
        return ""
    if not x.endswith(("คะ", "ค่ะ", "ครับ", "?")):
        x += "คะ"
    return x


def parse_owner_state_question_rule(text: str | None) -> tuple[str, str, str] | None:
    """Parse e.g. 'เวลางานเลยกำหนด ให้ถามวันที่คาดว่าจะเสร็จ'."""
    raw = " ".join((text or "").strip().split())
    if not raw or "ให้ถาม" not in raw:
        return None
    before, instruction = raw.split("ให้ถาม", 1)
    before = re.sub(r"^(?:เวลา|เมื่อ|ถ้า)\s*", "", before.strip(), flags=re.IGNORECASE)
    before = re.sub(r"^งาน\s*", "", before.strip(), flags=re.IGNORECASE)
    before_low = before.lower().strip(" :,-")
    state_key = None
    for key, aliases in _STATE_RULE_ALIASES:
        if any(alias in before_low for alias in aliases):
            state_key = key
            break
    if not state_key:
        return None
    question = _normalize_owner_question_text(state_key, instruction)
    if not question:
        return None
    return state_key, question, STATE_QUESTION_LABELS[state_key]


def _state_question_key(task: Task, memory: str = "") -> str:
    combined = " ".join(filter(None, [memory, task.progress_summary, task.waiting_on, task.next_action])).lower()
    # A concrete blocker is more actionable than a generic overdue state.
    if any(k in combined for k in ("ติดปัญหา", "ติดขัด", "ยังแก้ไม่ได้", "แก้ไม่ได้", "มีปัญหา", "blocked")):
        return "blocked"
    if task.next_action and _appointment_like(task.next_action):
        return "appointment"
    waiting = (task.waiting_on or "").lower()
    if waiting:
        if any(k in waiting for k in ("เอกสาร", "ใบตรวจรับ", "หนังสือ", "datasheet", "data sheet")):
            return "waiting_document"
        if any(k in waiting for k in ("อนุมัติ", "อนุญาต", "เซ็น", "ลงนาม")):
            return "waiting_approval"
        if any(k in waiting for k in ("ของ", "สินค้า", "อุปกรณ์", "ตู้", "สาย", "fiber", "ไฟเบอร์")):
            return "waiting_goods"
        if any(k in waiting for k in ("ตอบ", "เซลล์", "sales", "เจ้าหน้าที่", "ผู้ขาย", "supplier", "futong")):
            return "waiting_response"
    if task.status == "OVERDUE":
        return "overdue"
    if task.status == "IN_PROGRESS":
        return "in_progress"
    return "general"


def _policy_question(policy: dict | None, task: Task, fallback: str, memory: str = "") -> str:
    questions = (policy or {}).get("state_questions") or {}
    key = _state_question_key(task, memory)
    value = str(questions.get(key) or "").strip()
    return value or fallback


def _waiting_question(waiting_on: str, reminder_count: int = 0) -> str:
    """Ask only about the unresolved dependency, using its actual business state."""
    w = _normalize_waiting_phrase(waiting_on)
    low = w.lower()
    if not w:
        return "ตอนนี้สิ่งที่รออยู่ขยับไปถึงไหนแล้วคะ"

    # Extract the party being waited on instead of echoing a whole progress sentence.
    party = ""
    m = re.search(r"(?:ทาง)?\s*(เซลล์|sales|เจ้าหน้าที่|ผู้ขาย|supplier|futong)[^,.;]*?(?:ยังไม่ตอบรับ|ยังไม่ตอบ|ตอบกลับ|ตอบรับ|ตอบ)", low)
    if m:
        raw = m.group(1)
        party_map = {"sales": "เซลล์", "supplier": "ผู้ขาย", "futong": "Futong"}
        party = party_map.get(raw, raw)
    if party:
        prefix = "" if party == "เจ้าหน้าที่" else "ทาง"
        return f"ตอนนี้{prefix}{party}ตอบกลับมาแล้วหรือยังคะ"

    if any(k in low for k in ("เอกสาร", "ใบตรวจรับ", "หนังสือ", "datasheet", "data sheet")):
        if "เจ้าหน้าที่" in low or "ตอบ" in low:
            return "ตอนนี้เจ้าหน้าที่ตอบกลับมาเพิ่มเติมแล้วหรือยังคะ"
        return "ตอนนี้เอกสารที่รออยู่กลับมาแล้วหรือยังคะ"
    if any(k in low for k in ("อนุมัติ", "อนุญาต", "เซ็น", "ลงนาม")):
        return "ตอนนี้ขั้นตอนอนุมัติ/ลงนามผ่านแล้วหรือยังคะ"
    if any(k in low for k in ("ของ", "สินค้า", "อุปกรณ์", "ตู้", "สาย", "fiber", "ไฟเบอร์")):
        return "ตอนนี้ของที่รออยู่เข้ามาแล้วหรือยังคะ"

    # Remove stale/completed clauses before echoing a generic dependency.
    dep = re.sub(r".*?(?:แต่|ตอนนี้)\s*", "", w).strip()
    dep = re.sub(r"(?:ยังไม่ตอบรับ|ยังไม่ตอบ|กำลังรอ|รอ)\s*", "", dep).strip(" ,-:|.")
    if len(dep) > 42:
        dep = dep[:41].rstrip() + "…"
    if dep:
        variants = (
            f"ตอนนี้เรื่อง{dep}มีความคืบหน้าแล้วหรือยังคะ",
            f"เรื่อง{dep}ตอนนี้ขยับไปถึงไหนแล้วคะ",
        )
        return variants[reminder_count % len(variants)]
    return "ตอนนี้สิ่งที่รออยู่ขยับไปถึงไหนแล้วคะ"


def _progress_question(task: Task, policy: dict | None = None, memory: str = "") -> str:
    """Select a state-driven question, honoring owner's runtime state rules."""
    rc = int(task.reminder_count or 0)
    state_key = _state_question_key(task, memory)

    if task.waiting_on:
        fallback = _waiting_question(task.waiting_on, rc)
        return _policy_question(policy, task, fallback, memory)
    if task.next_action:
        action = _clip_message_piece(task.next_action, 86)
        if _appointment_like(action):
            fallback = "วันนี้เป็นช่วงที่นัดไว้ ตอนนี้ดำเนินการเป็นอย่างไรบ้างคะ"
            return _policy_question(policy, task, fallback, memory)
        if any(k in action.lower() for k in ("ส่ง", "ส่งของ", "ส่งเอกสาร")):
            fallback = "ขั้นตอนที่ต้องส่งต่อ ตอนนี้ดำเนินการเรียบร้อยหรือยังคะ"
            return _policy_question(policy, task, fallback, memory)
        fallback = f"ขั้นตอนถัดไปเรื่อง{action} ตอนนี้ไปถึงไหนแล้วคะ"
        return _policy_question(policy, task, fallback, memory)
    if state_key == "blocked":
        fallback = "ตอนนี้ยังติดตรงส่วนไหนอยู่ไหมคะ"
        return _policy_question(policy, task, fallback, memory)
    if task.status == "OVERDUE":
        fallback = (
            "ตอนนี้คาดว่าจะเรียบร้อยได้ประมาณเมื่อไหร่คะ"
            if rc % 2 == 0 else
            "ตอนนี้ยังติดตรงส่วนไหนอยู่ไหมคะ และคาดว่าจะจบได้เมื่อไหร่คะ"
        )
        return _policy_question(policy, task, fallback, memory)
    if task.status == "IN_PROGRESS":
        fallback = (
            "ตอนนี้เหลือขั้นตอนไหนอีกบ้างคะ"
            if rc % 2 == 0 else
            "จากที่ทำต่อมา ตอนนี้ไปถึงขั้นตอนไหนแล้วคะ"
        )
        return _policy_question(policy, task, fallback, memory)
    fallback = (
        "ตอนนี้ไปถึงไหนแล้วคะ"
        if rc % 2 == 0 else
        "ตอนนี้มีอะไรขยับเพิ่มเติมแล้วบ้างคะ"
    )
    return _policy_question(policy, task, fallback, memory)


def contextual_followup_text(db: Session, task: Task, assignee_token: str, owner_name: str) -> tuple[str, bool]:
    """State-driven follow-up text, finalized by the owner's live language policy."""
    policy = get_followup_policy(db)
    structured_memory = bool((task.progress_summary or "").strip())
    memory = (task.progress_summary or "").strip()
    if not memory:
        ev = latest_status_reply(db, task)
        memory = summarize_progress_update(ev.text if ev else None)
    topic = _clip_message_piece(task_reference_label(db, task), 76)
    if memory and not structured_memory and not _memory_relevant_to_task(db, task, memory):
        print("task memory rejected as cross-topic contamination:", task.task_code, repr(memory), "base=", repr(_base_task_text(db, task)))
        memory = ""

    def finish(value: str) -> tuple[str, bool]:
        return apply_followup_policy(db, value), True

    if not memory:
        if task.status == "OVERDUE":
            q = _progress_question(task, policy, memory)
            return finish(f"{assignee_token}คะ เรื่อง{topic}เลยกำหนดแล้วค่ะ\n{q}")
        return finish(f"{assignee_token}คะ เรื่อง{topic} ตอนนี้ไปถึงไหนแล้วคะ")

    memory = _clip_message_piece(memory, 100)
    completion_like = (
        any(x in memory for x in ("เรียบร้อยแล้ว", "เสร็จแล้ว", "เสร็จเรียบร้อย", "จบแล้ว"))
        and not any(x in memory for x in ("ยังไม่", "รอ", "แต่", "ติด", "เหลือ"))
    )
    if completion_like and task.status in {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"}:
        print("completion-like stale snapshot suppressed:", task.task_code, repr(memory), "status=", task.status)
        memory = ""

    question = _progress_question(task, policy, memory)
    if task.next_action and _appointment_like(task.next_action):
        return finish(
            f"{assignee_token}คะ เรื่อง{topic} วันนี้ถึงช่วงที่นัดไว้ตามอัปเดตล่าสุดแล้วค่ะ\n"
            f"ตอนนี้ดำเนินการเป็นอย่างไรบ้างคะ"
        )
    if task.status == "WAITING" or task.waiting_on:
        return finish(f"{assignee_token}คะ เรื่อง{topic} ล่าสุด: {memory}\n{question}")
    return finish(f"{assignee_token}คะ เรื่อง{topic} ล่าสุด: {memory}\n{question}")

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
    exact_key = normalize_alias_key(name)
    legacy_key = normalize_name(name)
    if not exact_key:
        return None
    alias = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == exact_key))
    if not alias and legacy_key and legacy_key != exact_key:
        alias = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == legacy_key))
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


def add_alias_to_person(db: Session, person_id: int, alias_name: str) -> PersonAlias:
    person = db.get(Person, person_id)
    if not person:
        raise ValueError("person not found")
    alias_name = " ".join((alias_name or "").strip().split())
    if not alias_name:
        raise ValueError("alias is required")
    key = normalize_alias_key(alias_name)
    existing = db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == key))
    if existing:
        if existing.person_id == person.id:
            existing.alias = alias_name
            db.commit(); db.refresh(existing); return existing
        other = db.get(Person, existing.person_id)
        raise ValueError(f"ชื่อนี้ถูกใช้กับบุคคลอื่นแล้ว: {other.canonical_name if other else 'unknown'}")
    row = PersonAlias(person_id=person.id, alias=alias_name, normalized_alias=key)
    db.add(row); db.commit(); db.refresh(row)
    return row


def delete_person_alias(db: Session, person_id: int, alias_name: str) -> None:
    person = db.get(Person, person_id)
    if not person:
        raise ValueError("person not found")
    key = normalize_alias_key(alias_name)
    row = db.scalar(select(PersonAlias).where(PersonAlias.person_id == person.id, PersonAlias.normalized_alias == key))
    if not row:
        # Backward compatibility for aliases stored by v0.6.8 and earlier.
        legacy = normalize_name(alias_name)
        row = db.scalar(select(PersonAlias).where(PersonAlias.person_id == person.id, PersonAlias.normalized_alias == legacy))
    if not row:
        raise ValueError("alias not found")
    # Keep at least the canonical identity resolvable.
    if normalize_name(row.alias) == normalize_name(person.canonical_name):
        raise ValueError("ไม่สามารถลบชื่อมาตรฐานออกจาก Aliases ได้")
    db.delete(row); db.commit()


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
    message_id: str | None = None, confidence: float | None = None,
) -> TaskEvent:
    ev = TaskEvent(
        task_id=task.id, event_type=event_type, actor_name=actor_name,
        actor_user_id=actor_user_id, text=text, message_id=message_id, confidence=confidence,
        old_status=old_status, new_status=new_status,
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


def _smart_search_terms(query: str) -> list[str]:
    """Conservative owner-search normalization. Never mutates tasks."""
    q = " ".join((query or "").strip().split()).lower()
    if not q:
        return []
    terms = {q}
    # Business synonyms/orthographic variants. Expand only well-known equivalents.
    groups = [
        {"meter", "metre", "มิเตอร์", "มิเตอร์ไฟ", "มิเตอร์ชั่วคราว"},
        {"po", "p.o.", "ใบสั่งซื้อ"},
        {"อบต", "อบต."},
        {"เทศบาล", "เทศบาลเมือง", "เทศบาลตำบล"},
    ]
    for group in groups:
        if q in group or any(token in q.split() for token in group):
            terms.update(group)
    # harmless spacing/punctuation variants
    terms.add(q.replace(" ", ""))
    return [t for t in terms if t]


def smart_search_tasks(db: Session, query: str, limit: int = 50) -> list[Task]:
    """Read-only natural owner search across task identity, progress and timeline.

    Searches every status so completed historical work is discoverable. Exact/lexical
    evidence is required; this function deliberately does not use fuzzy similarity to
    avoid cross-project contamination.
    """
    terms = _smart_search_terms(query)
    if not terms:
        return []
    task_clauses = []
    event_clauses = []
    for t in terms:
        like = f"%{t}%"
        task_clauses.extend([
            Task.task_code.ilike(like), Task.title.ilike(like), Task.project.ilike(like),
            Task.assignee_name.ilike(like), Task.notes.ilike(like),
            Task.progress_summary.ilike(like), Task.waiting_on.ilike(like), Task.next_action.ilike(like),
        ])
        event_clauses.append(TaskEvent.text.ilike(like))
    event_task_ids = select(TaskEvent.task_id).where(or_(*event_clauses))
    stmt = (select(Task)
            .where(or_(or_(*task_clauses), Task.id.in_(event_task_ids)))
            .order_by(Task.updated_at.desc(), Task.id.desc())
            .limit(limit))
    return list(db.scalars(stmt).all())


def find_existing_followup_task(
    db: Session,
    group_id: str,
    text: str,
    *,
    sender_name: str | None = None,
    assignee_name: str | None = None,
    assignee_user_id: str | None = None,
    min_confidence: float = 0.80,
) -> tuple[Task | None, float, list[Task]]:
    """Find an existing active task before any new FU is created.

    Content evidence is mandatory. Assignee identity can improve confidence but cannot
    turn a weak topical match into a duplicate. Returns (target, confidence, ambiguous).
    """
    tasks = open_tasks(db, group_id)
    if not tasks:
        return None, 0.0, []
    match_text = text or ""
    # Assignee names in a follow-up request are routing metadata, not task subject.
    # Remove the explicit assignee from the semantic text before comparing subjects.
    if assignee_name:
        match_text = re.sub(re.escape(str(assignee_name)), " ", match_text, flags=re.IGNORECASE)
    rows = rank_status_targets_with_history(
        db, tasks, sender_name, assignee_name, match_text, assignee_user_id
    )
    if not rows:
        return None, 0.0, []

    best = rows[0]
    best_content = float(best.get("content") or 0.0)
    # Content dominates; identity adds only a small bonus after meaningful content exists.
    confidence = min(1.0, best_content + (0.08 if best_content >= 0.55 and best.get("identity_match") else 0.0))
    plausible = [r for r in rows if float(r.get("content") or 0.0) >= max(0.55, best_content - 0.10)]

    if confidence < min_confidence:
        return None, confidence, [r["task"] for r in plausible[:3]]
    if len(plausible) > 1:
        second = plausible[1]
        second_conf = min(1.0, float(second.get("content") or 0.0) + (0.08 if second.get("identity_match") else 0.0))
        if confidence - second_conf < 0.12:
            return None, confidence, [r["task"] for r in plausible[:3]]
    return best["task"], confidence, []


def find_task_for_explicit_query(
    db: Session,
    group_id: str,
    text: str,
    *,
    sender_name: str | None = None,
    assignee_name: str | None = None,
    assignee_user_id: str | None = None,
) -> tuple[Task | None, float, list[Task]]:
    """Resolve a status/follow-up query against open tasks, then recent completed tasks.

    The sender is the person asking, not automatically the assignee. An explicit
    @mentioned assignee is only a tie-breaker after meaningful topic evidence.
    """
    target, score, ambiguous = find_existing_followup_task(
        db, group_id, text, sender_name=sender_name, assignee_name=assignee_name,
        assignee_user_id=assignee_user_id, min_confidence=0.80
    )
    if target or ambiguous:
        return target, score, ambiguous

    recent_done = list(db.scalars(
        select(Task).where(Task.group_id == group_id, Task.status == "COMPLETED")
        .order_by(Task.updated_at.desc(), Task.id.desc()).limit(30)
    ).all())
    if not recent_done:
        return None, score, []
    rows = rank_status_targets_with_history(db, recent_done, sender_name, assignee_name, text, assignee_user_id)
    if not rows:
        return None, score, []
    best = rows[0]
    best_score = float(best.get("content") or 0.0)
    if best_score < 0.80:
        return None, best_score, []
    if len(rows) > 1 and float(rows[1].get("content") or 0.0) >= best_score - 0.10:
        return None, best_score, [r["task"] for r in rows[:3] if float(r.get("content") or 0.0) >= 0.55]
    return best["task"], best_score, []


# v0.6.40 owner private-context helpers ---------------------------------------
def get_runtime_preference(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == key))
    return row.value if row and row.value is not None else default


def set_runtime_preference(db: Session, key: str, value: str) -> str:
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == key))
    if row:
        row.value = str(value)
        row.updated_at = utcnow()
    else:
        row = OwnerPreference(key=key, value=str(value))
        db.add(row)
    db.commit()
    return str(value)


def owner_reopen_task_state(
    db: Session,
    task: Task,
    *,
    next_reminder_at: datetime,
    actor_name: str | None = None,
    actor_user_id: str | None = None,
    reason: str = "",
    now_utc: datetime | None = None,
) -> str:
    """Correct a falsely-completed task back to an active state.

    This is intentionally owner-only at the routing layer. The function preserves
    history, restores the active status immediately preceding the latest completion
    when available, and creates explicit correction/reactivation timeline events.
    """
    now = now_utc or utcnow()
    old_status = task.status
    latest_completion = db.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.new_status == "COMPLETED",
        ).order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc()).limit(1)
    )
    if latest_completion and latest_completion.old_status in OPEN_STATUSES:
        new_status = latest_completion.old_status
    elif task.due_at and task.due_at < now:
        new_status = "OVERDUE"
    else:
        new_status = "IN_PROGRESS"

    task.status = new_status
    task.next_reminder_at = next_reminder_at
    correction = (reason or "เจ้าของยืนยันว่างานยังไม่เสร็จ").strip()
    current_progress = task.progress_summary or ""
    if not current_progress or any(k in current_progress.lower() for k in ("เรียบร้อย", "เสร็จ", "ปิดงาน", "completed")):
        task.progress_summary = correction
    current_action = task.next_action or ""
    if not current_action or any(k in current_action.lower() for k in ("ปิดงาน", "เสร็จ", "เรียบร้อย", "completed")):
        task.next_action = "ติดตามความคืบหน้าต่อ"
    task.last_progress_at = now

    record_task_event(
        db, task, "OWNER_STATUS_CORRECTION", actor_name=actor_name, actor_user_id=actor_user_id,
        text=correction, old_status=old_status, new_status=new_status, commit=False,
    )
    record_task_event(
        db, task, "REMINDER_REACTIVATED", actor_name=actor_name, actor_user_id=actor_user_id,
        text="เปิดคิวติดตามอีกครั้ง", old_status=new_status, new_status=new_status, commit=False,
    )
    db.commit()
    db.refresh(task)
    return new_status
