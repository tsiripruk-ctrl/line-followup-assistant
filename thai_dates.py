"""Normalize explicit Thai month dates without inventing a date from vague wording."""
import re

MONTHS = (
    ('มกราคม','ม.ค.'), ('กุมภาพันธ์','ก.พ.'), ('มีนาคม','มี.ค.'), ('เมษายน','เม.ย.'),
    ('พฤษภาคม','พ.ค.'), ('มิถุนายน','มิ.ย.'), ('กรกฎาคม','ก.ค.'), ('สิงหาคม','ส.ค.'),
    ('กันยายน','ก.ย.'), ('ตุลาคม','ต.ค.'), ('พฤศจิกายน','พ.ย.'), ('ธันวาคม','ธ.ค.'),
)
LOOKUP = {label:number for number,labels in enumerate(MONTHS,1) for label in labels}
PATTERN = re.compile(r'(?<!\d)(\d{1,2})\s*(?:เดือน\s*)?('+
    '|'.join(re.escape(label) for label in sorted(LOOKUP,key=len,reverse=True))+
    r')(?:\s*(?:พ\.ศ\.|ค\.ศ\.|ปี)?\s*(\d{4}|\d{2})(?![\d:.]))?')


def normalize_month_date(text):
    value = (text or '').translate(str.maketrans('๐๑๒๓๔๕๖๗๘๙','0123456789'))
    def substitute(match):
        day,month,year = match.groups()
        return f'{day}/{LOOKUP[month]}' + (f'/{year}' if year else '')
    return PATTERN.sub(substitute,value)
