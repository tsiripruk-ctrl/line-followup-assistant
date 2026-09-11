import random
from datetime import datetime, timedelta
import asyncio
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, Header, Query
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.responses import HTMLResponse
from html import escape
from apscheduler.triggers.cron import CronTrigger

from db import Base, engine, SessionLocal
from models import Message, Task, OutboundTaskMessage, Person, PersonAlias
from config import settings
from line_api import verify_signature, get_member_profile, push_text, reply_text, push_text_mention
from ai import extract_task
from service import (
    create_task, open_tasks, completed_tasks, get_task_by_code, tasks_due_today,
    tasks_due_tomorrow, overdue_tasks, waiting_tasks, completed_today,
    format_task, choose_status_target, STATUS_THAI, brief_counts,
    event_exists, record_event, search_open_tasks, task_stats,
    resolve_canonical_name, set_person_alias, list_people, record_task_event,
    task_timeline, backfill_task_created_events, bind_person_identity
)

VERSION = "0.6.0"
app = FastAPI(title="LINE Follow-up Assistant", version=VERSION)
scheduler = AsyncIOScheduler(timezone=settings.timezone)


@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        imported = backfill_task_created_events(db)
        if imported:
            print(f"v0.5 timeline backfill: {imported} tasks")
    # If CRON_SECRET is configured, v0.3.1 uses /jobs/tick as the single
    # authoritative reminder scheduler. This avoids duplicate sends when an
    # internal timer and an external cron fire at nearly the same time.
    if not settings.cron_secret:
        scheduler.add_job(
            reminder_scan, "interval", seconds=settings.reminder_check_seconds,
            id="reminder_scan", replace_existing=True, max_instances=1, coalesce=True
        )
        if settings.daily_brief_enabled:
            scheduler.add_job(
                morning_brief,
                CronTrigger(hour=settings.morning_brief_hour, minute=settings.morning_brief_minute, timezone=settings.timezone),
                id="morning_brief", replace_existing=True, max_instances=1, coalesce=True,
            )
            scheduler.add_job(
                evening_brief,
                CronTrigger(hour=settings.evening_brief_hour, minute=settings.evening_brief_minute, timezone=settings.timezone),
                id="evening_brief", replace_existing=True, max_instances=1, coalesce=True,
            )
        scheduler.start()
        print("scheduler mode: internal fallback")
    else:
        print("scheduler mode: external /jobs/tick")
    print(f"LINE Follow-up Assistant v{VERSION} started")


@app.get("/")
def root():
    return {"ok": True, "service": "line-followup-assistant", "version": VERSION}


@app.get("/health")
def health():
    dialect = engine.url.get_backend_name()
    return {
        "ok": True, "service": "line-followup-assistant", "version": VERSION,
        "scheduler": "external" if settings.cron_secret else "internal",
        "dashboard": bool(settings.dashboard_token),
        "database": "postgresql" if dialect == "postgresql" else dialect,
        "timeline": True, "assignee_normalization": True,
        "mention_assignment": True, "mention_followup": True,
    }


def _require_cron_secret(secret: str | None):
    # When CRON_SECRET is blank, external job endpoints are disabled.
    if not settings.cron_secret:
        raise HTTPException(status_code=404, detail="Not found")
    if secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")


@app.get("/jobs/tick")
@app.post("/jobs/tick")
async def external_tick(
    x_cron_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
):
    """Reliable scheduler entrypoint. Call every 5 minutes from an external cron.

    Header X-Cron-Secret is preferred. Query ?secret= is supported only for
    schedulers that cannot send custom headers.
    """
    _require_cron_secret(x_cron_secret or secret)
    reminder_stats = await reminder_scan(force=False)
    if settings.brief_catchup_on_tick:
        brief_stats = await catch_up_daily_briefs()
    else:
        brief_stats = {"morning": False, "evening": False, "mode": "separate-brief-jobs"}
    return {
        "ok": True,
        "job": "tick",
        "local_time": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
        "quiet_hours": in_quiet_hours(),
        "reminders": reminder_stats,
        "briefs": brief_stats,
    }


@app.post("/jobs/reminder")
async def external_reminder(x_cron_secret: str | None = Header(default=None)):
    _require_cron_secret(x_cron_secret)
    # External reminder calls MUST respect quiet hours.
    stats = await reminder_scan(force=False)
    return {"ok": True, "job": "reminder", "stats": stats}


@app.post("/jobs/morning-brief")
async def external_morning_brief(x_cron_secret: str | None = Header(default=None)):
    _require_cron_secret(x_cron_secret)
    await morning_brief()
    return {"ok": True, "job": "morning-brief"}


@app.post("/jobs/evening-brief")
async def external_evening_brief(x_cron_secret: str | None = Header(default=None)):
    _require_cron_secret(x_cron_secret)
    await evening_brief()
    return {"ok": True, "job": "evening-brief"}


@app.get("/jobs/test")
@app.post("/jobs/test")
async def external_job_test(
    x_cron_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
):
    _require_cron_secret(x_cron_secret or secret)
    if not settings.owner_line_user_id:
        raise HTTPException(status_code=400, detail="OWNER_LINE_USER_ID not configured")
    local = datetime.now(ZoneInfo(settings.timezone))
    await push_text(
        settings.owner_line_user_id,
        f"✅ TEST ONLY — Scheduler → Render → LINE สำเร็จค่ะ\n"
        f"เวลาทดสอบ: {local.strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"เวอร์ชัน: {VERSION}\n\n"
        "ข้อความนี้มาจาก /jobs/test เท่านั้น และจะไม่สั่ง Reminder หรือ Daily Brief ค่ะ"
    )
    return {"ok": True, "job": "test", "local_time": local.isoformat()}


@app.get("/jobs/status")
async def external_job_status(
    x_cron_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
):
    _require_cron_secret(x_cron_secret or secret)
    now = datetime.utcnow()
    with SessionLocal() as db:
        due = list(db.scalars(select(Task).where(
            Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
            Task.next_reminder_at != None,
            Task.next_reminder_at <= now,
        ).order_by(Task.next_reminder_at.asc()).limit(20)).all())
    return {
        "ok": True,
        "version": VERSION,
        "local_time": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
        "quiet_hours": in_quiet_hours(),
        "due_reminder_count": len(due),
        "due_tasks": [t.task_code for t in due],
    }


