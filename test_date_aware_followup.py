from datetime import datetime
from zoneinfo import ZoneInfo

from service import extract_followup_commitment_at

TZ = ZoneInfo("Asia/Bangkok")

def local_result(value):
    assert value is not None
    return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)


def test_friday_from_tuesday_goes_to_upcoming_friday_0830():
    now = datetime(2026, 9, 15, 16, 40, tzinfo=TZ)  # Tuesday
    out = local_result(extract_followup_commitment_at("นัดเซ็นสัญญาวันศุกร์ครับ", now_local=now))
    assert (out.year, out.month, out.day, out.hour, out.minute) == (2026, 9, 18, 8, 30)


def test_tomorrow_defaults_to_work_start():
    now = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
    out = local_result(extract_followup_commitment_at("พรุ่งนี้จะเข้าไปติดตามอีกที", now_local=now))
    assert (out.year, out.month, out.day, out.hour, out.minute) == (2026, 9, 16, 8, 30)


def test_explicit_time_is_preserved_inside_work_window():
    now = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
    out = local_result(extract_followup_commitment_at("วันศุกร์ 14:00 นัดเซ็นสัญญา", now_local=now))
    assert (out.year, out.month, out.day, out.hour, out.minute) == (2026, 9, 18, 14, 0)


def test_no_date_returns_none():
    now = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
    assert extract_followup_commitment_at("กำลังดำเนินการอยู่ครับ", now_local=now) is None


def test_buddhist_numeric_date():
    now = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)
    out = local_result(extract_followup_commitment_at("นัดอีกที 20/09/2569", now_local=now))
    assert (out.year, out.month, out.day, out.hour, out.minute) == (2026, 9, 20, 8, 30)
