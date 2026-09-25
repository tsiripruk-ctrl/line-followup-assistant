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




COMMAND_PREFIXES = (
    ("NEW_TASK", ("งานใหม่:", "งานใหม่：", "มอบหมายงาน:", "มอบหมายงาน：")),
    ("FOLLOW_UP", ("ติดตามงาน:", "ติดตามงาน：")),
    ("PROGRESS_UPDATE", ("อัปเดตงาน:", "อัพเดตงาน:", "อัปเดตงาน：", "อัพเดตงาน：")),
    ("COMPLETION_CONFIRMATION", ("ปิดงาน:", "ปิดงาน：")),
)

def parse_command_prefix(text: str | None):
    """Return (intent, cleaned_text, prefix) for explicit owner/user command prefixes.

    Prefixes are a hard routing signal and must beat question words such as "หรือยัง".
    Example: "งานใหม่: @Proud ส่งมอบงานแล้วหรือยัง" is NEW_TASK, not STATUS_QUERY.
    """
    raw = (text or "").strip()
    lowered = raw.lower()
    for intent, prefixes in COMMAND_PREFIXES:
        for prefix in prefixes:
            if lowered.startswith(prefix.lower()):
                cleaned = raw[len(prefix):].strip()
                return intent, cleaned, prefix
    return None, raw, None

DIRECT_TASK_REQUEST_PATTERNS = (
    "ฝากตรวจสอบ", "ช่วยตรวจสอบ", "รบกวนตรวจสอบ", "ฝากเช็ก", "ฝากเช็ค",
    "ช่วยเช็ก", "ช่วยเช็ค", "รบกวนเช็ก", "รบกวนเช็ค", "ช่วยดู", "ฝากดู",
    "ช่วยดำเนินการ", "ฝากดำเนินการ", "ช่วยแก้", "ฝากแก้", "ช่วยติดต่อ", "ฝากติดต่อ",
)

# v0.6.46: natural owner wording can assign a brand-new follow-up without
# sounding like a bot command.  These phrases are only promoted when the caller
# also confirms a real LINE @mention, so ordinary group chatter stays quiet.
NATURAL_ASSIGNMENT_PATTERNS = (
    "ฝากเรื่องงาน", "ฝากงาน", "ฝากเรื่อง", "มีเรื่องใหม่", "มีอีกเรื่อง",
    "มีเรื่องให้ช่วย", "มีเรื่องอยากให้ช่วย", "ฝากช่วยเรื่อง",
)
NATURAL_ASSIGNMENT_CONTEXT_PATTERNS = (
    "ความก้าวหน้า", "ความคืบหน้า", "ติดตาม", "ตามงาน", "อัปเดต", "อัพเดต",
    "ตรวจสอบ", "เช็ก", "เช็ค", "ดู", "ดำเนินการ", "ประสาน",
)

def is_natural_assignment_request(text: str | None) -> bool:
    """Detect natural human wording that assigns a new work item.

    Examples:
      - "@ตี๋ ฝากเรื่องงาน อบต หนองชิ่ม ในความก้าวหน้าของงานหน่อยนะ"
      - "@MARCH มีอีกเรื่อง ปากน้ำประแส ฝากดูความคืบหน้าให้หน่อย"

    The caller must additionally require a real LINE @mention.  This keeps the
    detector conservative and avoids creating tasks from casual chatter.
    """
    value = _compact(text)
    if not value:
        return False
    if "ฝากเรื่องงาน" in value or "ฝากงาน" in value:
        return True
    if not _has_any(value, NATURAL_ASSIGNMENT_PATTERNS):
        return False
    has_work_anchor = _has_any(value, tuple(_compact(x) for x in WORK_TOPIC_PATTERNS))
    has_tracking_context = _has_any(value, tuple(_compact(x) for x in NATURAL_ASSIGNMENT_CONTEXT_PATTERNS))
    return has_work_anchor or has_tracking_context

