"""Work-response observations and explicit private management controls.

Only trusted, committed task updates by the bound assignee are observed. No raw
conversation, personality labels, silent-user scores, or cross-person guesses.
All writes share the caller's transaction; no LINE/network calls live here.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from statistics import median
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete
from models import OwnerPreference, Person, PersonAlias, Task, TaskEvent, OutboundTaskMessage
from config import settings

MAX_SAMPLES = 200
RETENTION_DAYS = 90
MIN_SAMPLES = 8
MIN_DAYS = 3
MIN_SHARE = 0.60
REMINDER_KINDS = ('REMINDER', 'FORCED_REMINDER', 'PRE_DUE')


def _key(uid, kind='profile'):
    digest = hashlib.sha256(str(uid).encode()).hexdigest()[:24]
    return f'learning_{kind}:{digest}'


def _read(db, key, default=None):
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == key))
    try:
        return json.loads(row.value) if row else default
    except (ValueError, TypeError):
        return default


def _write(db, key, value):
    row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == key))
    if row is None:
        row = OwnerPreference(key=key, value='')
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)


def is_owner(uid):
    return bool(uid and settings.owner_line_user_id and uid == settings.owner_line_user_id)


def manager_ids(db):
    value = _read(db, 'private_manager_ids', [])
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def can_manage(db, uid):
    if is_owner(uid):
        return True
    return bool(uid and uid in manager_ids(db) and db.scalar(select(Person.id).where(
        Person.line_user_id == uid, Person.active.is_(True))))


def audit(db, uid, action, detail):
    _write(db, 'private_audit:' + uuid4().hex,
           {'at': datetime.utcnow().isoformat(), 'actor_user_id': uid, 'action': action, 'detail': detail})
    db.flush()
    rows = list(db.scalars(select(OwnerPreference).where(
        OwnerPreference.key.like('private_audit:%')).order_by(OwnerPreference.id.desc()).offset(100)).all())
    for row in rows:
        db.delete(row)


def set_manager(db, actor_uid, person, enabled):
    if not is_owner(actor_uid):
        raise PermissionError('เฉพาะเจ้าของระบบเท่านั้นที่เพิ่มหรือลบผู้จัดการได้ค่ะ')
    if not person.active or not person.line_user_id:
        raise ValueError('ต้องเป็นบุคคลที่ใช้งานอยู่และผูกบัญชี LINE แล้วค่ะ')
    ids = set(manager_ids(db))
    ids.add(person.line_user_id) if enabled else ids.discard(person.line_user_id)
    _write(db, 'private_manager_ids', sorted(ids))
    audit(db, actor_uid, 'manager_added' if enabled else 'manager_removed', person.canonical_name)


def resolve_person(db, name):
    # Exact names/aliases only. Duplicate call/display names remain ambiguous.
    key = re.sub(r'\s+', '', (name or '').strip().lstrip('@')).casefold()
    if not key:
        return []
    people = list(db.scalars(select(Person).where(Person.active.is_(True))).all())
    matches = {p.id: p for p in people if any(
        re.sub(r'\s+', '', str(value or '')).casefold() == key
        for value in (p.canonical_name, p.call_name, p.display_name, p.line_user_id))}
    aliases = list(db.scalars(select(PersonAlias)).all())
    by_id = {p.id: p for p in people}
    for a in aliases:
        if re.sub(r'\s+', '', a.alias).casefold() == key and a.person_id in by_id:
            matches[a.person_id] = by_id[a.person_id]
    return list(matches.values())


def global_enabled(db):
    return _read(db, 'learning_enabled', True) is True


def profile_settings(db, uid):
    data = _read(db, _key(uid), {})
    return data if isinstance(data, dict) else {}


def set_learning_enabled(db, actor_uid, enabled, person=None):
    if not can_manage(db, actor_uid):
        raise PermissionError('คำสั่งนี้สำหรับเจ้าของระบบหรือผู้จัดการที่ได้รับสิทธิ์ค่ะ')
    if person is None:
        if not is_owner(actor_uid):
            raise PermissionError('เฉพาะเจ้าของระบบเท่านั้นที่เปิดหรือปิดการเรียนรู้ทั้งระบบได้ค่ะ')
        _write(db, 'learning_enabled', bool(enabled))
    else:
        if not person.line_user_id:
            raise ValueError('บุคคลนี้ยังไม่ได้ผูกบัญชี LINE ค่ะ')
        data = profile_settings(db, person.line_user_id)
        data['paused'] = not enabled
        _write(db, _key(person.line_user_id), data)
    audit(db, actor_uid, 'learning_enabled' if enabled else 'learning_paused',
          person.canonical_name if person else 'global')


def _sample_rows(db, uid):
    return list(db.scalars(select(OwnerPreference).where(
        OwnerPreference.key.like(_key(uid, 'sample') + ':%'))).all())


def _samples(db, uid, now):
    cutoff = now - timedelta(days=RETENTION_DAYS)
    values = []
    for row in _sample_rows(db, uid):
        try:
            value = json.loads(row.value)
            at = datetime.fromisoformat(value['at'])
            if cutoff <= at <= now and value.get('kind') in ('WAITING', 'IN_PROGRESS', 'COMPLETED', 'OPEN', 'OVERDUE'):
                values.append(value)
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(values, key=lambda v: v['at'])[-MAX_SAMPLES:]


def observe_update(db, task, actor_uid, message_id, text, *, has_date=False, now=None):
    now = now or datetime.utcnow()
    # Identity is the actual LINE account, not a display-name or a mentioned person.
    if not actor_uid or not message_id or task.assignee_user_id != actor_uid:
        return False
    if not global_enabled(db) or profile_settings(db, actor_uid).get('paused'):
        return False
    sample_key = _key(actor_uid, 'sample') + ':' + hashlib.sha256(str(message_id).encode()).hexdigest()[:32]
    if db.scalar(select(OwnerPreference.id).where(OwnerPreference.key == sample_key)):
        return False
    samples = _samples(db, actor_uid, now)
    # Only reminder messages, never receipts or status answers. One interval per reminder.
    reminder = db.scalar(select(OutboundTaskMessage).where(
        OutboundTaskMessage.task_id == task.id,
        OutboundTaskMessage.group_id == task.group_id,
        OutboundTaskMessage.message_kind.in_(REMINDER_KINDS),
        OutboundTaskMessage.created_at <= now,
        OutboundTaskMessage.created_at >= now - timedelta(hours=72),
    ).order_by(OutboundTaskMessage.created_at.desc(), OutboundTaskMessage.id.desc()).limit(1))
    last_assignment = db.scalar(select(TaskEvent.created_at).where(
        TaskEvent.task_id == task.id, TaskEvent.event_type == 'ASSIGNEE_CHANGED'
    ).order_by(TaskEvent.created_at.desc()).limit(1))
    lag = None
    rid = None
    if reminder and (not last_assignment or reminder.created_at >= last_assignment):
        rid = reminder.id
        if not any(s.get('reminder_id') == rid for s in samples):
            lag = round((now - reminder.created_at).total_seconds() / 60, 1)
    sample = {'at': now.isoformat(), 'task_id': task.id, 'kind': task.status,
              'chars': len(re.sub(r'\s+', '', text or '')), 'has_date': bool(has_date),
              'reminder_id': rid, 'minutes_since_reminder': lag}
    _write(db, sample_key, sample)
    db.flush()
    # Retain at most 200 observations/90 days. Original task history is separate.
    rows = _sample_rows(db, actor_uid)
    live_keys = []
    for row in rows:
        try:
            at = datetime.fromisoformat(json.loads(row.value)['at'])
        except (ValueError, TypeError, KeyError):
            db.delete(row)
            continue
        if at < now - timedelta(days=RETENTION_DAYS):
            db.delete(row)
        else:
            live_keys.append((at, row.id, row))
    for _, _, row in sorted(live_keys, key=lambda v: (v[0], v[1]), reverse=True)[MAX_SAMPLES:]:
        db.delete(row)
    return True


def clear_learning(db, actor_uid, person):
    if not can_manage(db, actor_uid):
        raise PermissionError('ไม่มีสิทธิ์ล้างข้อมูลการเรียนรู้ค่ะ')
    if not person.line_user_id:
        raise ValueError('บุคคลนี้ยังไม่ได้ผูกบัญชี LINE ค่ะ')
    for row in _sample_rows(db, person.line_user_id):
        db.delete(row)
    audit(db, actor_uid, 'learning_cleared', person.canonical_name)


def person_profile(db, uid, *, now=None):
    now = now or datetime.utcnow()
    samples = _samples(db, uid, now)
    tz = ZoneInfo(settings.timezone)
    local_times = [datetime.fromisoformat(s['at']).replace(tzinfo=timezone.utc).astimezone(tz) for s in samples]
    start = settings.followup_start_hour * 60 + settings.followup_start_minute
    end = settings.followup_end_hour * 60 + settings.followup_end_minute
    in_window = [t for t in local_times if start <= t.hour * 60 + t.minute < end]
    hours = Counter(t.hour for t in in_window)
    top_hour, top_count = hours.most_common(1)[0] if hours else (None, 0)
    share = top_count / len(in_window) if in_window else 0
    days = len({t.date() for t in in_window})
    confident = len(in_window) >= MIN_SAMPLES and days >= MIN_DAYS and share >= MIN_SHARE
    cfg = profile_settings(db, uid)
    lags = [s['minutes_since_reminder'] for s in samples if isinstance(s.get('minutes_since_reminder'), (float, int))]
    return {'sample_count': len(samples), 'distinct_days': len({t.date() for t in local_times}),
            'task_count': len({s['task_id'] for s in samples}), 'state_counts': dict(Counter(s['kind'] for s in samples)),
            'median_chars': median([s['chars'] for s in samples]) if samples else None,
            'dated_updates': sum(bool(s.get('has_date')) for s in samples),
            'timed_samples': len(lags), 'median_minutes_since_reminder': median(lags) if lags else None,
            'common_hour': top_hour, 'common_hour_share': round(share, 3),
            'schedule_evidence_sufficient': confident, 'work_window_samples': len(in_window),
            'learning_active': global_enabled(db) and not cfg.get('paused', False),
            'preferred_window': cfg.get('preferred_window'), 'retention_days': RETENTION_DAYS}


def set_preferred_window(db, actor_uid, person, start=None, end=None):
    if not can_manage(db, actor_uid):
        raise PermissionError('ไม่มีสิทธิ์ตั้งเวลาติดตามค่ะ')
    if not person.line_user_id:
        raise ValueError('บุคคลนี้ยังไม่ได้ผูกบัญชี LINE ค่ะ')
    data = profile_settings(db, person.line_user_id)
    if start is None and end is None:
        data.pop('preferred_window', None)
    else:
        work_start = settings.followup_start_hour * 60 + settings.followup_start_minute
        work_end = settings.followup_end_hour * 60 + settings.followup_end_minute
        if not isinstance(start, int) or not isinstance(end, int) or not work_start <= start < end <= work_end:
            raise ValueError('ช่วงเวลาต้องอยู่ภายในเวลาติดตามของระบบ และเวลาเริ่มต้องก่อนเวลาสิ้นสุดค่ะ')
        data['preferred_window'] = [start, end]
    _write(db, _key(person.line_user_id), data)
    audit(db, actor_uid, 'preferred_window', {'person': person.canonical_name, 'window': data.get('preferred_window')})


def personalize_followup_at(db, task, candidate, *, now=None):
    """Only move a normal future check later; never override a commitment or urgency."""
    now = now or datetime.utcnow()
    if candidate is None or candidate <= now or not task.assignee_user_id:
        return candidate
    if task.status in ('COMPLETED', 'CANCELLED', 'OVERDUE') or task.due_at is not None:
        return candidate
    # Persistent forced-followup registry belongs to the existing scheduler.
    forced = _read(db, 'forced_followup_tasks_v1', {})
    if isinstance(forced, dict) and task.task_code in forced:
        return candidate
    commitment_row = db.scalar(select(OwnerPreference).where(OwnerPreference.key == 'commitment:' + task.task_code))
    try:
        if commitment_row and commitment_row.value and datetime.fromisoformat(commitment_row.value) > now:
            return candidate
    except ValueError:
        pass
    profile = person_profile(db, task.assignee_user_id, now=now)
    window = profile['preferred_window']
    manual = isinstance(window, list) and len(window) == 2 and all(isinstance(x, int) for x in window)
    if not manual:
        if not profile['learning_active'] or not profile['schedule_evidence_sufficient']:
            return candidate
        hour = profile['common_hour']
        window = [max(hour * 60, settings.followup_start_hour * 60 + settings.followup_start_minute),
                  min((hour + 1) * 60, settings.followup_end_hour * 60 + settings.followup_end_minute)]
    start, end = window
    work_start = settings.followup_start_hour * 60 + settings.followup_start_minute
    work_end = settings.followup_end_hour * 60 + settings.followup_end_minute
    if not work_start <= start < end <= work_end:
        return candidate
    local = candidate.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone))
    minute = local.hour * 60 + local.minute
    if start <= minute < end:
        return candidate
    proposed = local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    if minute >= end:
        if not manual:
            return candidate
        proposed += timedelta(days=1)
    result = proposed.astimezone(timezone.utc).replace(tzinfo=None)
    if result < candidate or (not manual and result - candidate > timedelta(hours=2)):
        return candidate
    return result


def reassign_task(db, actor_uid, task, person, reason=''):
    if not can_manage(db, actor_uid):
        raise PermissionError('ไม่มีสิทธิ์เปลี่ยนผู้รับผิดชอบค่ะ')
    if not person.active or not person.line_user_id:
        raise ValueError('ผู้รับผิดชอบใหม่ต้องผูกบัญชี LINE ก่อน เพื่อให้แท็กคนได้ถูกต้องค่ะ')
    if task.status in ('COMPLETED', 'CANCELLED'):
        raise ValueError('งานนี้ปิดอยู่ค่ะ ต้องเปิดงานกลับมาก่อนเปลี่ยนผู้รับผิดชอบ')
    old = {'name': task.assignee_name, 'user_id': task.assignee_user_id}
    if old['user_id'] == person.line_user_id and old['name'] == person.canonical_name:
        return False, old['name']
    task.assignee_name = person.canonical_name
    task.assignee_user_id = person.line_user_id
    detail = {'from': old, 'to': {'name': person.canonical_name, 'user_id': person.line_user_id}, 'reason': reason}
    db.add(TaskEvent(task_id=task.id, event_type='ASSIGNEE_CHANGED', actor_user_id=actor_uid,
                     text=json.dumps(detail, ensure_ascii=False), old_status=task.status, new_status=task.status))
    audit(db, actor_uid, 'assignee_changed', {'task': task.task_code, **detail})
    return True, old['name']

def prune_learning_samples(db, *, now=None):
    """Run during scheduler maintenance, including for inactive/paused people."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=RETENTION_DAYS)
    count = 0
    rows = list(db.scalars(select(OwnerPreference).where(OwnerPreference.key.like('learning_sample:%'))).all())
    for row in rows:
        try:
            expired = datetime.fromisoformat(json.loads(row.value)['at']) < cutoff
        except (ValueError, TypeError, KeyError):
            expired = True
        if expired:
            db.delete(row)
            count += 1
    return count
