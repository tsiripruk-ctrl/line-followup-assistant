"""Explicit, reversible manager corrections; called only after permission checks."""
import json
from datetime import datetime
from sqlalchemy import select
from models import TaskEvent

FIELDS = ('progress_summary', 'waiting_on', 'next_action', 'last_progress_at', 'next_reminder_at')


def snapshot(task):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key in FIELDS for value in [getattr(task, key)]}


def correct(db, task, uid, text):
    from service import update_task_progress_snapshot, pending_commitment_at, record_task_event, explicit_task_scope
    from intent_engine import classify_message_intent
    if task.status in ('COMPLETED', 'CANCELLED'):
        raise ValueError('งานปิดแล้วค่ะ ต้องเปิดงานก่อนแก้ความเข้าใจ')
    scope = explicit_task_scope(db, text)
    if scope is not None and (task not in scope or len(scope) != 1):
        raise ValueError('ข้อความระบุงานอื่นหรือหลายงานค่ะ กรุณาแก้ครั้งละหนึ่งงาน')
    if classify_message_intent(text).intent not in ('PROGRESS_UPDATE', 'NOT_COMPLETED'):
        raise ValueError('ระบุความคืบหน้าหรือสิ่งที่รอให้ชัดเจนค่ะ คำสั่งนี้ไม่ใช้ปิดงาน')
    before = snapshot(task)
    previous = pending_commitment_at(db, task)
    update_task_progress_snapshot(db, task, text, actor_user_id=uid)
    record_task_event(db, task, 'MANAGER_PROGRESS_CORRECTION', actor_user_id=uid,
                      text=json.dumps({'before': before, 'after': snapshot(task),
                                       'previous_commitment': previous.isoformat() if previous else None,
                                       'correction': text}, ensure_ascii=False), commit=False)


def undo(db, task, uid):
    from service import record_task_event, remember_commitment
    latest = db.scalar(select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id.desc()).limit(1))
    if not latest or latest.event_type != 'MANAGER_PROGRESS_CORRECTION':
        raise ValueError('ย้อนกลับไม่ได้ค่ะ มีเหตุการณ์ใหม่หลังแก้ความเข้าใจ หรือไม่มีรายการให้ย้อนกลับ')
    saved = json.loads(latest.text)
    if snapshot(task) != saved['after']:
        raise ValueError('ข้อมูลงานเปลี่ยนแล้วค่ะ กรุณาแก้ความเข้าใจใหม่แทนการย้อนกลับ')
    for key, value in saved['before'].items():
        setattr(task, key, datetime.fromisoformat(value) if key.endswith('_at') and value else value)
    previous = saved['previous_commitment']
    remember_commitment(db, task, datetime.fromisoformat(previous) if previous else None)
    record_task_event(db, task, 'MANAGER_PROGRESS_CORRECTION_UNDONE', actor_user_id=uid,
                      text=json.dumps({'correction_event_id': latest.id}), commit=False)
