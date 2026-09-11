import base64, hashlib, hmac
import httpx
from config import settings

LINE_API = "https://api.line.me/v2/bot"

def verify_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.line_channel_secret:
        return False
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)

async def push_text(to: str, text: str) -> str | None:
    """Send a LINE push message and return the sent LINE message ID when available."""
    if not settings.line_channel_access_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}
    payload = {"to": to, "messages": [{"type": "text", "text": text[:5000]}]}
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
