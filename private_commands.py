"""Private commands. Return response text; the caller sends only to the requesting user."""
import re
from sqlalchemy import select
from models import Person, Task
from learning import (can_manage, is_owner, manager_ids, resolve_person, set_manager,
                      person_profile, set_learning_enabled, global_enabled, clear_learning,
                      set_preferred_window, reassign_task)

PREFIXES = ('ดูรูปแบบการตอบของ', 'ดูข้อมูลการเรียนรู้', 'เปิดการเรียนรู้', 'ปิดการเรียนรู้',
            'หยุดเรียนรู้', 'เริ่มเรียนรู้', 'ล้างข้อมูลการเรียนรู้ของ', 'ตั้งเวลาติดตาม',
            'ล้างเวลาติดตาม', 'เพิ่มผู้จัดการ', 'ลบผู้จัดการ', 'ดูรายชื่อผู้จัดการ',
            'เปลี่ยนผู้รับผิดชอบ', 'คำสั่งเรียนรู้', 'ข้อความแจ้งการเรียนรู้')


def is_management_command(text):
    raw = (text or '').strip()
    return raw.startswith(PREFIXES) or 'สะดวกให้ติดตามช่วง' in raw


def _person(db, name):
    people = resolve_person(db, name)
    if not people:
        raise ValueError(f'ยังไม่พบชื่อ {name} ในทะเบียนค่ะ ให้บุคคลนั้นส่งข้อความในกลุ่มและผูกชื่อกับบัญชี LINE ก่อน')
    if len(people) != 1:
        raise ValueError('ชื่อนี้ตรงกับหลายคนค่ะ กรุณาระบุชื่อมาตรฐานให้ชัดเจน: ' + ', '.join(p.canonical_name for p in people))
    person = people[0]
    if not person.line_user_id:
        raise ValueError(f'{person.canonical_name} ยังไม่ได้ผูกบัญชี LINE ค่ะ จึงยังระบุบุคคลไม่ได้แน่นอน')
    return person