def _require_dashboard_token(token: str | None):
    if not settings.dashboard_token:
        raise HTTPException(status_code=404, detail="Dashboard disabled")
    if token != settings.dashboard_token:
        raise HTTPException(status_code=401, detail="Invalid dashboard token")


def _task_dict(t: Task):
    due_text = "ยังไม่ระบุ"
    if t.due_at:
        due_text = format_task(t).split("กำหนด: ", 1)[1].split("\n", 1)[0]
    return {
        "task_code": t.task_code,
        "title": t.title,
        "project": t.project,
        "assignee_name": t.assignee_name,
        "due": due_text,
        "status": t.status,
        "status_th": STATUS_THAI.get(t.status, t.status),
        "reminder_count": t.reminder_count,
    }


@app.get("/api/stats")
def api_stats(token: str | None = Query(default=None)):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        return {"ok": True, "version": VERSION, "stats": task_stats(db)}


@app.get("/api/tasks")
def api_tasks(
    token: str | None = Query(default=None),
    q: str = Query(default=""),
    project: str = Query(default=""),
    assignee: str = Query(default=""),
    status: str = Query(default="ACTIVE"),
):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        tasks = search_open_tasks(db, q, project, assignee, status)
        return {"ok": True, "count": len(tasks), "tasks": [_task_dict(t) for t in tasks]}


@app.post("/api/tasks/{task_code}/status")
def api_task_status(
    task_code: str,
    status: str = Query(...),
    token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    new_status = status.upper().strip()
    allowed = {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE", "COMPLETED", "CANCELLED"}
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")
    with SessionLocal() as db:
        t = get_task_by_code(db, task_code)
        if not t:
            raise HTTPException(status_code=404, detail="Task not found")
        old_status = t.status
        t.status = new_status
        if new_status in {"COMPLETED", "CANCELLED"}:
            t.next_reminder_at = None
        elif t.next_reminder_at is None:
            t.next_reminder_at = datetime.utcnow() + timedelta(minutes=5)
        record_task_event(
            db, t, "DASHBOARD_STATUS_CHANGE", actor_name=settings.owner_display_name,
            text=f"เปลี่ยนสถานะจาก Dashboard → {STATUS_THAI.get(new_status, new_status)}",
            old_status=old_status, new_status=new_status, commit=False,
        )
        db.commit()
        db.refresh(t)
        return {"ok": True, "task": _task_dict(t)}



@app.get("/api/tasks/{task_code}/timeline")
def api_task_timeline(task_code: str, token: str | None = Query(default=None)):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        task = get_task_by_code(db, task_code)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        events = task_timeline(db, task)
        result = []
        for ev in events:
            local_time = ev.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.timezone))
            result.append({
                "event_type": ev.event_type,
                "actor_name": ev.actor_name or "System",
                "text": ev.text or "",
                "old_status": ev.old_status,
                "new_status": ev.new_status,
                "created_at": local_time.strftime("%d/%m/%Y %H:%M:%S"),
            })
        return {"ok": True, "task": _task_dict(task), "events": result}


@app.get("/api/people")
def api_people(token: str | None = Query(default=None)):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        return {"ok": True, "people": list_people(db)}


@app.post("/api/people/alias")
def api_people_alias(
    alias: str = Query(...), canonical: str = Query(...),
    token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            person, updated = set_person_alias(db, alias, canonical)
            return {"ok": True, "canonical_name": person.canonical_name, "updated_tasks": updated}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    token: str | None = Query(default=None),
    q: str = Query(default=""),
    project: str = Query(default=""),
    assignee: str = Query(default=""),
    status: str = Query(default="ACTIVE"),
):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        stats = task_stats(db)
        tasks = search_open_tasks(db, q, project, assignee, status)
        people = list_people(db)

    cards = [
        ("งานค้าง", stats["open"]),
        ("ครบกำหนดวันนี้", stats["today"]),
        ("เลยกำหนด", stats["overdue"]),
        ("รอข้อมูล", stats["waiting"]),
        ("ปิดวันนี้", stats["completed_today"]),
        ("พรุ่งนี้", stats["tomorrow"]),
    ]
    card_html = "".join(
        f'<div class="card"><b>{escape(label)}</b><span>{value}</span></div>'
        for label, value in cards
    )
    rows = []
    for t in tasks:
        d = _task_dict(t)
        code = escape(d["task_code"])
        rows.append(
            "<tr>"
            f"<td><b>{code}</b><br><small>{escape(d['status_th'])}</small></td>"
            f"<td>{escape(d['title'])}</td>"
            f"<td>{escape(d['project'] or '-')}</td>"
            f"<td>{escape(d['assignee_name'] or '-')}</td>"
            f"<td>{escape(d['due'])}</td>"
            f"<td>{d['reminder_count']}</td>"
            f"<td><button onclick=\"showTimeline('{code}')\">ประวัติ</button> "
            f"<button onclick=\"setStatus('{code}','COMPLETED')\">ปิดงาน</button> "
            f"<button class=\"secondary\" onclick=\"setStatus('{code}','WAITING')\">รอข้อมูล</button></td>"
            "</tr>"
        )
    rows_html = "".join(rows) or '<tr><td colspan="7">ไม่มีรายการ</td></tr>'
    safe_token = escape(token or "", quote=True)
    selected = status.upper()
    options = []
    for value, label in [
        ("ACTIVE", "งานค้าง"), ("OVERDUE", "เลยกำหนด"),
        ("WAITING", "รอข้อมูล"), ("COMPLETED", "เสร็จแล้ว"),
        ("CANCELLED", "ยกเลิก"),
    ]:
        sel = " selected" if selected == value else ""
        options.append(f'<option value="{value}"{sel}>{label}</option>')
    options_html = "".join(options)
    token_js = repr(token or "")
    people_rows = []
    for p in people:
        aliases = ", ".join(p["aliases"]) or "-"
        people_rows.append(
            f"<tr><td><b>{escape(p['canonical_name'])}</b></td><td>{escape(aliases)}</td></tr>"
        )
    people_html = "".join(people_rows) or '<tr><td colspan="2">ยังไม่มีชื่อมาตรฐาน</td></tr>'

    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LINE Follow-up Dashboard</title>
