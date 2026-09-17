from intent_engine import classify_message_intent, is_direct_task_request


def test_human_directed_action_request_beats_question_clause():
    text = "@Proud ตอนนี้เบอร์ออฟฟิตใช้งานไม่ได้หรือเปล่า ฝากตรวจสอบที"
    assert classify_message_intent(text).intent == "STATUS_QUERY"
    assert is_direct_task_request(text) is True


def test_plain_status_question_is_not_new_task_request():
    text = "@Proud เรื่อง Flow Account ถึงไหนแล้ว"
    assert classify_message_intent(text).intent == "STATUS_QUERY"
    assert is_direct_task_request(text) is False


def test_followup_stays_followup_not_new_task():
    text = "@MARCH ช่วยตามเรื่องมิเตอร์ให้หน่อย"
    assert classify_message_intent(text).intent == "FOLLOW_UP"
    assert is_direct_task_request(text) is False


def test_action_request_without_question_is_detected():
    assert is_direct_task_request("@Proud ฝากตรวจสอบเบอร์ออฟฟิศที") is True
