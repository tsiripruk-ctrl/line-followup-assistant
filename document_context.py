"""Explicit private per-manager context, expires and never guesses between uploads."""
from datetime import datetime, timedelta
import re
from sqlalchemy import select
from document_models import DocumentChatContext, KnowledgeDocument as Doc
from document_analysis import category_for_question, fact_answer


def remember(db, uid, doc, *, upload=False):
    now = datetime.utcnow()
    context = db.get(DocumentChatContext, uid)
    if not context:
        context = DocumentChatContext(user_id=uid, document_id=doc.id, expires_at=now)
        db.add(context)
    elif upload and context.expires_at > now and (context.ambiguous or context.document_id != doc.id):
        context.ambiguous = True
        context.document_id = None
        context.expires_at = now + timedelta(minutes=30)
        return
    context.document_id, context.ambiguous = doc.id, False
    context.expires_at = now + timedelta(minutes=30)


def document_question(text):
    if text.startswith(('เลื่อน', 'บังคับ', 'ปิด', 'เชื่อม', 'แก้', 'งานใหม่', 'ติดตาม', 'เปลี่ยน', 'ให้')):
        return False
    if any(w in text for w in ('งานค้าง', 'ผู้รับผิดชอบ', 'ความคืบหน้า', 'สถานะงาน')):
        return False
    return (category_for_question(text) is not None and
            (any(w in text for w in ('เท่าไหร่', 'เท่าไร', 'เมื่อไหร่', 'กี่', 'อะไร', 'ไหน', 'ขอดู', 'ช่วยดู', 'อย่างไร', 'ยังไง', 'หรือไม่', 'ไหม')) or len(text) < 30))


def context_answer(db, uid, text):
    context = db.get(DocumentChatContext, uid)
    if not context or context.expires_at <= datetime.utcnow():
        return 'ยังไม่ได้เลือกเอกสารที่จะถามหรือบริบทหมดเวลาแล้วค่ะ ส่ง ดูเอกสาร ตามด้วยรหัส DOC เพื่อเลือกก่อน'
    if context.ambiguous:
        return 'มีหลายเอกสารในบทสนทนานี้ค่ะ กรุณาระบุว่าจะถามฉบับไหน เช่น ดูเอกสาร DOC-xxxxxxxxxxxx แล้วถามต่อได้'
    doc = db.get(Doc, context.document_id)
    if not doc or doc.status in {'REJECTED', 'SUPERSEDED'}:
        return 'เอกสารที่เลือกไม่ได้ใช้งานแล้วค่ะ กรุณาเลือกเอกสารใหม่ด้วย ดูเอกสาร ตามด้วยรหัส DOC'
    if doc.status in {'ERROR', 'EXTRACTING'}:
        return f'เอกสาร {doc.code} ยังอ่านไม่สำเร็จหรือกำลังอ่านอยู่ค่ะ ใช้ ดูเอกสาร {doc.code} เพื่อตรวจสถานะ'
    remember(db, uid, doc)
    if doc.status == 'DRAFT':
        if not doc.project:
            return (f'กำลังคุยถึง {doc.code} — {doc.filename} ค่ะ\nเอกสารยังรอเลือกโครงการและยืนยันข้อมูล\n'
                    f'ระบุเองได้: เลือกโครงการ {doc.code} ชื่อโครงการ\n'
                    'จากนั้นตรวจสรุปและกด ยืนยันข้อมูลเอกสาร แล้วถามต่อได้ค่ะ')
        return (f'กำลังคุยถึง {doc.code} — {doc.filename} โครงการ {doc.project} ค่ะ\n'
                f'ยังรอยืนยันข้อมูล กรุณาตรวจสรุปแล้วส่ง ยืนยันเอกสาร {doc.code} หรือกดปุ่มยืนยันค่ะ')
    answer = fact_answer(db, doc, text)
    return answer or f'กรุณาถามเอกสาร {doc.project} พร้อมรายละเอียดที่ต้องการค่ะ'