def _clock(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _profile_text(db, person):
    p = person_profile(db, person.line_user_id)
    lines = [f'รูปแบบการตอบงานของ {person.canonical_name}',
             f'การเรียนรู้: {"เปิด" if p["learning_active"] else "หยุด"}',
             f'ตัวอย่างใน {p["retention_days"]} วันล่าสุด: {p["sample_count"]} ข้อความ / {p["distinct_days"]} วัน / {p["task_count"]} งาน']
    if p['sample_count']:
        lines.append(f'ความยาวคำตอบค่ากลาง: {p["median_chars"]:g} ตัวอักษร (ไม่รวมช่องว่าง)')
        lines.append(f'อัปเดตที่ระบุวันนัดในอนาคต: {p["dated_updates"]} ข้อความ')
    if p['common_hour'] is not None:
        lines.append(f'ช่วงที่พบการตอบบ่อยในเวลางาน: {p["common_hour"]:02d}:00–{p["common_hour"]+1:02d}:00 ({p["common_hour_share"]:.0%} ของ {p["work_window_samples"]} ตัวอย่างในเวลางาน)')
    if p['timed_samples']:
        lines.append(f'เวลาห่างจากการเตือนล่าสุดค่ากลาง: {p["median_minutes_since_reminder"]:g} นาที จาก {p["timed_samples"]} ตัวอย่าง ไม่ใช่เวลาทำงานหรือคะแนนผลงาน')
    if p['preferred_window']:
        start,end = p['preferred_window']
        lines.append(f'เวลาที่ผู้จัดการกำหนด: {_clock(start)}–{_clock(end)}')
    elif p['schedule_evidence_sufficient'] and p['learning_active']:
        lines.append('หลักฐานพอทดลองปรับเวลาติดตามปกติ: เลื่อนช้าลงได้ไม่เกิน 2 ชั่วโมงในวันเดิม')
    else:
        lines.append('ยังไม่ปรับเวลาจากสถิติ: ต้องมีอย่างน้อย 8 ตัวอย่างในเวลางาน จาก 3 วัน และอย่างน้อย 60% อยู่ในช่วงชั่วโมงเดียวกัน โดยเปิดการเรียนรู้ไว้')
    lines.append('เวลาในรายงานเป็นเวลาท้องถิ่นของระบบ วันนัด งานมีกำหนดส่ง งานเลยกำหนด และโหมดบังคับติดตามมีลำดับก่อนการปรับเวลารายบุคคล')
    lines.append('เป็นสถิติการตอบงาน ไม่ใช่ข้อสรุปนิสัย ความสะดวก หรือความรับผิดชอบค่ะ')
    return '\n'.join(lines)


def execute_private_command(db, uid, text):
    raw = (text or '').strip()
    if not is_management_command(raw):
        return None
    if not can_manage(db, uid):
        raise PermissionError('คำสั่งนี้ใช้ในแชตส่วนตัวได้เฉพาะเจ้าของระบบหรือผู้จัดการที่ได้รับสิทธิ์ค่ะ')
    if raw == 'คำสั่งเรียนรู้':
        return ('คำสั่งส่วนตัว\nดูรูปแบบการตอบของ ตี๋\nดูข้อมูลการเรียนรู้\nหยุดเรียนรู้ ตี๋\nเริ่มเรียนรู้ ตี๋\nล้างข้อมูลการเรียนรู้ของ ตี๋\n'
                'ตั้งเวลาติดตาม ตี๋ 14:00-16:00\nล้างเวลาติดตาม ตี๋\nเปลี่ยนผู้รับผิดชอบ FU-xxxxxx-xxxx เป็น ตี๋\n'
                'ดูรายชื่อผู้จัดการ\nเจ้าของระบบเท่านั้น: เพิ่มผู้จัดการ ชื่อ / ลบผู้จัดการ ชื่อ / เปิดการเรียนรู้ / ปิดการเรียนรู้')
    if raw == 'ข้อความแจ้งการเรียนรู้':
        return ('ข้อความสำหรับนำไปแจ้งทีม:\nเลขาจะใช้เฉพาะอัปเดตที่เชื่อมกับงานได้ เพื่อจำขั้นตอนงานและสรุปรูปแบบการตอบ '
                'เช่น เวลาที่ตอบและความยาวคำตอบ เพื่อปรับการติดตาม ไม่ใช้ประเมินนิสัยหรือคะแนนผลงาน '
                'ข้อมูลการเรียนรู้เก็บไม่เกิน 90 วัน/200 ตัวอย่างต่อบัญชี ผู้จัดการดู หยุด หรือล้างข้อมูลนี้ได้ '
                'ประวัติงานเดิมยังคงอยู่ตามระบบค่ะ\n\nยังไม่ได้ส่งข้อความนี้เข้ากลุ่มค่ะ')
    if raw == 'ดูข้อมูลการเรียนรู้':
        return ('การเรียนรู้ทั้งระบบ: ' + ('เปิด' if global_enabled(db) else 'หยุด') +
                '\nเรียนรู้จากอัปเดตงานที่บันทึกสำเร็จและบัญชีผู้ตอบตรงกับผู้รับผิดชอบเท่านั้น เริ่มเก็บตั้งแต่รุ่นนี้ ไม่ย้อนอ่านแชตทั่วไป'
                '\nใช้ ดูรูปแบบการตอบของ ชื่อ เพื่อดูจำนวนหลักฐานและการตั้งเวลา'
                '\nเก็บสถิติสูงสุด 200 ตัวอย่าง/90 วันต่อบัญชี; ไม่เก็บข้อความดิบเพิ่มในข้อมูลการเรียนรู้')
    if raw in ('เปิดการเรียนรู้', 'ปิดการเรียนรู้'):
        enabled = raw == 'เปิดการเรียนรู้'
        set_learning_enabled(db, uid, enabled)
        return ('เปิด' if enabled else 'หยุด') + 'การเก็บและใช้สถิติรายบุคคลทั้งระบบแล้วค่ะ เวลาที่กำหนดเองยังคงอยู่'
    if raw == 'ดูรายชื่อผู้จัดการ':
        people = list(db.scalars(select(Person).where(Person.line_user_id.in_(manager_ids(db)), Person.active.is_(True))).all())
        return 'ผู้จัดการที่ได้รับสิทธิ์:\n' + ('\n'.join(p.canonical_name for p in people) if people else 'ยังไม่มี (เจ้าของระบบยังใช้ได้ตามเดิม)')
    for prefix, enabled in [('เพิ่มผู้จัดการ ',True),('ลบผู้จัดการ ',False)]:
        if raw.startswith(prefix):
            if not is_owner(uid):
                raise PermissionError('เฉพาะเจ้าของระบบเท่านั้นที่เพิ่มหรือลบผู้จัดการได้ค่ะ')
            person = _person(db,raw[len(prefix):])
            set_manager(db,uid,person,enabled)
            return ('เพิ่มสิทธิ์' if enabled else 'ถอนสิทธิ์') + f'ผู้จัดการ {person.canonical_name} แล้วค่ะ'
    if raw.startswith('ดูรูปแบบการตอบของ '):
        return _profile_text(db,_person(db,raw[len('ดูรูปแบบการตอบของ '):]))
    for prefix,enabled in [('หยุดเรียนรู้ ',False),('เริ่มเรียนรู้ ',True)]:
        if raw.startswith(prefix):
            person = _person(db,raw[len(prefix):])
            set_learning_enabled(db,uid,enabled,person)
            return ('เริ่ม' if enabled else 'หยุด') + f'การเก็บและใช้สถิติของ {person.canonical_name} แล้วค่ะ'
    if raw.startswith('ล้างข้อมูลการเรียนรู้ของ '):
        person = _person(db,raw[len('ล้างข้อมูลการเรียนรู้ของ '):])
        clear_learning(db,uid,person)
        return f'ล้างตัวอย่างการเรียนรู้ของ {person.canonical_name} แล้วค่ะ ไม่ดึงตัวอย่างเก่ากลับมา ประวัติงานและเวลาที่กำหนดเองยังอยู่'
    if raw.startswith('ล้างเวลาติดตาม '):
        person = _person(db,raw[len('ล้างเวลาติดตาม '):])
        set_preferred_window(db,uid,person)
        return f'ล้างเวลาที่กำหนดเองของ {person.canonical_name} แล้วค่ะ กลับไปใช้กติกาปกติและสถิติเมื่อหลักฐานเพียงพอ'
    window = re.fullmatch(r'(?:ตั้งเวลาติดตาม\s+(.+?)\s+|(.+?)\s*สะดวกให้ติดตามช่วง\s*)(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})',raw)
    if window:
        person = _person(db,window.group(1) or window.group(2))
        h1,m1,h2,m2 = map(int,window.groups()[2:])
        if h1>23 or h2>23 or m1>59 or m2>59:
            raise ValueError('รูปแบบเวลาไม่ถูกต้องค่ะ เช่น 14:00-16:00')
        set_preferred_window(db,uid,person,h1*60+m1,h2*60+m2)
        return f'ตั้งเวลาติดตามปกติของ {person.canonical_name} เป็น {_clock(h1*60+m1)}–{_clock(h2*60+m2)} แล้วค่ะ มีผลเมื่อจัดคิวรอบถัดไป โดยยังเคารพวันนัด กำหนดส่ง และโหมดบังคับติดตาม'
    if raw.startswith('เปลี่ยนผู้รับผิดชอบ'):
        match = re.fullmatch(r'เปลี่ยนผู้รับผิดชอบ\s+(FU-\d{6}-\d{4,})\s+เป็น\s+(.+?)(?:\s+เพราะ\s*(.+))?',raw,re.I)
        if not match:
            raise ValueError('ระบุรหัสงานและชื่อค่ะ เช่น เปลี่ยนผู้รับผิดชอบ FU-260924-0012 เป็น ตี๋ เพราะจอยส่งต่องานแล้ว')
        code,name,reason=match.groups()
        task = db.scalar(select(Task).where(Task.task_code == code.upper()))
        if not task:
            raise ValueError(f'ไม่พบงาน {code.upper()} ค่ะ ยังไม่ได้เปลี่ยนผู้รับผิดชอบ')
        person = _person(db,name)
        changed,old = reassign_task(db,uid,task,person,reason or '')
        if not changed:
            return f'{task.task_code} มี {person.canonical_name} เป็นผู้รับผิดชอบอยู่แล้วค่ะ'
        return (f'เปลี่ยนผู้รับผิดชอบแล้วค่ะ\n{task.task_code} {task.title}\nจาก: {old or "ยังไม่ระบุ"} → {person.canonical_name}'
                '\nคงสถานะ ความคืบหน้า และวันนัดเดิม ไม่ส่งประกาศการเปลี่ยนคนเข้ากลุ่ม'
                '\nเมื่อถึงรอบติดตาม เลขาจะแท็กคนใหม่ในกลุ่มค่ะ')
    raise ValueError('รูปแบบคำสั่งยังไม่ครบค่ะ พิมพ์ คำสั่งเรียนรู้ เพื่อดูตัวอย่าง')