<style>
body{{font-family:Arial,sans-serif;background:#f6f7f8;margin:0;color:#202124}}
main{{max-width:1280px;margin:auto;padding:24px}} h1{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px}}
.card{{background:white;border:1px solid #ddd;border-radius:12px;padding:14px}} .card span{{display:block;font-size:28px;margin-top:7px}}
form{{background:white;padding:14px;border-radius:12px;border:1px solid #ddd;margin-bottom:18px;display:flex;gap:8px;flex-wrap:wrap}}
input,select{{padding:9px;border:1px solid #bbb;border-radius:8px}} button{{padding:8px 10px;border:0;border-radius:8px;background:#1677ff;color:white;cursor:pointer}}
button.secondary{{background:#6b7280}} table{{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden}}
th,td{{padding:11px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}} th{{background:#f0f2f5}} small{{color:#666}}
.section{{margin-top:22px}} .section h2{{margin:0 0 10px}}
.modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);align-items:center;justify-content:center;padding:20px}}
.modalbox{{background:white;max-width:760px;width:100%;max-height:80vh;overflow:auto;border-radius:14px;padding:18px}}
.timeline-item{{border-left:3px solid #1677ff;padding:8px 12px;margin:8px 0;background:#f8fafc}}
.timeline-time{{font-size:12px;color:#666}}
@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}} table{{font-size:13px;display:block;overflow-x:auto}}}}
</style></head>
<body><main><h1>LINE Follow-up Assistant</h1><div class="sub">Command Center v{VERSION}</div>
<div class="cards">{card_html}</div>
<form method="get">
<input type="hidden" name="token" value="{safe_token}">
<input name="q" value="{escape(q, quote=True)}" placeholder="ค้นหางาน">
<input name="project" value="{escape(project, quote=True)}" placeholder="โครงการ">
<input name="assignee" value="{escape(assignee, quote=True)}" placeholder="ผู้รับผิดชอบ">
<select name="status">{options_html}</select><button type="submit">ค้นหา</button>
</form>
<table><thead><tr><th>รหัส/สถานะ</th><th>งาน</th><th>โครงการ</th><th>ผู้รับผิดชอบ</th><th>กำหนด</th><th>ตามแล้ว</th><th>จัดการ</th></tr></thead>
<tbody>{rows_html}</tbody></table>

<div class="section"><h2>จัดการชื่อผู้รับผิดชอบ</h2>
<form onsubmit="saveAlias(event)">
<input id="aliasName" placeholder="ชื่อที่พบ เช่น Tong Thanakrit" required>
<input id="canonicalName" placeholder="ชื่อมาตรฐาน เช่น ต้น" required>
<button type="submit">รวมชื่อ</button>
</form>
<table><thead><tr><th>ชื่อมาตรฐาน</th><th>ชื่อที่ระบบรู้จัก</th></tr></thead><tbody>{people_html}</tbody></table>
</div>
</main>
<div id="timelineModal" class="modal" onclick="if(event.target===this)closeTimeline()"><div class="modalbox">
<div style="display:flex;justify-content:space-between;gap:10px"><h2 id="timelineTitle">ประวัติงาน</h2><button class="secondary" onclick="closeTimeline()">ปิด</button></div>
<div id="timelineBody"></div></div></div>
<script>
const token={token_js};
async function setStatus(code,status){{
  if(!confirm('ยืนยัน '+code+' → '+status+' ?')) return;
  const u='/api/tasks/'+encodeURIComponent(code)+'/status?status='+encodeURIComponent(status)+'&token='+encodeURIComponent(token);
  const r=await fetch(u,{{method:'POST'}});
  if(r.ok) location.reload(); else alert(await r.text());
}}
async function showTimeline(code){{
  const r=await fetch('/api/tasks/'+encodeURIComponent(code)+'/timeline?token='+encodeURIComponent(token));
  if(!r.ok){{alert(await r.text());return;}}
  const d=await r.json();
  document.getElementById('timelineTitle').textContent=code+' — '+d.task.title;
  const body=document.getElementById('timelineBody'); body.innerHTML='';
  if(!d.events.length) body.innerHTML='<p>ยังไม่มีประวัติ</p>';
  d.events.forEach(e=>{{
    const item=document.createElement('div'); item.className='timeline-item';
    item.innerHTML='<div class="timeline-time">'+e.created_at+' • '+esc(e.actor_name)+'</div><b>'+esc(e.event_type)+'</b><div>'+esc(e.text||'')+'</div>';
    body.appendChild(item);
  }});
  document.getElementById('timelineModal').style.display='flex';
}}
function closeTimeline(){{document.getElementById('timelineModal').style.display='none';}}
function esc(s){{return String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
async function saveAlias(ev){{
  ev.preventDefault();
  const alias=document.getElementById('aliasName').value.trim();
  const canonical=document.getElementById('canonicalName').value.trim();
  const u='/api/people/alias?alias='+encodeURIComponent(alias)+'&canonical='+encodeURIComponent(canonical)+'&token='+encodeURIComponent(token);
  const r=await fetch(u,{{method:'POST'}});
  if(r.ok){{const d=await r.json();alert('รวมชื่อแล้ว และปรับงานเดิม '+d.updated_tasks+' รายการ');location.reload();}} else alert(await r.text());
}}
</script></body></html>"""


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-line-signature")
    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    payload = await request.json()
    for event in payload.get("events", []):
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            asyncio.create_task(process_message(event))
    return {"ok": True}


async def acknowledge_task_reply(reply_token: str | None, group_id: str, status_signal: str):
    """Reply like a natural Thai female secretary, with light variation.

    Only messages successfully matched to an existing task reach this function.
    The wording intentionally avoids system-like phrases and repetitive templates.
    """
    reply_pool = {
        "completed": [
            "ขอบคุณมากค่ะ เรียบร้อยแล้วนะคะ ขอบคุณที่ช่วยจัดการให้ค่ะ",
            "ขอบคุณนะคะ งานนี้เรียบร้อยแล้วค่ะ จัดการให้เรียบร้อยดีมากเลยค่ะ",
            "ขอบคุณมากค่ะ รับทราบว่าเรียบร้อยแล้วนะคะ งานนี้ปิดได้เลยค่ะ",
            "ขอบคุณค่ะ เรียบร้อยแล้วนะคะ ขอบคุณที่อัปเดตให้ค่ะ",
        ],
        "waiting": [
            "ขอบคุณที่อัปเดตนะคะ รับทราบเรื่องที่ยังรออยู่ค่ะ เดี๋ยวขออนุญาตติดตามต่ออีกครั้งนะคะ",
            "ขอบคุณค่ะ รับทราบว่ายังรอข้อมูลอยู่นะคะ เดี๋ยวช่วยติดตามต่อค่ะ",
            "รับทราบค่ะ ขอบคุณที่แจ้งนะคะ เรื่องนี้เดี๋ยวขออนุญาตติดตามต่อจนเรียบร้อยค่ะ",
        ],
        "in_progress": [
            "ขอบคุณที่อัปเดตนะคะ รับทราบค่ะ เดี๋ยวขออนุญาตติดตามต่อจนเรียบร้อยนะคะ",
            "ขอบคุณค่ะ รับทราบว่ากำลังดำเนินการอยู่นะคะ ไว้เดี๋ยวขอติดตามต่ออีกครั้งค่ะ",
            "รับทราบค่ะ ขอบคุณที่อัปเดตนะคะ เดี๋ยวช่วยติดตามความคืบหน้าต่อค่ะ",
        ],
        "none": [
            "ขอบคุณนะคะ รับทราบค่ะ",
            "ขอบคุณที่แจ้งนะคะ รับทราบค่ะ",
            "รับทราบค่ะ ขอบคุณที่อัปเดตนะคะ",
            "ขอบคุณค่ะ รับทราบแล้วนะคะ",
        ],
    }
    text = random.choice(reply_pool.get(status_signal, reply_pool["none"]))
    try:
        if reply_token:
            await reply_text(reply_token, text)
        else:
            await push_text(group_id, text)
    except Exception as exc:
        print("task reply acknowledgement failed:", repr(exc))
        try:
            await push_text(group_id, text)
        except Exception as fallback_exc:
            print("task reply acknowledgement fallback failed:", repr(fallback_exc))


def _mention_text(text: str, mentionee: dict) -> str | None:
    """Best-effort visible @name extraction for mention entries without userId."""
    try:
        start = int(mentionee.get("index", 0))
        length = int(mentionee.get("length", 0))
        value = text[start:start + length].strip()
        return value.lstrip("@").strip() or None
    except Exception:
        return None


async def resolve_message_mentions(msg: dict, group_id: str) -> list[dict]:
    """Return real user mentions, excluding @All and mentions to the bot itself."""
    result = []
    mention = msg.get("mention") or {}
    text = msg.get("text", "")
    for item in mention.get("mentionees") or []:
        if item.get("type") != "user" or item.get("isSelf") is True:
            continue
        uid = item.get("userId")
        visible = _mention_text(text, item)
        display = None
        if uid:
            display = await get_member_profile(group_id, uid)
        result.append({"user_id": uid, "display_name": display or visible, "visible_name": visible})
    return result


def select_primary_mention(mentions: list[dict], extracted_assignee: str | None) -> dict | None:
    if not mentions:
        return None
    if len(mentions) == 1:
        return mentions[0]
    wanted = (extracted_assignee or "").strip().lower()
    if wanted:
        for m in mentions:
            for value in (m.get("display_name"), m.get("visible_name")):
                if value and (wanted in value.lower() or value.lower() in wanted):
                    return m
    # One task currently has one primary assignee. The first explicit user mention
    # is treated as primary; future versions can add co-assignees.
    return mentions[0]


async def process_message(event: dict):
    try:
        await _process_message(event)
    except Exception as exc:
        print("process_message failed:", repr(exc))


async def _process_message(event: dict):
    msg = event["message"]
    source = event.get("source", {})
    source_type = source.get("type", "unknown")
    source_id = source.get("groupId") or source.get("userId") or source.get("roomId")
    user_id = source.get("userId")
    reply_token = event.get("replyToken")
    if not source_id:
        return

    display_name = None
    if source_type == "group" and user_id:
        display_name = await get_member_profile(source_id, user_id)

    text = msg.get("text", "").strip()
    quoted_message_id = msg.get("quotedMessageId")
    mentions = await resolve_message_mentions(msg, source_id) if source_type == "group" else []

    # Learn every group participant from the stable LINE userId. This lets later
    # plain-name assignments resolve to the same person even if their display name changes.
    if source_type == "group" and user_id and display_name:
        with SessionLocal() as db:
            bind_person_identity(db, display_name, user_id, display_name)
            db.commit()
    with SessionLocal() as db:
        if db.scalar(select(Message).where(Message.line_message_id == msg["id"])):
            return
        db.add(Message(
            line_message_id=msg["id"], source_type=source_type, source_id=source_id,
            user_id=user_id, display_name=display_name, text=text
        ))
        db.commit()

    if source_type == "user" and settings.owner_line_user_id.strip().upper() == "TEMP":
        await push_text(user_id, f"เชื่อมต่อสำเร็จค่ะ\nLINE User ID ของคุณคือ:\n{user_id}\n\nให้นำค่านี้ไปใส่ใน Render ที่ OWNER_LINE_USER_ID แล้ว Deploy ใหม่ค่ะ")
        return

    if source_type == "user" and user_id == settings.owner_line_user_id:
        await handle_owner_command(user_id, text)
        return

    if source_type != "group":
        return

    extraction = await asyncio.to_thread(extract_task, text, display_name)

    # v0.5.4: if the user used LINE's quote/reply feature on one of the
    # assistant's reminder messages, resolve the exact task by quotedMessageId.
    # This is much more reliable than guessing from display names such as
    # "พราว" vs "Proud🤍" or from short reply text.
    if quoted_message_id:
        changed = await handle_quoted_task_reply(
            source_id, user_id, display_name, quoted_message_id, extraction, text
        )
        if changed is not None:
            await acknowledge_task_reply(reply_token, source_id, extraction.status_signal)
            if changed and settings.owner_status_updates and settings.owner_line_user_id:
                await push_text(settings.owner_line_user_id, changed)
            return

    if extraction.status_signal != "none":
        changed = await try_update_task_from_status(source_id, display_name, extraction, text)
        if changed is not None:
            await acknowledge_task_reply(reply_token, source_id, extraction.status_signal)
            if changed and settings.owner_status_updates and settings.owner_line_user_id:
                await push_text(settings.owner_line_user_id, changed)
            return

    if extraction.is_task_reply:
        with SessionLocal() as db:
            tasks = open_tasks(db, source_id)
            canonical_sender = resolve_canonical_name(db, display_name) or display_name
            target = choose_status_target(tasks, canonical_sender, extraction.assignee_name, extraction.related_task_hint, user_id)
            if target:
                record_task_event(
                    db, target, "COMMENT", actor_name=canonical_sender, actor_user_id=user_id,
                    text=text, new_status=target.status, commit=False,
                )
                target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender or '-'}: {text}").strip()
                db.commit()
                print("task comment recorded:", target.task_code)
                await acknowledge_task_reply(reply_token, source_id, extraction.status_signal)
                if settings.owner_status_updates and settings.owner_line_user_id:
                    await push_text(
                        settings.owner_line_user_id,
                        f"มีการตอบกลับงานแล้วค่ะ\n\n"
                        f"{target.task_code} {target.title}\n"
                        f"ผู้ตอบ: {canonical_sender or '-'}\n"
                        f"ข้อความ: {text}\n\n"
                        f"สถานะยังเป็น: {STATUS_THAI.get(target.status, target.status)}"
                    )
                return

    if extraction.is_task and extraction.confidence >= settings.auto_create_confidence:
        primary_mention = select_primary_mention(mentions, extraction.assignee_name)
        mention_name = primary_mention.get("display_name") if primary_mention else None
        mention_user_id = primary_mention.get("user_id") if primary_mention else None
        with SessionLocal() as db:
            task = create_task(
                db, source_id, msg["id"], extraction, source_text=text,
                actor_name=display_name, actor_user_id=user_id,
                assignee_name_override=mention_name, assignee_user_id=mention_user_id,
            )
            # If AI found a plain-text assignee and that person is already known in
            # People Registry, attach their stable LINE userId too.
            if not task.assignee_user_id and task.assignee_name:
                person = db.scalar(select(Person).where(Person.canonical_name == task.assignee_name))
                if person and person.line_user_id:
                    task.assignee_user_id = person.line_user_id
                    db.commit()
                    db.refresh(task)
            confirmation = format_task(task)
        print("task created:", task.task_code, task.title, "assignee_user_id=", bool(task.assignee_user_id))
        if settings.owner_task_ack and settings.owner_line_user_id:
            identity_note = "\nผูก LINE ผู้รับผิดชอบแล้วค่ะ" if task.assignee_user_id else "\nยังไม่ได้ผูก LINE ผู้รับผิดชอบค่ะ"
            await push_text(settings.owner_line_user_id, "รับเรื่องติดตามจากกลุ่มแล้วค่ะ\n\n" + confirmation + identity_note)


async def handle_quoted_task_reply(
    group_id: str, user_id: str | None, sender_name: str | None, quoted_message_id: str, extraction, text: str
) -> str | None:
    """Apply a LINE quote-reply to the exact task whose reminder was quoted.

    Returns:
      None  -> quote wasn't one of our tracked task messages; continue normal matching.
      ""    -> handled but owner notification disabled/not needed.
      str   -> owner notification text.
    """
    with SessionLocal() as db:
        link = db.scalar(select(OutboundTaskMessage).where(
            OutboundTaskMessage.line_message_id == str(quoted_message_id),
            OutboundTaskMessage.group_id == group_id,
        ))
        if not link:
            return None
        target = db.get(Task, link.task_id)
        if not target or target.status not in {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"}:
            return ""

        canonical_sender = resolve_canonical_name(db, sender_name) or sender_name or "-"

        # Once somebody quote-replies to a reminder addressed to this task, bind the
        # LINE user ID to the task and learn their display-name alias automatically.
        if user_id:
            target.assignee_user_id = user_id
            if target.assignee_name:
                person = db.scalar(select(Person).where(Person.canonical_name == target.assignee_name))
                if not person:
                    person = Person(canonical_name=target.assignee_name, line_user_id=user_id)
                    db.add(person)
                    db.flush()
                elif not person.line_user_id:
                    person.line_user_id = user_id
                alias_key = ''.join((sender_name or '').strip().lower().split())
                if alias_key and not db.scalar(select(PersonAlias).where(PersonAlias.normalized_alias == alias_key)):
                    db.add(PersonAlias(person_id=person.id, alias=sender_name or target.assignee_name, normalized_alias=alias_key))
                canonical_sender = target.assignee_name

        mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
        new_status = mapping.get(extraction.status_signal)
        old_status = target.status

        if new_status:
            target.status = new_status
            if new_status == "COMPLETED":
                target.next_reminder_at = None
            elif new_status == "WAITING":
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=6)
            else:
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
            event_type = "QUOTED_STATUS_REPLY"
        else:
            # Any genuine human response should suppress an immediate repeat chase.
            # Keep the task open, record the comment, and snooze by the normal repeat window.
            target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
            event_type = "QUOTED_COMMENT_REPLY"

        target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender}: {text}").strip()
        record_task_event(
            db, target, event_type, actor_name=canonical_sender, actor_user_id=user_id, text=text,
            old_status=old_status, new_status=target.status, commit=False,
        )
        db.commit()
        db.refresh(target)

        status_th = STATUS_THAI.get(target.status, target.status)
        return (
            f"มีการตอบกลับงานแล้วค่ะ\n\n"
            f"{target.task_code} {target.title}\n"
            f"ผู้ตอบ: {canonical_sender}\n"
            f"ข้อความ: {text}\n"
            f"สถานะ: {status_th}"
        )


async def try_update_task_from_status(group_id: str, sender_name: str | None, extraction, text: str) -> str | None:
    with SessionLocal() as db:
        tasks = open_tasks(db, group_id)
        canonical_sender = resolve_canonical_name(db, sender_name) or sender_name
        canonical_extracted = resolve_canonical_name(db, extraction.assignee_name) or extraction.assignee_name
        target = choose_status_target(tasks, canonical_sender, canonical_extracted, extraction.related_task_hint, user_id)
        if not target:
            return None

        mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
        new_status = mapping.get(extraction.status_signal)
        if not new_status:
            return None

        old_status = target.status
        target.status = new_status
        target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender or '-'}: {text}").strip()
        if new_status == "COMPLETED":
            target.next_reminder_at = None
        elif target.due_at and datetime.utcnow() > target.due_at:
            target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
        else:
            target.next_reminder_at = datetime.utcnow() + timedelta(hours=6)
        record_task_event(
            db, target, "STATUS_REPLY", actor_name=canonical_sender, text=text,
            old_status=old_status, new_status=new_status, commit=False,
        )
        db.commit()
        db.refresh(target)

        if old_status == new_status and new_status != "COMPLETED":
            return ""
        status_th = STATUS_THAI.get(new_status, new_status)
        return (
            f"อัปเดตงานจากบทสนทนาในกลุ่มแล้วค่ะ\n\n"
            f"{target.task_code} {target.title}\n"
            f"ผู้ตอบ: {canonical_sender or '-'}\nสถานะใหม่: {status_th}"
        )


async def handle_owner_command(user_id: str, text: str):
    raw = text.strip()
    low = raw.lower()

    if low.startswith("ปิด ") or low.startswith("เสร็จ "):
        parts = raw.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        with SessionLocal() as db:
            task = get_task_by_code(db, code)
            if not task:
                await push_text(user_id, f"ไม่พบงาน {code} ค่ะ")
                return
            old_status = task.status
            task.status = "COMPLETED"
            task.next_reminder_at = None
            record_task_event(
                db, task, "OWNER_STATUS_CHANGE", actor_name=settings.owner_display_name, actor_user_id=user_id,
                text="ปิดงานจาก LINE ส่วนตัว", old_status=old_status, new_status="COMPLETED", commit=False,
            )
            db.commit()
            await push_text(user_id, f"ปิดงาน {task.task_code} เรียบร้อยค่ะ\n{task.title}")
        return

    if low.startswith("ประวัติ "):
        code = raw.split(maxsplit=1)[1].strip()
        with SessionLocal() as db:
            task = get_task_by_code(db, code)
            if not task:
                await push_text(user_id, f"ไม่พบงาน {code} ค่ะ")
                return
            events = task_timeline(db, task)
        lines = [f"ประวัติ {task.task_code} — {task.title}", ""]
        for ev in events[-12:]:
            when = ev.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m %H:%M")
            actor = ev.actor_name or "System"
            detail = ev.text or ev.event_type
            lines.append(f"• {when} | {actor} | {detail}")
        await push_text(user_id, "\n".join(lines))
        return

    if low.startswith("รวมชื่อ "):
        body = raw[len("รวมชื่อ "):].strip()
        if "=" not in body:
            await push_text(user_id, "รูปแบบ: รวมชื่อ ชื่อเดิม = ชื่อมาตรฐาน\nเช่น รวมชื่อ Tong Thanakrit = ต้น")
            return
        alias_name, canonical_name = [x.strip() for x in body.split("=", 1)]
        with SessionLocal() as db:
            person, updated = set_person_alias(db, alias_name, canonical_name)
        await push_text(user_id, f"ตั้งชื่อมาตรฐานแล้วค่ะ\n{alias_name} → {person.canonical_name}\nปรับงานเดิม {updated} รายการ")
        return

    if low.startswith("งานของ "):
        name = raw.split(" ", 1)[1].strip()
        with SessionLocal() as db:
            tasks = search_open_tasks(db, assignee=name, status="ACTIVE")
        await send_task_list(user_id, f"งานค้างของ {name}", tasks)
        return
    if low.startswith("โครงการ "):
        project = raw.split(" ", 1)[1].strip()
        with SessionLocal() as db:
            tasks = search_open_tasks(db, project=project, status="ACTIVE")
        await send_task_list(user_id, f"งานค้างโครงการ {project}", tasks)
        return
    if low.startswith("ค้นหา "):
        query = raw.split(" ", 1)[1].strip()
        with SessionLocal() as db:
            tasks = search_open_tasks(db, query=query, status="ACTIVE")
        await send_task_list(user_id, f"ผลค้นหา {query}", tasks)
        return

    if "สรุปเช้า" in low or "brief เช้า" in low:
        await send_daily_brief(user_id, "morning", force=True)
        return
    if "สรุปเย็น" in low or "brief เย็น" in low:
        await send_daily_brief(user_id, "evening", force=True)
        return
    if "พรุ่งนี้" in low:
        with SessionLocal() as db:
            tasks = tasks_due_tomorrow(db)
        await send_task_list(user_id, "งานที่ครบกำหนดพรุ่งนี้", tasks)
        return
    if "รอข้อมูล" in low or "รอ supplier" in low or "รอซัพพลายเออร์" in low:
        with SessionLocal() as db:
            tasks = waiting_tasks(db)
        await send_task_list(user_id, "งานที่กำลังรอข้อมูล/บุคคลอื่น", tasks)
        return
    if "เลยกำหนด" in low or "เกินกำหนด" in low:
        with SessionLocal() as db:
            tasks = overdue_tasks(db)
        await send_task_list(user_id, "งานที่เลยกำหนด", tasks)
        return
    if "วันนี้" in low:
        with SessionLocal() as db:
            tasks = tasks_due_today(db)
        await send_task_list(user_id, "งานที่ครบกำหนดวันนี้", tasks)
        return
    if "เสร็จแล้ว" in low or "งานที่ปิด" in low:
        with SessionLocal() as db:
            tasks = completed_tasks(db, 10)
        await send_task_list(user_id, "งานที่ปิดล่าสุด", tasks)
        return
    if any(k in low for k in ["งานค้าง", "ต้องตาม", "สรุปงาน", "ทั้งหมด"]):
        with SessionLocal() as db:
            tasks = open_tasks(db)
        await send_task_list(user_id, "สรุปงานติดตามที่ยังไม่ปิด", tasks)
        return

    await push_text(user_id,
        "สั่งได้แบบนี้ค่ะ\n"
        "• สรุปงานค้าง\n"
        "• วันนี้มีอะไรต้องตาม\n"
        "• พรุ่งนี้มีอะไรต้องตาม\n"
        "• งานเลยกำหนด\n"
        "• งานรอข้อมูล\n"
        "• งานที่ปิดแล้ว\n"
        "• งานของ ต้น\n"
        "• โครงการ ปากน้ำประแส\n"
        "• ค้นหา กล้อง\n"
        "• ประวัติ FU-xxxxxx-xxxx\n"
        "• รวมชื่อ Tong Thanakrit = ต้น\n"
        "• สรุปเช้า / สรุปเย็น\n"
        "• ปิด FU-xxxxxx-xxxx"
    )


async def send_task_list(user_id: str, title: str, tasks: list[Task]):
    if not tasks:
        await push_text(user_id, f"{title}: ไม่มีรายการค่ะ")
        return
    lines = [f"{title}ค่ะ", ""]
    for t in tasks[:15]:
        lines.append(format_task(t))
        lines.append("")
    if len(tasks) > 15:
        lines.append(f"และมีอีก {len(tasks)-15} รายการ")
    await push_text(user_id, "\n".join(lines))


def in_quiet_hours() -> bool:
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    start, end = settings.quiet_hour_start, settings.quiet_hour_end
    if start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


async def reminder_scan(force: bool = False):
    stats = {"due": 0, "sent": 0, "failed": 0, "skipped_quiet": 0}
    now = datetime.utcnow()

    # Do not lose a due reminder during quiet hours. It remains due and will be
    # delivered by the first tick after quiet hours end.
    if not force and in_quiet_hours():
        with SessionLocal() as db:
            stats["due"] = db.query(Task).filter(
                Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
                Task.next_reminder_at != None,
                Task.next_reminder_at <= now,
            ).count()
        stats["skipped_quiet"] = stats["due"]
        print("reminder scan skipped: quiet hours, due=", stats["due"] )
        return stats

    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(
            Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
            Task.next_reminder_at != None,
            Task.next_reminder_at <= now,
        ).order_by(Task.next_reminder_at.asc())).all())
        stats["due"] = len(tasks)

        for t in tasks:
            try:
                became_overdue = bool(t.due_at and now > t.due_at and t.status != "OVERDUE")
                if t.due_at and now > t.due_at:
                    t.status = "OVERDUE"

                assignee = t.assignee_name.strip() if t.assignee_name else "ทีม"
                use_mention = bool(t.assignee_user_id)
                greeting = "{assignee}คะ" if use_mention else (f"{assignee}คะ" if assignee != "ทีม" else "ทีมคะ")
                is_pre_due = bool(t.due_at and now < t.due_at)

                if is_pre_due:
                    due_text = format_due_local(t)
                    body = (
                        f"{greeting} ขอแจ้งเตือนเรื่อง{t.title}ค่ะ\n"
                        f"งานนี้กำหนด {due_text} ถ้าเรียบร้อยแล้วแจ้ง{settings.owner_display_name}ได้เลยนะคะ"
                    )
                    # After the advance reminder, the next check is the due time itself.
                    t.next_reminder_at = t.due_at
                elif t.status == "OVERDUE":
                    if t.reminder_count >= settings.escalation_after_reminders:
                        body = (
                            f"{greeting} ขออัปเดตเรื่อง{t.title}อีกครั้งค่ะ\n"
                            f"ตอนนี้เลยกำหนดแล้ว หากยังติดปัญหาตรงไหน รบกวนแจ้งสาเหตุและวันที่คาดว่าจะเรียบร้อยให้{settings.owner_display_name}ทราบด้วยนะคะ"
                        )
                    else:
                        body = (
                            f"{greeting} ขออัปเดตเรื่อง{t.title}หน่อยค่ะ\n"
                            f"ตอนนี้เลยกำหนดแล้ว หากยังติดอะไรอยู่แจ้ง{settings.owner_display_name}ไว้ได้เลยนะคะ"
                        )
                    t.next_reminder_at = now + timedelta(hours=settings.reminder_repeat_hours)
                elif t.status == "WAITING":
                    body = (
                        f"{greeting} ขออัปเดตเรื่อง{t.title}หน่อยค่ะ\n"
                        f"เรื่องที่รออยู่มีความคืบหน้าเพิ่มเติมไหมคะ"
                    )
                    t.next_reminder_at = now + timedelta(hours=6)
                elif t.status == "IN_PROGRESS":
                    body = (
                        f"{greeting} ขออัปเดตความคืบหน้าเรื่อง{t.title}หน่อยค่ะ\n"
                        f"ถ้าเรียบร้อยแล้ว รบกวนแจ้ง{settings.owner_display_name}ด้วยนะคะ"
                    )
                    t.next_reminder_at = now + timedelta(hours=6)
                else:
                    body = (
                        f"{greeting} ขออัปเดตเรื่อง{t.title}หน่อยค่ะ\n"
                        f"ถ้าเรียบร้อยแล้ว รบกวนแจ้ง{settings.owner_display_name}ด้วยนะคะ"
                    )
                    t.next_reminder_at = now + timedelta(hours=settings.reminder_repeat_hours)

                if use_mention:
                    try:
                        sent_message_id = await push_text_mention(t.group_id, body, t.assignee_user_id)
                    except Exception as mention_exc:
                        print("mention reminder failed; fallback plain text:", t.task_code, repr(mention_exc))
                        plain_body = body.replace("{assignee}", assignee)
                        sent_message_id = await push_text(t.group_id, plain_body)
                        body = plain_body
                else:
                    sent_message_id = await push_text(t.group_id, body)
                if sent_message_id:
                    db.add(OutboundTaskMessage(
                        line_message_id=sent_message_id, task_id=t.id, group_id=t.group_id,
                        message_kind="PRE_DUE" if is_pre_due else "REMINDER",
                    ))
                # Advance reminders are logged, but do not count toward escalation.
                # `reminder_count` remains a count of actual follow-ups at/after due time.
                if not is_pre_due:
                    t.reminder_count += 1
                t.last_reminded_at = now
                record_task_event(
                    db, t, "PRE_DUE_REMINDER_SENT" if is_pre_due else "REMINDER_SENT",
                    actor_name="LINE Follow-up Assistant",
                    text=body, old_status=None, new_status=t.status, commit=False,
                )
                db.commit()
                stats["sent"] += 1
                print("reminder sent:", t.task_code, t.status, "pre_due=", is_pre_due, "count=", t.reminder_count)

                if settings.owner_escalation_alerts and settings.owner_line_user_id:
                    if became_overdue:
                        await push_text(
                            settings.owner_line_user_id,
                            f"งานเลยกำหนดแล้วค่ะ\n\n{format_task(t)}"
                        )
                    elif t.status == "OVERDUE" and t.reminder_count >= settings.escalation_after_reminders:
                        await push_text(
                            settings.owner_line_user_id,
                            f"งานนี้ตามแล้ว {t.reminder_count} ครั้งและยังไม่ปิดค่ะ\n\n{format_task(t)}"
                        )
            except Exception as exc:
                db.rollback()
                stats["failed"] += 1
                print("reminder push failed:", t.task_code, repr(exc))

    return stats


async def catch_up_daily_briefs():
    """Send today's brief after its scheduled time if it has not been sent yet.

    This makes a 5-minute external tick reliable even when the Render service
    was sleeping at the exact scheduled minute.
    """
    result = {"morning": False, "evening": False}
    if not settings.daily_brief_enabled or not settings.owner_line_user_id:
        return result

    local = datetime.now(ZoneInfo(settings.timezone))
    morning_at = local.replace(
        hour=settings.morning_brief_hour, minute=settings.morning_brief_minute,
        second=0, microsecond=0
    )
    evening_at = local.replace(
        hour=settings.evening_brief_hour, minute=settings.evening_brief_minute,
        second=0, microsecond=0
    )

    with SessionLocal() as db:
        morning_key = f"daily-brief:morning:{local.strftime('%Y-%m-%d')}"
        evening_key = f"daily-brief:evening:{local.strftime('%Y-%m-%d')}"
        morning_needed = local >= morning_at and not event_exists(db, morning_key)
        evening_needed = local >= evening_at and not event_exists(db, evening_key)

    # Do not send a missed morning brief in the evening. Once evening time has
    # arrived, the evening brief is the useful catch-up summary for the owner.
    if local < evening_at and morning_needed:
        await morning_brief()
        result["morning"] = True
    if evening_needed:
        await evening_brief()
        result["evening"] = True
    return result


async def morning_brief():
    if settings.owner_line_user_id:
        await send_daily_brief(settings.owner_line_user_id, "morning")


async def evening_brief():
    if settings.owner_line_user_id:
        await send_daily_brief(settings.owner_line_user_id, "evening")


async def send_daily_brief(user_id: str, period: str, force: bool = False):
    local = datetime.now(ZoneInfo(settings.timezone))
    key = f"daily-brief:{period}:{local.strftime('%Y-%m-%d')}"
    with SessionLocal() as db:
        if not force and event_exists(db, key):
            return
        counts = brief_counts(db)
        overdue = overdue_tasks(db)
        today = tasks_due_today(db)
        tomorrow = tasks_due_tomorrow(db)

        if period == "morning":
            title = f"สรุปงานเช้า {local.strftime('%d/%m/%Y')}"
            lines = [
                title,
                "",
                f"งานค้างทั้งหมด: {counts['open']} งาน",
                f"ครบกำหนดวันนี้: {counts['today']} งาน",
                f"เลยกำหนด: {counts['overdue']} งาน",
                f"รอข้อมูล/บุคคลอื่น: {counts['waiting']} งาน",
                f"ครบกำหนดพรุ่งนี้: {counts['tomorrow']} งาน",
            ]
            focus = overdue[:3] + [t for t in today if t not in overdue][:5]
            if focus:
                lines += ["", "เรื่องที่ควรดูเป็นอันดับแรก:"]
                for t in focus[:8]:
                    lines.append(f"• {t.task_code} {t.title} — {STATUS_THAI.get(t.status, t.status)}")
        else:
            title = f"สรุปงานเย็น {local.strftime('%d/%m/%Y')}"
            lines = [
                title,
                "",
                f"ปิดงานวันนี้: {counts['completed_today']} งาน",
                f"ยังค้างทั้งหมด: {counts['open']} งาน",
                f"เลยกำหนด: {counts['overdue']} งาน",
                f"ต้องตามพรุ่งนี้: {counts['tomorrow']} งาน",
            ]
            focus = overdue[:3] + tomorrow[:5]
            if focus:
                lines += ["", "เรื่องที่ต้องตามต่อ:"]
                for t in focus[:8]:
                    lines.append(f"• {t.task_code} {t.title} — {STATUS_THAI.get(t.status, t.status)}")

        await push_text(user_id, "\n".join(lines))
        if not force:
            record_event(db, key)
