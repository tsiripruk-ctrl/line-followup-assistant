import re
from dataclasses import dataclass


@dataclass(frozen=True)
class IntentGuardResult:
    block_task_creation: bool
    reason: str | None = None


# These notices are informational/administrative status messages, not operational
# work items for the follow-up bot. Keep the list intentionally narrow and pair it
# with action-request detection so that real tasks about leave paperwork still pass.
_LEAVE_NOTICE_PATTERNS = [
    r"(?:^|[\s,])(?:วันนี้|พรุ่งนี้|มะรืน|ช่วงเช้า|ช่วงบ่าย)?(?:ขออนุญาต|ขอ|แจ้ง)?\s*ลา(?:กิจ|ป่วย|พักร้อน|คลอด|อุปสมบท|ดูแลครอบครัว)",
    r"(?:^|[\s,])(?:วันนี้|พรุ่งนี้|มะรืน|ช่วงเช้า|ช่วงบ่าย)\s*ลา(?:\s|$)",
    r"(?:ไม่เข้าทำงาน|ไม่ได้เข้าทำงาน|ไม่มาทำงาน|หยุดงาน|มาสาย|ขอกลับก่อน|กลับก่อน)",
]

# Strong request/action wording means the message may be assigning real work,
# even when the subject is leave/attendance (e.g. "ช่วยทำใบลาให้บอส").
_ACTION_REQUEST_PATTERNS = [
    r"(?:ช่วย|รบกวน|ฝาก|ขอให้|ให้)\s*(?:ทำ|จัดทำ|เตรียม|ส่ง|ยื่น|ตรวจ|ตรวจสอบ|เช็ก|เช็ค|อนุมัติ|เปิด|แก้|ดำเนินการ|ประสาน|ติดตาม|ตาม|ออก|บันทึก)",
    r"(?:ทำ|จัดทำ|เตรียม|ส่ง|ยื่น|ตรวจ|ตรวจสอบ|เช็ก|เช็ค|อนุมัติ|เปิด|แก้|ดำเนินการ|ประสาน|ติดตาม|ตาม|ออก|บันทึก)\s*(?:ใบลา|คำขอลา|เอกสารลา|รายการลา|ข้อมูลลา)",
    r"(?:อนุมัติ|พิจารณา)\s*(?:การ)?ลา",
]


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_action_request(text: str | None) -> bool:
    value = _norm(text)
    if not value:
        return False
    return any(re.search(p, value, flags=re.IGNORECASE) for p in _ACTION_REQUEST_PATTERNS)


def classify_precreation_guard(text: str | None) -> IntentGuardResult:
    """Deterministic guard executed before the LLM may auto-create a task.

    Goal: prevent ordinary leave/attendance notices from becoming FU tasks while
    preserving real assignments such as "ช่วยทำใบลาให้บอส" or "อนุมัติการลาให้...".
    The function never changes existing tasks; it only controls new auto-creation.
    """
    value = _norm(text)
    if not value:
        return IntentGuardResult(False, None)

    if is_action_request(value):
        return IntentGuardResult(False, None)

    if any(re.search(p, value, flags=re.IGNORECASE) for p in _LEAVE_NOTICE_PATTERNS):
        return IntentGuardResult(True, "leave_or_attendance_notice")

    return IntentGuardResult(False, None)

# v0.6.36: PASSIVE CONVERSATION / AUTO-CREATION GUARD
# Ordinary work chatter and factual reports must not create a new FU or schedule
# follow-up unless the message contains a clear assignment/request signal.
_EXPLICIT_WORK_REQUEST_PATTERNS = [
    r"(?:^|[\s@])(?:ช่วย|รบกวน|ฝาก|ขอให้|ให้)\s*(?:ตรวจ|ตรวจสอบ|เช็ก|เช็ค|ดู|ทำ|จัดทำ|ส่ง|ยื่น|แก้|แก้ไข|ดำเนินการ|ประสาน|ติดต่อ|ติดตาม|ตาม|จัดการ|เตรียม|เปิด|ปิด|สั่ง|ซื้อ|ขอ|ออก|นัด|เข้าไป)",
    # Natural manager phrasing: 'ต้องให้ประสาน...', 'ต้องประสาน...', 'ต้องให้เข้าไปดำเนินการ...'
    r"(?:^|[\s@])ต้อง(?:การ)?\s*(?:ให้\s*)?(?:ตรวจ|ตรวจสอบ|เช็ก|เช็ค|ดู|ทำ|จัดทำ|ส่ง|ยื่น|แก้|แก้ไข|ดำเนินการ|ประสาน|ติดต่อ|ติดตาม|ตาม|จัดการ|เตรียม|เปิด|ปิด|สั่ง|ซื้อ|ขอ|ออก|นัด|เข้าไป)",
    r"(?:^|[\s@])(?:ติดตามงาน|งานใหม่|มอบหมายงาน|อัปเดตงาน|อัพเดตงาน|ปิดงาน)\s*[:：]",
    r"(?:เป็นผู้รับผิดชอบ|รับผิดชอบเรื่อง|มอบหมายให้)",
]

# Common conversational/reporting forms. These are evidence that the speaker is
# describing an existing situation, not assigning a new trackable task.
_PASSIVE_REPORT_PATTERNS = [
    r"^(?:ตอนนี้|ล่าสุด|เมื่อกี้|เมื่อวาน|วันนี้)?\s*(?:เขา|เค้า|ผม|ฉัน|เรา|ทาง|ช่าง|เซลล์|เจ้าหน้าที่|เทศบาล|ลูกค้า)\b",
    r"(?:เขา|เค้า)\s*(?:ให้|แจ้ง|บอก|ขอ|ตอบ|ส่ง|แก้|เพิ่ม|พิมพ์)",
    r"(?:ไม่ได้|ยังไม่ได้|ตอนนี้|ล่าสุด).*(?:ครับ|ค่ะ|คะ)$",
    r"(?:แค่|เพียง|ส่วน|แล้วก็|ซึ่ง|แต่|เพราะ|เลย)\s*.*(?:ครับ|ค่ะ|คะ)$",
]


def has_explicit_work_request(text: str | None) -> bool:
    value = _norm(text)
    if not value:
        return False
    return any(re.search(p, value, flags=re.IGNORECASE) for p in _EXPLICIT_WORK_REQUEST_PATTERNS)


def looks_like_passive_conversation(text: str | None) -> bool:
    """Return True for informational work chatter that should not auto-create FU.

    This deliberately does not classify exact quote replies or recent reminder
    replies; callers should resolve those continuity paths before using this guard.
    """
    value = _norm(text)
    if not value:
        return False
    if has_explicit_work_request(value):
        return False
    # Questions/follow-ups are routed elsewhere and are not "new task" creation.
    if re.search(r"(?:หรือยัง|ไหม|มั้ย|ถึงไหน|เป็นยังไง|เป็นอย่างไร|มีความคืบหน้า)", value, flags=re.IGNORECASE):
        return False
    return any(re.search(p, value, flags=re.IGNORECASE) for p in _PASSIVE_REPORT_PATTERNS)
