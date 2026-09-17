import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from db import Base
from models import Task, TaskEvent, Message
from service import find_existing_followup_task


class DuplicateFollowupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.task = Task(
            task_code="FU-260914-0018",
            group_id="G1",
            source_message_id="m-create",
            title="ติดตามมิเตอร์ชั่วคราว อบต.XXX",
            project="อบต.XXX",
            assignee_name="บอส",
            status="OPEN",
            confidence=0.99,
        )
        self.db.add(self.task)
        self.db.flush()
        self.db.add(TaskEvent(task_id=self.task.id, event_type="CREATED", text="ช่วยตามเรื่องมิเตอร์ชั่วคราว อบต.XXX ให้หน่อย"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_followup_matches_existing_task(self):
        target, score, ambiguous = find_existing_followup_task(
            self.db, "G1", "ขออัปเดตเรื่องมิเตอร์ชั่วคราว อบต.XXX",
            assignee_name="บอส", min_confidence=0.80,
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.id, self.task.id)
        self.assertGreaterEqual(score, 0.80)
        self.assertEqual(ambiguous, [])

    def test_three_followups_do_not_require_new_task(self):
        for text in [
            "ขออัปเดตเรื่องมิเตอร์ชั่วคราว",
            "ตามเรื่องมิเตอร์หน่อย",
            "เรื่องมิเตอร์ถึงไหนแล้ว",
            "ช่วยถามบอสเรื่องมิเตอร์ด้วย",
            "มิเตอร์ของ อบต.XXX เป็นยังไงบ้าง",
        ]:
            target, score, _ = find_existing_followup_task(self.db, "G1", text, assignee_name="บอส", min_confidence=0.80)
            self.assertIsNotNone(target, (text, score))
            self.db.add(TaskEvent(task_id=target.id, event_type="FOLLOW_UP", text=text))
            self.db.commit()
        self.assertEqual(self.db.query(Task).count(), 1)
        self.assertEqual(self.db.query(TaskEvent).filter(TaskEvent.event_type == "FOLLOW_UP").count(), 5)

    def test_message_id_is_idempotency_key(self):
        self.db.add(Message(line_message_id="LINE-MSG-1", source_type="group", source_id="G1", text="hello"))
        self.db.commit()
        self.db.add(Message(line_message_id="LINE-MSG-1", source_type="group", source_id="G1", text="retry"))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.db.query(Message).filter(Message.line_message_id == "LINE-MSG-1").count(), 1)


if __name__ == "__main__":
    unittest.main()
