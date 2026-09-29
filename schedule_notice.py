"""Explicit private rescheduling with opt-in public notice after persistence."""
import re
from zoneinfo import ZoneInfo
from config import settings
from learning import can_manage
from service import (get_task_by_code, extract_followup_commitment_at, remember_commitment,
                     record_task_event, link_outbound_task_message)


def local_time(value):
    return value.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo(settings.timezone)).strftime('%d/%m/%Y %H:%M')


async def reschedule(factory, push, uid, raw):
    with factory() as db:
        if not can_manage(db,uid):
            await push(uid,'คำสั่งเลื่อนติดตามใช้ส่วนตัวได้เฉพาะเจ้าของระบบหรือผู้จัดการที่ได้รับสิทธิ์ค่ะ')
            return
        match = re.fullmatch(r'เลื่อนติดตาม\s+(FU-\d{6}-\d{4,})\s+(.+)',raw.strip(),re.I|re.S)
        if not match:
            await push(uid,'รูปแบบ: เลื่อนติดตาม FU-xxxxxx-xxxx 10/10/2569 08:30 แจ้งกลุ่ม (ใส่ แจ้งกลุ่ม เมื่อให้แจ้งทีมด้วย)')
            return
        code,when_text=match[1].upper(),match[2].strip()
        public=bool(re.search(r'\s+แจ้งกลุ่ม$',when_text))
        if public:
            when_text=re.sub(r'\s+แจ้งกลุ่ม$','',when_text).strip()
        if 'แจ้งกลุ่ม' in when_text:
            await push(uid,'หากต้องการแจ้งทีม ให้เติมคำว่า แจ้งกลุ่ม ท้ายคำสั่งค่ะ หากต้องการส่วนตัวไม่ต้องเติมคำนี้')
            return
        requested=extract_followup_commitment_at(when_text,clamp_to_work_window=False)
        if not requested:
            await push(uid,'ยังอ่านวัน/เวลาที่ต้องการไม่ได้ค่ะ เช่น พรุ่งนี้ 14:00 หรือ 10/10/2569 08:30')
            return
        task=get_task_by_code(db,code)
        if not task:
            await push(uid,f'ไม่พบงาน {code} ค่ะ')
            return
        if task.status in ('COMPLETED','CANCELLED'):
            await push(uid,f'{code} ปิดอยู่ค่ะ ให้เจ้าของเปิดงานกลับมาก่อนเลื่อนติดตาม')
            return
        if public and not task.group_id:
            await push(uid,'งานนี้ไม่มีข้อมูลกลุ่มค่ะ ยังไม่ได้เลื่อนหรือส่งแจ้งกลุ่ม')
            return
        task.next_reminder_at=requested
        remember_commitment(db,task,requested)
        record_task_event(db,task,'OWNER_FOLLOWUP_RESCHEDULED',actor_user_id=uid,
                          text=f'เลื่อนติดตาม: {when_text}; แจ้งกลุ่ม={public}',commit=False)
        # ORM applies the global calendar. Do not publish the pre-normalized date.
        db.commit();db.refresh(task)
        actual=task.next_reminder_at
        task_id,group_id,title=task.id,task.group_id,task.title
    outcome=''
    if public:
        title=' '.join((title or '').split())[:500]
        notice=f'แจ้งวันติดตามใหม่ค่ะ\n{code} — {title}\nเลขาจะติดตามอีกครั้งวันที่ {local_time(actual)} ค่ะ'
        if actual!=requested:
            notice+=f'\nจากวันที่ขอ {local_time(requested)} ปรับตามวันและเวลาทำการที่กำหนดค่ะ'
        notice+='\nจะไม่ส่งเตือนติดตามก่อนวันนัดนี้ค่ะ'
        sent_id=None
        try:
            sent_id=await push(group_id,notice)
            outcome='แจ้งวันติดตามใหม่ในกลุ่มแล้วค่ะ' if sent_id else 'บันทึกวันใหม่แล้ว แต่ยังยืนยันการส่งแจ้งกลุ่มไม่ได้ค่ะ กรุณาตรวจในกลุ่มก่อนสั่งซ้ำ'
        except Exception as exc:
            # A timeout can occur after delivery; do not retry automatically.
            print('schedule notice delivery unconfirmed:',type(exc).__name__)
            outcome='บันทึกวันใหม่แล้ว แต่ยังยืนยันการส่งแจ้งกลุ่มไม่ได้ค่ะ กรุณาตรวจในกลุ่มก่อนสั่งซ้ำ'
        try:
            with factory() as db:
                task=get_task_by_code(db,code)
                if sent_id:
                    link_outbound_task_message(db,line_message_id=sent_id,task_id=task_id,group_id=group_id,
                                               message_kind='SCHEDULE_NOTICE',commit=False)
                record_task_event(db,task,'GROUP_SCHEDULE_NOTICE_SENT' if sent_id else 'GROUP_SCHEDULE_NOTICE_UNCONFIRMED',
                                  actor_user_id=uid,text=notice,message_id=sent_id,commit=False)
                db.commit()
        except Exception as exc:
            print('schedule notice audit failed:',type(exc).__name__)
            if sent_id:
                outcome+=' แต่บันทึกการเชื่อม Reply ของข้อความแจ้งไม่สำเร็จค่ะ'
    await push(uid,f'เลื่อนติดตาม {code} แล้วค่ะ\nครั้งถัดไป: {local_time(actual)}'+('\n'+outcome if outcome else ''))
