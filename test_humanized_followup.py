import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Task, TaskEvent
from service import contextual_followup_text, update_task_progress_snapshot


class HumanizedFollowupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def task(self, code, title, status='IN_PROGRESS', reminder_count=0):
        t = Task(
            task_code=code, group_id='G1', source_message_id='src-'+code,
            title=title, assignee_name='MARCH', status=status,
            reminder_count=reminder_count, confidence=0.99,
        )
        self.db.add(t); self.db.flush()
        self.db.add(TaskEvent(task_id=t.id, event_type='CREATED', text=title))
        self.db.commit()
        return t

    def test_waiting_sales_asks_sales_not_po_again(self):
        t = self.task('FU-1', 'เปิด PO กับ Futong งานศาลากลาง', 'WAITING')
        update_task_progress_snapshot(self.db, t, 'เปิด PO แล้ว แต่ทางเซลล์ยังไม่ตอบรับ เดี๋ยวจะติดตามอีกที', commit=True)
        body, used = contextual_followup_text(self.db, t, '@MARCH', 'พี่ต้อง')
        self.assertTrue(used)
        self.assertIn('เซลล์', body)
        self.assertIn('ตอบกลับ', body)
        self.assertNotIn('ขออัปเดตเรื่อง', body)
        self.assertNotIn('เปิด PO หรือยัง', body)

    def test_waiting_officer_asks_reply(self):
        t = self.task('FU-2', 'แจ้งซ่อมระบบ Flow Account TSP', 'WAITING')
        update_task_progress_snapshot(
            self.db, t,
            'ส่งเอกสารไปแล้ว มีเมลตอบมาแล้วแต่ไม่ตรงประเด็น ตอนนี้ขอเบอร์เจ้าหน้าที่ รอเขาตอบเมลกลับมาค่ะ',
            commit=True,
        )
        body, _ = contextual_followup_text(self.db, t, '@Proud', 'พี่ต้อง')
        self.assertIn('เจ้าหน้าที่', body)
        self.assertIn('ตอบกลับ', body)
        self.assertNotIn('เรื่องที่รออยู่มีความคืบหน้าเพิ่มเติมไหมคะ', body)

    def test_appointment_uses_checkpoint_style(self):
        t = self.task('FU-3', 'เซ็นสัญญาโครงการ A', 'IN_PROGRESS')
        update_task_progress_snapshot(self.db, t, 'นัดเซ็นสัญญาวันศุกร์', commit=True)
        body, _ = contextual_followup_text(self.db, t, '@ตี', 'พี่ต้อง')
        self.assertIn('ช่วงที่นัดไว้', body)
        self.assertNotIn('ขออัปเดต', body)

    def test_no_progress_uses_original_task_and_natural_question(self):
        t = self.task('FU-4', 'ตรวจสอบเบอร์ออฟฟิศใช้งานไม่ได้', 'OPEN')
        body, _ = contextual_followup_text(self.db, t, '@Proud', 'พี่ต้อง')
        self.assertIn('เบอร์ออฟฟิศ', body)
        self.assertIn('ไปถึงไหนแล้วคะ', body)
        self.assertNotIn('ล่าสุด:', body)

    def test_overdue_asks_expected_completion(self):
        t = self.task('FU-5', 'ส่งเอกสารใบตรวจรับ', 'OVERDUE')
        body, _ = contextual_followup_text(self.db, t, '@Boss', 'พี่ต้อง')
        self.assertIn('เลยกำหนด', body)
        self.assertTrue('ประมาณเมื่อไหร่' in body or 'จบได้เมื่อไหร่' in body)

    def test_state_variation_is_deterministic_not_random(self):
        t0 = self.task('FU-6', 'ติดตั้งกล้อง', 'IN_PROGRESS', reminder_count=0)
        update_task_progress_snapshot(self.db, t0, 'ติดตั้งไปแล้วครึ่งหนึ่ง', commit=True)
        b1, _ = contextual_followup_text(self.db, t0, '@MARCH', 'พี่ต้อง')
        b2, _ = contextual_followup_text(self.db, t0, '@MARCH', 'พี่ต้อง')
        self.assertEqual(b1, b2)
        t0.reminder_count = 1
        self.db.commit()
        b3, _ = contextual_followup_text(self.db, t0, '@MARCH', 'พี่ต้อง')
        self.assertNotEqual(b1, b3)

    def test_no_system_language_in_group_reminder(self):
        t = self.task('FU-7', 'ติดตามเอกสารเทศบาล', 'WAITING')
        update_task_progress_snapshot(self.db, t, 'ส่งเอกสารแล้ว รอเจ้าหน้าที่ตอบกลับ', commit=True)
        body, _ = contextual_followup_text(self.db, t, '@Proud', 'พี่ต้อง')
        for banned in ('Task', 'จับคู่', 'ระบบประมวลผล', 'Confidence', 'ไม่พบงาน'):
            self.assertNotIn(banned, body)

    def test_progress_message_is_concise(self):
        t = self.task('FU-8', 'ติดตามการอนุมัติเอกสารโครงการงานระบบกล้องวงจรปิด', 'WAITING')
        update_task_progress_snapshot(self.db, t, 'ส่งเอกสารทั้งหมดให้หน่วยงานแล้ว ตอนนี้กำลังรออนุมัติจากคณะกรรมการ', commit=True)
        body, _ = contextual_followup_text(self.db, t, '@Proud', 'พี่ต้อง')
        self.assertLessEqual(len(body), 240)
        self.assertLessEqual(len(body.splitlines()), 3)

    def test_waiting_document_asks_document_or_approval(self):
        t = self.task('FU-9', 'ติดตามเอกสารอนุมัติ', 'WAITING')
        update_task_progress_snapshot(self.db, t, 'ส่งเอกสารแล้ว ตอนนี้รออนุมัติ', commit=True)
        body, _ = contextual_followup_text(self.db, t, '@Proud', 'พี่ต้อง')
        self.assertTrue('อนุมัติ' in body or 'เอกสาร' in body)

    def test_completed_milestone_not_reasked_when_waiting_exists(self):
        t = self.task('FU-10', 'เปิด PO กับ Futong งานศาลากลาง', 'WAITING')
        update_task_progress_snapshot(self.db, t, 'เปิด PO เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับ', commit=True)
        body, _ = contextual_followup_text(self.db, t, '@MARCH', 'พี่ต้อง')
        # Context may mention the completed milestone once, but the actual question must target Sales.
        last_line = body.splitlines()[-1]
        self.assertIn('เซลล์', last_line)
        self.assertIn('ตอบกลับ', last_line)
        self.assertNotIn('เปิด PO', last_line)


if __name__ == '__main__':
    unittest.main()
