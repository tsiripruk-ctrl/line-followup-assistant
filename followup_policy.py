"""Persistent, non-bypassable calendar for automatic follow-up reminders."""
from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from models import Task, OwnerPreference
from config import settings

KEY = 'global_followup_calendar_v1'
DEFAULT = {'start': 510, 'end': 1050, 'weekends': False}
ACTIVE = ('OPEN', 'IN_PROGRESS', 'WAITING', 'OVERDUE')


def utcnow():
    return datetime.utcnow()


def load_policy(db):
    # Include pending changes so a policy command and its queue repair are atomic.
    row = next((r for r in list(db.new) + list(db.dirty)
                if isinstance(r, OwnerPreference) and r.key == KEY), None)
    if row is None:
        row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == KEY).execution_options(populate_existing=True))
    try:
        value = json.loads(row.value) if row else DEFAULT.copy()
        if (type(value['start']) is not int or type(value['end']) is not int
                or not 0 <= value['start'] < value['end'] <= 1440
                or type(value['weekends']) is not bool):
            return DEFAULT.copy()
        return {key: value[key] for key in DEFAULT}
    except (ValueError, TypeError, KeyError):
        return DEFAULT.copy()


def allowed(policy, at):
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    local = at.astimezone(ZoneInfo(settings.timezone))
    return ((policy['weekends'] or local.weekday() < 5)
            and policy['start'] <= local.hour * 60 + local.minute < policy['end'])


def next_allowed(policy, candidate):
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    local = candidate.astimezone(ZoneInfo(settings.timezone))
    while True:
        if not policy['weekends'] and local.weekday() >= 5:
            local = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            continue
        minute = local.hour * 60 + local.minute
        if minute < policy['start']:
            local = local.replace(hour=policy['start']//60, minute=policy['start']%60, second=0, microsecond=0)
        elif minute >= policy['end']:
            local = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            continue
        return local.astimezone(timezone.utc).replace(tzinfo=None)


def normalize_task(db, task, policy):
    if task.status not in ACTIVE or task.next_reminder_at is None:
        return False
    candidate = task.next_reminder_at
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == 'commitment:' + task.task_code))
    # A pending commitment may not have been flushed yet.
    row = next((r for r in list(db.new)+list(db.dirty) if isinstance(r,OwnerPreference)
                and r.key == 'commitment:' + task.task_code),row)
    try:
        if row and row.value:
            candidate = max(candidate, datetime.fromisoformat(row.value))
    except ValueError:
        pass
    value = next_allowed(policy,candidate)
    if value != task.next_reminder_at:
        task.next_reminder_at = value
        return True
    return False


def repair_calendar(db):
    policy = load_policy(db)
    tasks = list(db.scalars(select(Task).where(Task.status.in_(ACTIVE), Task.next_reminder_at.is_not(None))).all())
    return sum(normalize_task(db,t,policy) for t in tasks)


@event.listens_for(Session, 'before_flush')
def enforce_calendar_before_flush(db, flush_context, instances):
    tasks = [t for t in list(db.new)+list(db.dirty) if isinstance(t,Task)
             and t.task_code and t.next_reminder_at is not None]
    if tasks:
        policy = load_policy(db)
        for task in tasks:
            normalize_task(db,task,policy)


def change_policy(db, actor_uid, *, start=None, end=None, weekends=None):
    from learning import can_manage, audit
    if not can_manage(db,actor_uid):
        raise PermissionError('เฉพาะเจ้าของระบบหรือผู้จัดการที่ได้รับสิทธิ์ค่ะ')
    policy = load_policy(db)
    before = dict(policy)
    if start is not None or end is not None:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= 1440:
            raise ValueError('ช่วงเวลาไม่ถูกต้องค่ะ เวลาเริ่มต้องก่อนเวลาสิ้นสุดภายในวันเดียวกัน')
        policy.update(start=start,end=end)
    if weekends is not None:
        policy['weekends'] = bool(weekends)
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == KEY).execution_options(populate_existing=True))
    if row is None:
        row=OwnerPreference(key=KEY,value='');db.add(row)
    row.value=json.dumps(policy)
    count=repair_calendar(db)
    audit(db,actor_uid,'global_followup_calendar',{'before':before,'after':policy,'repaired':count})
    return policy,count


def describe(policy):
    def clock(n): return f'{n//60:02d}:{n%60:02d}'
    days='ทุกวัน' if policy['weekends'] else 'จันทร์–ศุกร์ (งดเสาร์–อาทิตย์)'
    return (f'เวลาติดตามทั้งหมด: {clock(policy["start"])}–{clock(policy["end"])}\nวันติดตาม: {days}'
            f'\nเขตเวลา: {settings.timezone}\nเวลาเริ่มรวมอยู่ในช่วง เวลาสิ้นสุดไม่รวม'
            '\nใช้กับทุกงาน รวมบังคับติดตามและสั่งรันทันที; ตอบรับผู้ใช้และรายงานเช้า–เย็นใช้กติกาเดิม')
