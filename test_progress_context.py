from datetime import datetime
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Task, TaskEvent
from service import (
    derive_progress_snapshot,
    update_task_progress_snapshot,
    contextual_followup_text,
)


class ProgressContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def make_task(self, code, title, status="IN_PROGRESS"):
        t = Task(
            task_code=code,
            group_id="G1",
            source_message_id=f"src-{code}",
            title=title,
            assignee_name="MARCH",
            status=status,
            confidence=0.99,
        )
        self.db.add(t)
        self.db.flush()
        self.db.add(TaskEvent(task_id=t.id, event_type="CREATED", text=title))
        self.db.commit()
        return t

    def test_snapshot_keeps_latest_progress_waiting_and_next_action(self):
        t = self.make_task("FU-1", "เปิด PO กับ Futong งานศาลากลาง", "WAITING")
        text = "เปิด PO ให้ทาง Futong เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับ เดี๋ยวจะติดตามอีกที"
        snap = update_task_progress_snapshot(self.db, t, text, actor_name="MARCH", commit=True)
        self.assertIn("เปิด PO", snap["summary"])
        self.assertTrue(t.progress_summary)
        self.assertTrue(t.waiting_on)
        self.assertTrue(t.next_action)
        self.assertIsNotNone(t.last_progress_at)
        self.assertEqual(
            self.db.query(TaskEvent).filter(TaskEvent.task_id == t.id, TaskEvent.event_type == "PROGRESS_SNAPSHOT_UPDATED").count(),
            1,
        )

    def test_new_progress_replaces_stale_snapshot_instead_of_appending(self):
        t = self.make_task("FU-2", "เปิด PO กับ Futong งานศาลากลาง", "WAITING")
        update_task_progress_snapshot(
            self.db, t,
            "เปิด PO เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับ เดี๋ยวจะติดตามอีกที",
            commit=True,
        )
        old = t.progress_summary
        update_task_progress_snapshot(
            self.db, t,
            "เซลล์ Futong ตอบรับแล้ว นัดส่งของวันศุกร์",
            commit=True,
        )
        self.assertNotEqual(t.progress_summary, old)
        self.assertIn("ตอบรับแล้ว", t.progress_summary)
        self.assertIn("วันศุกร์", t.progress_summary)

    def test_contextual_followup_shows_progress_not_generic_restart(self):
        t = self.make_task("FU-3", "เปิด PO กับ Futong งานศาลากลาง", "WAITING")
        update_task_progress_snapshot(
            self.db, t,
            "เปิด PO ให้ทาง Futong เรียบร้อยแล้ว แต่ทางเซลล์ยังไม่ตอบรับ เดี๋ยวจะติดตามอีกที",
            commit=True,
        )
        body, used = contextual_followup_text(self.db, t, "@MARCH", "พี่ต้อง")
        self.assertTrue(used)
        self.assertIn("ล่าสุด:", body)
        self.assertIn("เปิด PO", body)
        self.assertIn("เซลล์", body)
        self.assertNotIn("ขออัปเดตเรื่องขออนุมัติเปิด PO", body)

    def test_progress_is_task_local_and_does_not_contaminate_other_task(self):
        po = self.make_task("FU-4", "เปิด PO กับ Futong งานศาลากลาง", "WAITING")
        camera = self.make_task("FU-5", "ตรวจกล้อง อบต.ตาสิทธิ์", "IN_PROGRESS")
        update_task_progress_snapshot(
            self.db, po,
            "เปิด PO แล้ว รอเซลล์ Futong ตอบรับ",
            commit=True,
        )
        self.db.refresh(camera)
        self.assertIsNone(camera.progress_summary)
        body, used = contextual_followup_text(self.db, camera, "@MARCH", "พี่ต้อง")
        self.assertTrue(used)
        self.assertIn("ตรวจกล้อง", body)
        self.assertNotIn("Futong", body)
        self.assertNotIn("เซลล์", body)

    def test_date_commitment_remains_visible_as_next_action(self):
        snap = derive_progress_snapshot("นัดเซ็นสัญญาวันศุกร์ มอบอำนาจให้เกมส์เซ็นสัญญาครับ", "IN_PROGRESS")
        self.assertIn("วันศุกร์", snap["summary"])
        self.assertIn("วันศุกร์", snap["next_action"])


if __name__ == "__main__":
    unittest.main()
