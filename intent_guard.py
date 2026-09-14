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
