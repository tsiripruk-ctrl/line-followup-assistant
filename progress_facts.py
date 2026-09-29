"""Conservative work facts from an already matched update; never infer identity."""
import re


def normalize_language(text):
    return (text or '').replace('เเ', 'แ').replace('พรุ้งนี้', 'พรุ่งนี้').replace('วันถัดไป', 'พรุ่งนี้')


def is_partial_milestone(text):
    value = re.sub(r'\s+', '', normalize_language(text))
    if re.search(r'(?:ประสานงาน|ประสางาน)(?:เสร็จ)?(?:เรียบร้อย|แล้ว)', value):
        return True
    if re.search(r'(?:อัปเดต|อัพเดท|อัพเดต|อัปเดท|ตรวจ|เช็ก|เช็ค)(?:ต่อ|อีกครั้ง|อีกที)?(?:ใน)?(?:พรุ่งนี้|วันถัดไป|วันจันทร์|วันอังคาร|วันพุธ|วันพฤหัส|วันศุกร์|วันเสาร์|วันอาทิตย์)', value):
        return True
    return any(term in value for term in (
        'นัดส่งเรียบร้อย', 'นัดส่งแล้ว', 'นำรถเข้าซ่อม', 'นำรถเข้าช่อม',
        'เสร็จแล้วจะไป', 'เสร็จแล้วจะส่ง', 'แล้วจะจัดส่ง',
    ))


def quantity_only(text):
    return bool(re.fullmatch(r'\s*(?:เหลือ(?:อีก)?|ส่งแล้ว)?\s*(?:ประมาณ)?\s*\d+\s*รายการ\s*(?:ครับ|ค่ะ|คะ)?\s*', text or ''))


def extract_facts(text):
    raw = text or ''
    value = normalize_language(raw)
    certainty = 'reported'
    if re.search(r'รอ\s*.+?(?:เฟิร์ม|เฟิม|ยืนยัน)', value):
        certainty = 'awaiting_confirmation'
    elif any(term in value for term in ('คาดว่า', 'ประมาณ', 'ไม่น่าจะ')):
        certainty = 'estimated'
    facts = {'certainty': certainty, 'milestone_only': is_partial_milestone(raw)}
    # Preserve quantities as attributed observations; do not add approximate/subset values.
    total = re.search(r'ทั้งหมด\s*(\d+)\s*ตัว', value)
    subset = re.search(r'ประมาณ\s*(\d+)\s*ตัว', value)
    if total:
        facts['total_reported'] = int(total[1])
        if subset and int(subset[1]) <= int(total[1]):
            facts['approximate_subset_reported'] = int(subset[1])
    counts = re.findall(r'(?:ประมาณ\s*)?\d+\s*รายการ', value)
    if counts:
        facts['quantity_observations'] = counts
    if 'รายการที่ยังไม่ได้ส่ง' in value:
        section = value.split('รายการที่ยังไม่ได้ส่ง', 1)[1]
        numbers = [int(n) for n in re.findall(r'^\s*(\d+)\.', section, re.M)]
        if numbers and numbers == list(range(1, len(numbers) + 1)):
            facts['undelivered_list_count'] = len(numbers)
    # A component shipment is not a whole-project delivery commitment.
    facts['date_scope'] = 'component' if re.search(r'UPS\s*จัดส่งวันที่', value, re.I) else 'update'
    return facts
