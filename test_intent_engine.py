import unittest
from intent_engine import classify_message_intent, intent_to_status_signal


class IntentEngineRegressionTests(unittest.TestCase):
    def assert_intent(self, text, expected):
        got = classify_message_intent(text)
        self.assertEqual(got.intent, expected, (text, got))
        return got

    def test_01_question_never_complete(self):
        r = self.assert_intent("งานนี้เรียบร้อยหรือยัง", "STATUS_QUERY")
        self.assertEqual(intent_to_status_signal(r.intent), "none")

    def test_02_negation_never_complete(self):
        r = self.assert_intent("ยังไม่เรียบร้อยครับ", "NOT_COMPLETED")
        self.assertNotEqual(intent_to_status_signal(r.intent), "completed")

    def test_03_conditional_followup(self):
        self.assert_intent("ถ้าเรียบร้อยแล้วแจ้งด้วย", "FOLLOW_UP")

    def test_04_explicit_completion(self):
        r = self.assert_intent("ดำเนินการเรียบร้อยแล้วครับ", "COMPLETION_CONFIRMATION")
        self.assertEqual(intent_to_status_signal(r.intent), "completed")

    def test_05_milestone_progress(self):
        self.assert_intent("ส่ง Datasheet ให้การไฟฟ้าแล้ว", "PROGRESS_UPDATE")

    def test_09_mixed_not_done_and_milestone(self):
        self.assert_intent("เรื่องนี้ยังไม่เสร็จ แต่ส่งเอกสารแล้ว", "PROGRESS_UPDATE")

    def test_10_question_with_completion_word(self):
        self.assert_intent("เรื่องนี้เสร็จหรือยังครับ", "STATUS_QUERY")

    def test_bare_word_not_completion(self):
        self.assert_intent("เรียบร้อย", "PROGRESS_UPDATE")

    def test_insurance_clear_completion(self):
        self.assert_intent("จ่ายค่าประกันเรียบร้อย", "COMPLETION_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