def is_direct_task_request(text: str | None) -> bool:
    """Return True when the sentence contains an explicit actionable request.

    This is deliberately narrower than generic question detection. A sentence can
    contain a question such as "ใช้งานไม่ได้หรือเปล่า" and still be a NEW_TASK
    when it ends with "ฝากตรวจสอบที".
    """
    value = _compact(text)
    if not value:
        return False
    # Natural assignment wording must be evaluated before the generic follow-up
    # exclusion.  "ฝากเรื่องงาน ... ความก้าวหน้า..." is an assignment even
    # though it contains progress language.
    if is_natural_assignment_request(text):
        return True
    if _has_any(value, ("ตามเรื่อง", "ขออัปเดต", "อัปเดตหน่อย", "มีความคืบหน้า", "ถึงไหน")):
        return False
    return _has_any(value, DIRECT_TASK_REQUEST_PATTERNS)



# v0.6.37: A direct @mention can introduce a brand-new work question even when
# the sentence is grammatically a question (e.g. "ค่าซ่อมรถตีราคาครบแล้วถูกไหม").
# This helper is intentionally conservative: it requires a recognizable work-topic
# anchor and rejects classic follow-up/status wording. The caller must additionally
# require a real LINE @mention and must search existing tasks first.
WORK_TOPIC_PATTERNS = (
    "งาน", "โครงการ", "ราคา", "ค่าบริการ", "ค่าซ่อม", "ซ่อม", "รถ",
    "เอกสาร", "หนังสือ", "ใบเสนอราคา", "ใบแจ้งหนี้", "invoice", "po", "พีโอ",
    "ส่งมอบ", "ส่งของ", "สินค้า", "อุปกรณ์", "สั่งซื้อ", "จัดซื้อ", "ติดตั้ง",
    "ระบบ", "เบอร์ออฟฟิต", "เบอร์ออฟฟิศ", "บัญชี", "flowaccount", "flow account",
    "กล้อง", "cctv", "มิเตอร์", "fiber", "ไฟเบอร์", "สัญญา", "ประกัน",
    "ชำระ", "จ่าย", "อนุมัติ", "ตรวจรับ", "datasheet", "ดาต้าชีท",
)

DIRECTED_NEW_WORK_QUESTION_EXCLUSIONS = (
    "ถึงไหน", "ความคืบหน้า", "ขออัปเดต", "อัปเดตหน่อย", "ตามเรื่อง",
    "ติดตาม", "สถานะ", "งานค้าง", "ล่าสุดเป็นยังไง", "ล่าสุดเป็นอย่างไร",
)

def is_directed_new_work_question(text: str | None) -> bool:
    """Return True for a work-topic question that may itself open a new task.

    Important: this does *not* decide creation by itself. In main.py it is used only
    when LINE metadata confirms a real @mention. Existing-task matching always runs
    first; only an unmatched question may fall through to NEW_TASK creation.
    """
    raw = (text or "").strip()
    value = _compact(raw)
    if not value:
        return False
    if _has_any(value, DIRECTED_NEW_WORK_QUESTION_EXCLUSIONS):
        return False
    has_question = ("?" in raw or "？" in raw or _has_any(value, QUESTION_PATTERNS))
    if not has_question:
        return False
    return _has_any(value, tuple(_compact(x) for x in WORK_TOPIC_PATTERNS))

