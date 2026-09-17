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
การตีความสถานะต้องดู "ความหมาย" ไม่ใช่จับเฉพาะคำสำคัญ:
- completed: ผู้ตอบให้ผลลัพธ์หรือคำตอบสุดท้ายที่ตอบโจทย์งานเดิมแล้ว เช่น งานให้เช็กสถานะ แล้วตอบว่า "สถานะใช้ได้ปกติ", "ของเข้าครบ", "เปิดใช้งานได้แล้ว", "ตรวจแล้วไม่มีปัญหา" ให้ถือว่า completed แม้ไม่มีคำว่าเสร็จแล้ว
- in_progress: ยังอยู่ระหว่างดำเนินการ เช่น กำลังทำ/กำลังเช็ก/กำลังประสาน/กำลังตรวจสอบ
- waiting: ยังต้องรอคนอื่นหรือข้อมูล เช่น รอ supplier/รอข้อมูล/รออนุมัติ/รอของเข้า
- none: เป็นเพียงข้อความเสริมที่ยังสรุปสถานะไม่ได้
ถ้าข้อความเป็นการตอบ/อัปเดต/ให้รายละเอียดต่อจากงานเดิม แม้ยังไม่เปลี่ยนสถานะ ให้ is_task_reply=true และใส่ related_task_hint เป็นหัวข้อสั้นๆ ที่ช่วยจับคู่งานเดิม
ถ้าเป็นข้อความทั่วไปที่ไม่เกี่ยวกับงานเดิม ให้ is_task_reply=false
Task creation safety:
- is_task=true เฉพาะเมื่อมี "คำขอ/คำสั่ง/มอบหมาย" ให้ใครทำสิ่งใด หรือมี prefix งานใหม่:/มอบหมายงาน: ชัดเจน
- ข้อความเล่าความคืบหน้า รายงานข้อเท็จจริง หรือบทสนทนางาน เช่น “ไม่ได้สรุปครับ เขาให้พิมพ์ในหนังสือแค่เพิ่มเติมอุปกรณ์ 1 จุด” ห้ามสร้างงานใหม่
- ถ้าข้อความลักษณะดังกล่าวไม่ได้ Reply/Quote งานเดิม และไม่มีบริบท Reminder ล่าสุดที่ตรงอย่างมั่นใจ ให้ is_task_reply=false ด้วย เพื่อไม่ให้ระบบเอาบทสนทนาไปผูกงานเอง
- อย่าใช้เพียงคำว่า งาน/ส่ง/เพิ่ม/ตรวจ/รอ เป็นเหตุผลในการสร้าง Task ต้องมีเจตนามอบหมายหรือติดตามที่ชัดเจน
ตีความวันเวลาโดยใช้ประเทศไทย Asia/Bangkok และคืน due_at_iso แบบ ISO 8601 พร้อม timezone เมื่อระบุได้
ชื่อ title ต้องสั้น ชัดเจน และคงสาระจากข้อความต้นฉบับ

Non-task guard:
- การแจ้งลา/ลาป่วย/ลากิจ/ลาพักร้อน/ไม่เข้าทำงาน/มาสาย/กลับก่อน ที่เป็นเพียงการแจ้งสถานะส่วนบุคคล ให้ is_task=false เสมอ
- เช่น “ขอลากิจ 2 วันเพื่อเฝ้าแม่ผ่าตัด” คือ NON-TASK ไม่ใช่งานติดตาม
- แต่ถ้ามีคำสั่งให้ทำงานเกี่ยวกับการลา เช่น “ช่วยทำใบลาให้บอส”, “ส่งใบลาให้ HR”, “อนุมัติการลาให้...” จึงพิจารณาเป็น task ได้
การระบุผู้รับผิดชอบ (assignee_name):
- ถ้าข้อความระบุชัดว่า “ให้ X เป็นผู้รับผิดชอบ”, “มอบหมายให้ X”, “ฝาก X ช่วย”, “X รับผิดชอบงานนี้” ให้คืนชื่อ X
- ระวังชื่อคนภาษาไทยที่ซ้ำกับคำกริยา/คำช่วย เช่น “ต้อง”: “ให้ต้องเป็นผู้รับผิดชอบ” หมายถึงคนชื่อ ต้อง แต่ “งานนี้ต้องส่งวันนี้” ไม่ได้ระบุคนชื่อ ต้อง
- ห้ามเดาชื่อผู้รับผิดชอบเมื่อบริบทไม่ชัด
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
