"""Manager-reviewed linking. Original speaker preserved, manager recorded separately."""
import json
import re
from datetime import datetime, timedelta
from sqlalchemy import select, update
from models import Message, Task, TaskEvent, OwnerPreference
from learning import can_manage
from rag import _get, review_text, save_review

PREFIXES = ('เชื่อม ', 'ย้อนการเชื่อม ', 'ดูข้อความรอเชื่อม', 'นำข้อความมาเชื่อม ')


def _snapshot(task):
    fields = ('status','progress_summary','waiting_on','next_action','last_progress_at','next_reminder_at')
    return {k: v.isoformat() if isinstance(v,datetime) else v for k in fields for v in [getattr(task,k)]}


def execute(db, uid, raw):
    if not can_manage(db,uid):
        raise PermissionError('ใช้ได้เฉพาะเจ้าของระบบหรือผู้จัดการที่ได้รับสิทธิ์ในแชตส่วนตัวค่ะ')
    if raw == 'ดูข้อความรอเชื่อม':
        rows = db.scalars(select(OwnerPreference).where(OwnerPreference.key.like('rag_review:%')).order_by(OwnerPreference.id.desc()))
        pending = [r.key.split(':',1)[1] for r in rows if json.loads(r.value)['state']=='pending']
        return 'ข้อความรอเชื่อม: '+(', '.join(pending[:30]) or 'ไม่มี')+'\nดูรายละเอียด: ดูข้อความรอเชื่อม U-000001'
    if raw.startswith('ดูข้อความรอเชื่อม '):
        return review_text(db,raw.split()[-1].upper())
    if raw.startswith('นำข้อความมาเชื่อม '):
        # Recover a historical stored message by its exact LINE message ID, never pasted text.
        code,_ = save_review(db,raw[len('นำข้อความมาเชื่อม '):].strip())
        return review_text(db,code)
    match = re.fullmatch(r'เชื่อม\s+(U-\d+)\s+กับ\s+(FU-\d{6}-\d{4,})',raw,re.I)
    undo = re.fullmatch(r'ย้อนการเชื่อม\s+(U-\d+)',raw,re.I)
    if not match and not undo:
        raise ValueError('รูปแบบ: เชื่อม U-000001 กับ FU-260910-0010 หรือ ย้อนการเชื่อม U-000001')
    code = (match or undo)[1].upper()
    row = _get(db,'rag_review:'+code)
    if not row:
        raise ValueError('ไม่พบข้อความรอเชื่อมค่ะ')
    old_value = row.value
    data = json.loads(old_value)
    message = db.scalar(select(Message).where(Message.line_message_id==data['message_id']))
    if not message:
        raise ValueError('ไม่พบข้อความต้นฉบับค่ะ')
    task_code = match[2].upper() if match else data.get('task_code')
    task = db.scalar(select(Task).where(Task.task_code==task_code).with_for_update())
    if not task or task.group_id != message.source_id:
        raise ValueError('ต้องเป็นงานในกลุ่มเดียวกับข้อความต้นฉบับค่ะ')
    from service import (record_task_event, update_task_progress_snapshot, pending_commitment_at,
                         remember_commitment, ignore_old_progress, explicit_task_scope, message_commitment, utcnow)
    from followup_policy import normalize_task, load_policy
    if match:
        if data['state']=='linked':
            if data['task_code']==task_code:
                return f'{code} เชื่อมกับ {task_code} แล้วค่ะ ไม่มีการบันทึกซ้ำ'
            raise ValueError('ข้อความนี้เชื่อมงานอื่นแล้วค่ะ ต้องย้อนการเชื่อมก่อน')
        if task.status in ('COMPLETED','CANCELLED'):
            raise ValueError('งานปิดอยู่ค่ะ เจ้าของระบบต้องเปิดงานกลับมาก่อนเชื่อม')
        scope = explicit_task_scope(db,message.text)
        if scope is not None and (task not in scope or len({t.project or t.id for t in scope}) > 1):
            raise ValueError('ชื่อหรือรหัสงานในข้อความขัดกับงานที่เลือกค่ะ ยังไม่เชื่อม')
        duplicate = db.scalar(select(TaskEvent.id).where(TaskEvent.message_id==message.line_message_id,
            TaskEvent.event_type.in_(['PROGRESS_UPDATE','COMPLETION_CONFIRMATION','QUOTED_STATUS_REPLY'])).limit(1))
        if duplicate:
            raise ValueError('ข้อความนี้มีการอัปเดตงานผ่านช่องทางเดิมแล้วค่ะ กรุณาตรวจประวัติก่อน')
        before = _snapshot(task)
        promise = pending_commitment_at(db,task)
        data.update(before=before, previous_commitment=promise.isoformat() if promise else None)
        # Explicit manager linking does not turn a vague answer into completion.
        from intent_engine import classify_message_intent, is_goods_waiting_update, is_procurement_waiting_update
        from service import derive_progress_snapshot, get_forced_followup_config
        intent = classify_message_intent(message.text).intent
        stale = ignore_old_progress(db,task,message.line_message_id)
        applied = False
        if not stale and intent in ('PROGRESS_UPDATE','NOT_COMPLETED'):
            waiting = is_goods_waiting_update(message.text) or is_procurement_waiting_update(message.text) or bool(derive_progress_snapshot(message.text)['waiting_on'])
            task.status = 'WAITING' if waiting else 'IN_PROGRESS'
            forced = get_forced_followup_config(db,task)
            hours = float(forced.get('interval_hours',2)) if forced else (24 if waiting else 6)
            task.next_reminder_at = message_commitment(db,message.text,message.line_message_id) or utcnow()+timedelta(hours=hours)
            update_task_progress_snapshot(db,task,message.text,actor_name=message.display_name,
                actor_user_id=message.user_id,message_id=message.line_message_id,commit=False)
            normalize_task(db,task,load_policy(db))
            applied = True
        record_task_event(db,task,'MANAGER_LINKED_MESSAGE',actor_name=message.display_name,
            actor_user_id=message.user_id,message_id=message.line_message_id,text=message.text,commit=False)
        audit = record_task_event(db,task,'MANAGER_LINK_AUDIT',actor_user_id=uid,
            text=json.dumps({'review':code,'source_message_id':message.line_message_id,'applied':applied,'stale':stale}),commit=False)
        db.flush()
        data.update(state='linked',task_code=task_code,after=_snapshot(task),audit_id=audit.id,manager_user_id=uid,applied=applied)
        result = f'เชื่อม {code} กับ {task_code} แล้วค่ะ\nผู้ตอบต้นฉบับ: {message.display_name or "-"}\n'
        result += 'ปรับความคืบหน้าและคิวติดตามแล้ว ไม่ปิดงานค่ะ' if applied else 'บันทึกข้อความในประวัติงานแล้ว ยังไม่เปลี่ยนสถานะหรือทับความคืบหน้าปัจจุบันค่ะ'
    else:
        if data['state']!='linked':
            raise ValueError('ข้อความนี้ยังไม่มีการเชื่อมที่ย้อนกลับได้ค่ะ')
        latest = db.scalar(select(TaskEvent.id).where(TaskEvent.task_id==task.id).order_by(TaskEvent.id.desc()).limit(1))
        if latest != data['audit_id'] or _snapshot(task)!=data['after']:
            raise ValueError('งานมีข้อมูลใหม่หลังเชื่อมแล้วค่ะ ไม่ย้อนทับข้อมูลใหม่')
        for key,value in data['before'].items():
            setattr(task,key,datetime.fromisoformat(value) if key.endswith('_at') and value else value)
        promise = data['previous_commitment']
        remember_commitment(db,task,datetime.fromisoformat(promise) if promise else None)
        normalize_task(db,task,load_policy(db))
        record_task_event(db,task,'MANAGER_LINK_UNDONE',actor_user_id=uid,text=json.dumps({'review':code,'audit_id':data['audit_id']}),commit=False)
        data['state']='pending'
        result=f'ย้อนการเชื่อม {code} แล้วค่ะ ข้อความกลับเข้ารายการรอเชื่อม'
    # CAS plus task lock prevents concurrent linking to two different tasks.
    changed=db.execute(update(OwnerPreference).where(OwnerPreference.id==row.id,OwnerPreference.value==old_value)
        .values(value=json.dumps(data,ensure_ascii=False)),execution_options={'synchronize_session':False})
    if changed.rowcount!=1:
        raise ValueError('ข้อความนี้ถูกแก้ไขพร้อมกันค่ะ กรุณาอ่านรายการล่าสุดก่อนลองใหม่')
    db.expire(row)
    return result
