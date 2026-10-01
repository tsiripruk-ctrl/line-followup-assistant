"""Actionable diagnostics without exposing keys, provider bodies or document data."""
import json
import re

KNOWN_CODES = {'invalid_api_key', 'model_not_found', 'insufficient_quota', 'rate_limit_exceeded',
               'billing_hard_limit_reached', 'organization_usage_limit_exceeded', 'invalid_image',
               'invalid_image_format', 'invalid_image_url', 'unsupported_image', 'invalid_value'}


def vision_error(exc):
    status = getattr(exc, 'status_code', None)
    status = status if isinstance(status, int) and 100 <= status <= 599 else None
    body = getattr(exc, 'body', None)
    body = body if isinstance(body, dict) else {}
    error = body.get('error', body)
    error = error if isinstance(error, dict) else {}
    code = error.get('code')
    code = code if isinstance(code, str) and code in KNOWN_CODES else None
    kind = type(exc).__name__
    if kind == 'APITimeoutError' or isinstance(exc, TimeoutError):
        category, message = 'TIMEOUT', 'บริการอ่านภาพตอบช้าเกินเวลาที่กำหนดค่ะ กรุณาลองอ่านเอกสารใหม่อีกครั้งภายหลัง'
    elif kind == 'APIConnectionError':
        category, message = 'CONNECTION', 'เชื่อมต่อบริการอ่านภาพไม่ได้ค่ะ กรุณาตรวจการเชื่อมต่อของระบบแล้วลองใหม่'
    elif status == 401 or code == 'invalid_api_key':
        category, message = 'AUTH', 'บริการไม่ยอมรับ API key ค่ะ กรุณาตรวจ OPENAI_API_KEY ใน Render แล้ว Deploy ใหม่'
    elif status == 403:
        category, message = 'ACCESS', 'บัญชีหรือโปรเจกต์ไม่มีสิทธิ์เรียกบริการนี้ค่ะ กรุณาตรวจสิทธิ์ API และโมเดลที่ตั้งไว้'
    elif status == 404 or code == 'model_not_found':
        category, message = 'MODEL', 'ไม่พบโมเดลที่ตั้งไว้หรือบัญชีไม่มีสิทธิ์ใช้ค่ะ กรุณาตรวจ OPENAI_MODEL หรือ OPENAI_VISION_MODEL ใน Render'
    elif status == 429 and code in {'insufficient_quota', 'billing_hard_limit_reached', 'organization_usage_limit_exceeded'}:
        category, message = 'QUOTA', 'โควตาหรือวงเงิน API ไม่เพียงพอค่ะ กรุณาตรวจ Billing และวงเงินของโปรเจกต์ OpenAI ก่อนลองใหม่'
    elif status == 429:
        category, message = 'RATE_LIMIT', 'บริการจำกัดจำนวนคำขอค่ะ หากทำซ้ำยังไม่ผ่าน ให้ตรวจโควตาและวงเงิน API แล้วลองใหม่ภายหลัง'
    elif status and status >= 500:
        category, message = 'SERVICE', 'บริการอ่านภาพขัดข้องชั่วคราวค่ะ กรุณาลองใหม่ภายหลัง'
    elif code in {'invalid_image', 'invalid_image_format', 'invalid_image_url', 'unsupported_image'}:
        category, message = 'IMAGE', 'บริการปฏิเสธรูปภาพนี้ค่ะ กรุณาตรวจไฟล์และใช้โมเดลที่รองรับภาพ'
    elif status == 400:
        category, message = 'REQUEST', 'บริการไม่รับคำขออ่านภาพค่ะ อาจเป็นข้อกำหนดของโมเดลหรือรูปแบบภาพ กรุณาตรวจโมเดลและข้อมูลคำขอในระบบ'
    elif kind in {'UnidentifiedImageError', 'DecompressionBombError', 'OSError'}:
        category, message = 'FILE', 'เปิดภาพไม่ได้ค่ะ กรุณาตรวจว่าไฟล์ภาพสมบูรณ์และไม่เสียหาย'
    else:
        category, message = 'UNKNOWN', 'อ่านภาพไม่สำเร็จและยังระบุสาเหตุไม่ได้ค่ะ กรุณาตรวจ Logs ของระบบจากรหัสด้านล่าง'
    request_id = getattr(exc, 'request_id', None)
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id) else None
    diagnostic = {'category': category, 'exception': kind, 'http_status': status, 'code': code, 'request_id': request_id}
    print('document vision unavailable:', json.dumps(diagnostic, ensure_ascii=True))
    suffix = f'\nรหัสตรวจสอบ: VISION_{category}'
    if status:
        suffix += f' / HTTP {status}'
    if code:
        suffix += f' / {code}'
    return message + suffix