def classify_message_intent(text: str | None) -> IntentResult:
    raw = (text or "").strip()
    forced_intent, cleaned, prefix = parse_command_prefix(raw)
    if forced_intent:
        return IntentResult(forced_intent, 1.0, f"explicit_command_prefix:{prefix}")
    value = _compact(raw)
    if not value:
        return IntentResult("OTHER", 1.0, "empty")

    # Explicit follow-up requests beat generic question-pattern words such as
    # "ตามเรื่อง" / "ขออัปเดต". They still never complete a task.
    if _has_any(value, FOLLOWUP_PATTERNS):
        return IntentResult("FOLLOW_UP", 0.99, "explicit_followup_pattern")

    # Hard safety: question intent always beats completion words.
    if "?" in raw or "？" in raw or _has_any(value, QUESTION_PATTERNS):
        return IntentResult("STATUS_QUERY", 0.99, "question_pattern")

    if _has_any(value, CANCEL_PATTERNS):
        return IntentResult("CANCEL_REQUEST", 0.98, "cancel_pattern")

    # Hard safety: negation/waiting beats any completion keyword in the same sentence.
    if _has_any(value, NEGATION_PATTERNS):
        if _has_any(value, WAITING_PATTERNS) or _has_any(value, MILESTONE_PATTERNS):
            return IntentResult("PROGRESS_UPDATE", 0.98, "negated_or_waiting_progress")
        return IntentResult("NOT_COMPLETED", 0.99, "negation_pattern")

    if re.search(r"(?:คาดว่า|ประมาณ|จะจัดส่ง|จะส่ง).*?\d{1,2}[/\-]\d{1,2}", raw) or re.search(r"รอ.+?(?:เฟิร์ม|ยืนยัน)", raw):
        return IntentResult("PROGRESS_UPDATE", 0.98, "future_commitment_or_confirmation")

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

# v0.6.34: Exact LINE quote/reply may safely confirm a whole-task completion
# with short natural phrases that are intentionally *not* safe in free chat.
QUOTED_COMPLETION_PATTERNS = (
    # Exact LINE quote/reply identifies the task, so short natural confirmations are safe.
    "เรียบร้อย", "เรียบร้อยแล้ว", "เสร็จ", "เสร็จแล้ว", "เสร็จเรียบร้อย", "เสร็จเรียบร้อยแล้ว",
    "จบ", "จบแล้ว", "ปิดได้", "ปิดได้เลย",
    "งานเรียบร้อย", "งานเรียบร้อยแล้ว", "งานเสร็จ", "งานเสร็จแล้ว",
    "ปิดงานได้", "ปิดงานได้เลย", "ดำเนินการเสร็จ", "ดำเนินการเสร็จแล้ว",
    "ดำเนินการเรียบร้อย", "ดำเนินการเรียบร้อยแล้ว", "เสร็จสมบูรณ์", "เสร็จสมบูรณ์แล้ว",
)


def is_safe_quoted_completion(text: str | None) -> bool:
    """Return True only for a clear completion reply to an *exactly quoted task*.

    This helper deliberately allows short replies like "เรียบร้อยแล้ว" only when
    the caller has already resolved the exact task from LINE quotedMessageId.
    Questions, negations/waiting language, and milestone-only updates always win.
    """
    raw = (text or "").strip()
    value = _compact(raw)
    if not value:
        return False

    # Hard safety rules always win, even inside an exact quote reply.
    if "?" in raw or "？" in raw or _has_any(value, QUESTION_PATTERNS):
        return False
    if _has_any(value, NEGATION_PATTERNS):
        return False
    if _has_any(value, MILESTONE_PATTERNS):
        return False

    # Accept common short whole-task confirmations, including polite suffixes.
    polite_trimmed = re.sub(r"(?:ครับ|ค่ะ|คะ|นะครับ|นะคะ|ครับผม)$", "", value)
    if polite_trimmed in {_compact(p) for p in QUOTED_COMPLETION_PATTERNS}:
        return True

    # Longer explicit whole-task confirmation remains safe too.
    return classify_message_intent(raw).intent == "COMPLETION_CONFIRMATION"


# v0.6.38: Clarification is opt-in, not a generic fallback.
def should_clarify_unmatched_query(text: str | None, intent: str, forced_command_intent: str | None = None) -> bool:
    """Return True only when the human explicitly asked the assistant to follow work.

    A plain question may be ordinary group conversation.  When it does not match
    an existing task, the safe UX is silence.  Explicit follow-up commands remain
    eligible for a short natural clarification.
    """
    raw = (text or "").strip()
    if forced_command_intent == "FOLLOW_UP":
        return True
    if intent == "FOLLOW_UP":
        return True
    if re.search(r"FU-\d{6}-\d{4}", raw, flags=re.I):
        return True
    return False
