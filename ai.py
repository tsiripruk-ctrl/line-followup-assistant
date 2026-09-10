from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from openai import OpenAI
from config import settings

class TaskExtraction(BaseModel):
    is_task: bool
    confidence: float = Field(ge=0, le=1)
    title: str = ""
    project: str | None = None
    assignee_name: str | None = None
    due_at_iso: str | None = None
    status_signal: Literal["none", "completed", "in_progress", "waiting"] = "none"
    is_task_reply: bool = False
    related_task_hint: str | None = None
    reason: str = ""

SYSTEM = """คุณคือ AI เลขานุการติดตามงานในกลุ่ม LINE ของบริษัท
หน้าที่คือแยก 'คำสั่งงาน/สิ่งที่ต้องติดตาม' ออกจากบทสนทนาทั่วไป
ให้ is_task=true เฉพาะเมื่อข้อความมีภารกิจที่ควรมีผู้รับผิดชอบหรือกำหนดติดตามจริง
อย่าจับบทสนทนาเล่น ข่าว ความเห็น หรือการแจ้งข้อมูลทั่วไปเป็นงาน
ถ้าพบคำว่า เสร็จแล้ว/เรียบร้อย/ส่งแล้ว/ปิดงาน ให้ status_signal=completed
ถ้าพบคำว่า กำลังทำ/กำลังเช็ก ให้ in_progress
ถ้าพบคำว่า รอ supplier/รอข้อมูล/รออนุมัติ ให้ waiting
ถ้าข้อความเป็นการตอบ/อัปเดต/ให้รายละเอียดต่อจากงานเดิม แม้ยังไม่เปลี่ยนสถานะ ให้ is_task_reply=true และใส่ related_task_hint เป็นหัวข้อสั้นๆ ที่ช่วยจับคู่งานเดิม
ถ้าเป็นข้อความทั่วไปที่ไม่เกี่ยวกับงานเดิม ให้ is_task_reply=false
ตีความวันเวลาโดยใช้ประเทศไทย Asia/Bangkok และคืน due_at_iso แบบ ISO 8601 พร้อม timezone เมื่อระบุได้
ชื่อ title ต้องสั้น ชัดเจน และคงสาระจากข้อความต้นฉบับ
"""

def extract_task(text: str, sender_name: str | None = None) -> TaskExtraction:
    if not settings.openai_api_key:
        return TaskExtraction(is_task=False, confidence=0, reason="OPENAI_API_KEY not configured")
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat()
    client = OpenAI(api_key=settings.openai_api_key)
    rsp = client.responses.parse(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"เวลาปัจจุบัน: {now}\nผู้ส่ง: {sender_name or '-'}\nข้อความ: {text}"},
        ],
        text_format=TaskExtraction,
    )
    return rsp.output_parsed
