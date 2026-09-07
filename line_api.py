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

async def push_text(to: str, text: str):
    if not settings.line_channel_access_token:
        return
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}", "Content-Type": "application/json"}
    payload = {"to": to, "messages": [{"type": "text", "text": text[:5000]}]}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{LINE_API}/message/push", headers=headers, json=payload)
        r.raise_for_status()

async def get_member_profile(group_id: str, user_id: str) -> str | None:
    if not settings.line_channel_access_token:
        return None
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{LINE_API}/group/{group_id}/member/{user_id}", headers=headers)
        if r.status_code == 200:
            return r.json().get("displayName")
    return None
