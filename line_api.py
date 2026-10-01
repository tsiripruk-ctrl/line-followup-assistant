import base64, hashlib, hmac
import httpx
from config import settings

LINE_API = "https://api.line.me/v2/bot"

async def download_message_content(message_id: str, max_bytes: int):
    """Stream LINE-hosted content only; bound bytes even without Content-Length."""
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message_id):
        raise ValueError('รหัสไฟล์ไม่ถูกต้องค่ะ')
    if not settings.line_channel_access_token:
        raise ValueError('ยังดาวน์โหลดไฟล์จาก LINE ไม่ได้ค่ะ กรุณาตรวจการเชื่อมต่อของเลขา')
    headers = {'Authorization': f'Bearer {settings.line_channel_access_token}'}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        async with client.stream('GET', f'https://api-data.line.me/v2/bot/message/{message_id}/content', headers=headers) as response:
            response.raise_for_status()
            if int(response.headers.get('content-length', 0)) > max_bytes:
                raise ValueError('ไฟล์ใหญ่เกินขนาดที่รับได้ค่ะ')
            result = bytearray()
            async for block in response.aiter_bytes():
                result.extend(block)
                if len(result) > max_bytes:
                    raise ValueError('ไฟล์ใหญ่เกินขนาดที่รับได้ค่ะ')
            return bytes(result), response.headers.get('content-type', 'application/octet-stream')

def verify_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.line_channel_secret:
        return False
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)

async def push_text(to: str, text: str, *, quick_replies=None) -> str | None:
    """Send a LINE push message and return the sent LINE message ID when available."""
    if not settings.line_channel_access_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}
    payload = {"to": to, "messages": [{"type": "text", "text": text[:5000]}]}
    if quick_replies:
        payload['messages'][0]['quickReply'] = {'items': [
            {'type': 'action', 'action': {'type': 'message', 'label': label[:20], 'text': command[:300]}}
            for label, command in quick_replies[:13]]}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{LINE_API}/message/push", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json() if r.content else {}
        sent = data.get("sentMessages") or []
        if sent:
            return str(sent[0].get("id")) if sent[0].get("id") is not None else None
        return None

async def reply_text(reply_token: str, text: str) -> str | None:
    """Reply directly to the LINE message that triggered the webhook."""
    if not settings.line_channel_access_token or not reply_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{LINE_API}/message/reply", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json() if r.content else {}
        sent = data.get("sentMessages") or []
        if sent:
            return str(sent[0].get("id")) if sent[0].get("id") is not None else None
        return None

async def push_text_mention(to: str, text: str, user_id: str, placeholder: str = "assignee") -> str | None:
    """Send a textV2 push message with a real LINE @mention substitution."""
    if not settings.line_channel_access_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}
    payload = {
        "to": to,
        "messages": [{
            "type": "textV2",
            "text": text[:5000],
            "substitution": {
                placeholder: {
                    "type": "mention",
                    "mentionee": {"type": "user", "userId": user_id},
                }
            },
        }],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{LINE_API}/message/push", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json() if r.content else {}
        sent = data.get("sentMessages") or []
        if sent:
            return str(sent[0].get("id")) if sent[0].get("id") is not None else None
        return None

async def get_member_profile(group_id: str, user_id: str) -> str | None:
    if not settings.line_channel_access_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{LINE_API}/group/{group_id}/member/{user_id}", headers=headers)
        if r.status_code == 200:
            return r.json().get("displayName")
    return None
