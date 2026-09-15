import re
from dataclasses import dataclass
from typing import Literal

IntentName = Literal[
    "NEW_TASK", "STATUS_QUERY", "FOLLOW_UP", "PROGRESS_UPDATE",
    "COMPLETION_CONFIRMATION", "NOT_COMPLETED", "CANCEL_REQUEST", "OTHER"
]

@dataclass(frozen=True)
class IntentResult:
    intent: IntentName
    confidence: float
    reason: str


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


def _has_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(t in value for t in terms)


QUESTION_PATTERNS = (
    "หรือยัง", "รึยัง", "ไหม", "มั้ย", "หรือไม่", "หรือเปล่า", "รึเปล่า",
    "เป็นยังไง", "เป็นอย่างไร", "ถึงไหน", "อะไรบ้าง", "ได้หรือยัง", "ส่งหรือยัง",
    "ได้รับหรือยัง", "มีความคืบหน้า", "อัปเดตหน่อย", "ขออัปเดต", "ตามเรื่อง",
)
NEGATION_PATTERNS = (
    "ยังไม่", "ไม่ได้", "ไม่เสร็จ", "ไม่เรียบร้อย", "ยังไม่ได้ดำเนินการ",
    "ยังไม่ได้ส่ง", "ยังไม่ได้รับ", "รออยู่", "รอดำเนินการ", "ติดปัญหา", "ยังแก้ไม่ได้",
    "ยังไม่ตอบ", "ยังไม่ตอบรับ", "รอตอบกลับ", "รอเขาตอบ", "รอเค้าตอบ",
)
FOLLOWUP_PATTERNS = (
    "ช่วยตาม", "ติดตาม", "ตามเรื่อง", "ขออัปเดต", "อัปเดตหน่อย", "ช่วยถาม",
    "ถ้าเรียบร้อยแล้วแจ้ง", "ถ้าเสร็จแล้วแจ้ง", "แจ้งด้วย",
)
CANCEL_PATTERNS = ("ยกเลิกงาน", "ไม่ต้องทำแล้ว", "ไม่ต้องตามแล้ว", "ยกเลิกเรื่องนี้", "ปิดเรื่องไม่ทำต่อ")
WAITING_PATTERNS = ("รอ", "รอตอบ", "รออนุมัติ", "รอของ", "รอข้อมูล", "รอเซลล์", "รอเจ้าหน้าที่")

# Clear whole-task completion phrases. Note that a bare "เรียบร้อย" is intentionally absent.
COMPLETION_PATTERNS = (
    "เรื่องนี้เรียบร้อยแล้ว", "งานนี้เรียบร้อยแล้ว", "ดำเนินการเรียบร้อยแล้ว",
    "ดำเนินการครบแล้ว", "ทำเสร็จเรียบร้อยแล้ว", "งานนี้เสร็จแล้ว", "เรื่องนี้เสร็จแล้ว",
    "ปิดงานได้เลย", "แก้ไขเรียบร้อยแล้ว", "เสร็จสมบูรณ์แล้ว", "ตรวจแล้วไม่มีปัญหา",
)

# Action + object + finality can also be a clear confirmation, e.g. "จ่ายค่าประกันเรียบร้อย".
ACTION_FINALITY_RE = re.compile(
    r"(?:จ่าย|ชำระ|ต่อ|แก้ไข|ติดตั้ง|ตรวจสอบ|ตรวจ|ดำเนินการ)[^?]{2,80}(?:เรียบร้อยแล้ว|เสร็จแล้ว|เรียบร้อย)$",
    re.I,
)

# Common milestones that may finish one step without finishing the parent task.
MILESTONE_PATTERNS = (
    "ส่งdatasheet", "ส่งเอกสาร", "ส่งเมล", "ส่งอีเมล", "เปิดpo", "เปิดพีโอ",
    "ขอเบอร์", "ประสานแล้ว", "แจ้งแล้ว", "ส่งข้อมูลแล้ว", "ยื่นแล้ว",
)

PROGRESS_PATTERNS = (
    "กำลัง", "ดำเนินการอยู่", "กำลังตรวจ", "กำลังเช็ก", "กำลังเช็ค", "กำลังประสาน",
    "ส่งแล้ว", "ส่งเรียบร้อย", "เปิดpoแล้ว", "เปิดพีโอแล้ว", "ยื่นแล้ว", "แจ้งแล้ว",
)


def classify_message_intent(text: str | None) -> IntentResult:
    raw = (text or "").strip()
    value = _compact(raw)
    if not value:
        return IntentResult("OTHER", 1.0, "empty")

    # Hard safety: question intent always beats completion words.
    if "?" in raw or "？" in raw or _has_any(value, QUESTION_PATTERNS):
        # Conditional reminder phrases are follow-ups, not completion confirmations.
        if _has_any(value, ("ถ้าเรียบร้อยแล้วแจ้ง", "ถ้าเสร็จแล้วแจ้ง")):
            return IntentResult("FOLLOW_UP", 0.99, "conditional_followup")
        return IntentResult("STATUS_QUERY", 0.99, "question_pattern")

    if _has_any(value, CANCEL_PATTERNS):
        return IntentResult("CANCEL_REQUEST", 0.98, "cancel_pattern")

    # Hard safety: negation/waiting beats any completion keyword in the same sentence.
    if _has_any(value, NEGATION_PATTERNS):
        if _has_any(value, WAITING_PATTERNS) or _has_any(value, MILESTONE_PATTERNS):
            return IntentResult("PROGRESS_UPDATE", 0.98, "negated_or_waiting_progress")
        return IntentResult("NOT_COMPLETED", 0.99, "negation_pattern")

    if _has_any(value, FOLLOWUP_PATTERNS):
        return IntentResult("FOLLOW_UP", 0.96, "followup_pattern")

    # Milestones are progress even if they contain "แล้ว" or "เรียบร้อย".
    if _has_any(value, MILESTONE_PATTERNS):
        return IntentResult("PROGRESS_UPDATE", 0.96, "milestone_not_whole_task")

    if _has_any(value, COMPLETION_PATTERNS) or ACTION_FINALITY_RE.search(raw):
        return IntentResult("COMPLETION_CONFIRMATION", 0.97, "explicit_completion")

    # Bare completion-ish words are deliberately not enough to close a task.
    if value in {"เรียบร้อย", "เสร็จ", "เสร็จแล้ว", "แล้ว", "ดำเนินการแล้ว", "ส่งแล้ว"}:
        return IntentResult("PROGRESS_UPDATE", 0.82, "bare_completion_word_not_safe")

    if _has_any(value, PROGRESS_PATTERNS):
        return IntentResult("PROGRESS_UPDATE", 0.90, "progress_pattern")

    return IntentResult("OTHER", 0.50, "no_deterministic_intent")


def intent_to_status_signal(intent: str) -> str:
    if intent == "COMPLETION_CONFIRMATION":
        return "completed"
    if intent in {"PROGRESS_UPDATE", "NOT_COMPLETED"}:
        return "in_progress"
    return "none"
