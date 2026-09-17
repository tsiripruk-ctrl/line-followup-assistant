import unittest
from intent_guard import classify_precreation_guard


class TaskCreationGuardTests(unittest.TestCase):
    def blocked(self, text: str) -> bool:
        return classify_precreation_guard(text).block_task_creation

    def test_leave_notice_is_not_task(self):
        self.assertTrue(self.blocked("ขอลากิจ 2 วันเพื่อเฝ้าแม่ผ่าตัด"))
        self.assertTrue(self.blocked("พรุ่งนี้ลาป่วย 1 วันครับ"))
        self.assertTrue(self.blocked("วันนี้ขออนุญาตลากิจนะคะ"))
        self.assertTrue(self.blocked("พรุ่งนี้ไม่เข้าทำงานครับ"))
        self.assertTrue(self.blocked("ช่วงบ่ายขอกลับก่อนครับ"))

    def test_leave_related_action_stays_task_eligible(self):
        self.assertFalse(self.blocked("บอสช่วยทำใบลาให้พนักงานด้วย"))
        self.assertFalse(self.blocked("รบกวนส่งใบลาของบอสให้ฝ่ายบุคคล"))
        self.assertFalse(self.blocked("ช่วยอนุมัติการลาให้มาร์ชด้วย"))
        self.assertFalse(self.blocked("จัดทำเอกสารลาและส่งให้ HR วันนี้"))
        self.assertFalse(self.blocked("ช่วยตามใบลาป่วยของบอสกับ HR ด้วย"))
        self.assertFalse(self.blocked("รบกวนตรวจสอบการลาของมาร์ชด้วย"))

    def test_normal_work_is_not_blocked(self):
        self.assertFalse(self.blocked("มาร์ชช่วยตามเรื่อง GPS จุดเก่าด้วย"))
        self.assertFalse(self.blocked("ส่งต้นทุนสายไฟเบอร์ภายในวันนี้"))


if __name__ == "__main__":
    unittest.main()
