import random
import json
import re
from datetime import datetime, timedelta
import asyncio
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException, Header, Query
from sqlalchemy import select, func
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.responses import HTMLResponse, RedirectResponse
from html import escape
from apscheduler.triggers.cron import CronTrigger

from db import Base, engine, SessionLocal, ensure_people_registry_schema, ensure_task_event_schema, ensure_task_progress_schema
from models import Message, Task, OutboundTaskMessage, Person, PersonAlias, TaskEvent
from config import settings
from line_api import verify_signature, get_member_profile, push_text, reply_text, push_text_mention
from ai import extract_task, TaskExtraction
from intent_guard import classify_precreation_guard
from intent_engine import classify_message_intent, intent_to_status_signal, is_direct_task_request
from service import (
    create_task, open_tasks, completed_tasks, get_task_by_code, tasks_due_today,
    tasks_due_tomorrow, overdue_tasks, waiting_tasks, completed_today,
    format_task, choose_status_target, STATUS_THAI, brief_counts,
    event_exists, record_event, search_open_tasks, task_stats,
    resolve_canonical_name, set_person_alias, list_people, record_task_event,
    task_timeline, backfill_task_created_events, bind_person_identity, update_person_profile, merge_people, add_alias_to_person, delete_person_alias,
    resolve_assignee_from_text, rank_status_targets, rank_status_targets_with_history, choose_status_target_with_history,
    contextual_followup_text, summarize_progress_update, update_task_progress_snapshot, task_progress_context, task_reference_label, recent_reminder_context_target, delete_task_by_code,
    find_existing_followup_task, find_task_for_explicit_query, extract_followup_commitment_at
)

VERSION = "0.6.31"
app = FastAPI(title="LINE Follow-up Assistant", version=VERSION)
scheduler = AsyncIOScheduler(timezone=settings.timezone)


@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    ensure_people_registry_schema()
    ensure_task_event_schema()
    ensure_task_progress_schema()
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
        "people_registry": True, "people_registry_profile": True,
        "safe_task_matching": True, "people_merge": True, "people_merge_nojs": True,
        "people_multi_alias": True, "people_alias_crud": True,
        "task_resolution_engine": True, "assignee_context_resolution": True,
        "status_semantic_core_matching": True, "message_deduplication": True,
        "local_status_fallback": True, "unmatched_status_ack": False,
        "fast_local_status_path": True, "ai_failure_status_fallback": True,
        "preprocessing_failure_ack": False, "best_effort_group_preprocessing": True,
        "business_concept_matching": True, "safe_status_notifications": True,
        "status_update_transaction_guard": True,
        "task_history_matching": True, "cross_group_unique_fallback": True, "candidate_score_logging": True,
        "business_first_status_resolution": True, "vehicle_insurance_resolution": True, "duplicate_business_task_resolution": True,
        "task_state_continuity": True, "context_aware_reminders": True, "waiting_followup_memory": True,
        "quoted_reply_identity_guard": True, "quoted_reply_task_memory": True,
        "quoted_reply_atomic_status": True, "quoted_reply_identity_best_effort": True,
        "mixed_progress_waiting_resolution": True,
        "working_hours_followup": True, "followup_window": "08:30-17:30",
        "daily_followup_limits": True, "staggered_group_followups": True,
        "task_context_integrity_guard": True, "cross_topic_memory_guard": True,
        "source_truth_reminders": True, "identity_only_substantive_match_disabled": True,
        "trust_recovery_mode": True, "public_technical_fallback_disabled": True,
        "multi_topic_update_guard": True, "human_group_ack": "state_change_only",
        "silent_ambiguity_mode": True, "recent_reminder_context": True,
        "passive_task_learning": True, "quiet_ack_policy": True,
        "public_exception_fallback_disabled": True,
        "task_creation_guard": True, "leave_notice_filter": True,
        "owner_hard_delete_command": True,
        "intent_classification_layer": True,
        "completion_safety_guard": True,
        "duplicate_followup_prevention": True,
        "question_never_completes": True,
        "negation_never_completes": True,
        "milestone_completion_guard": True,
        "human_directed_request_guard": True, "mentioned_assignee_query_routing": True,
        "unrelated_status_response_guard": True,
        "date_aware_followup": True, "future_commitment_memory": True,
        "weekday_followup_scheduling": True,
        "task_event_message_trace": True,
        "structured_task_progress": True, "progressive_followup_context": True,
        "task_local_progress_snapshot": True, "progress_snapshot_dashboard": True,
        "humanized_context_followup": True,
        "reminder_queue_self_healing": True,
        "missing_next_reminder_repair": True,
        "overdue_waiting_reconciliation": True, "state_driven_message_style": True,
        "generic_followup_template_disabled": True, "deterministic_followup_variety": True,
        "short_contextual_reminders": True,
    }


def infer_local_status_signal(text: str | None) -> str:
    """Safety-first deterministic status signal derived from message intent.

    Questions and negations can never produce a completed signal. Bare words such as
    ``เรียบร้อย`` are intentionally treated as progress, not whole-task completion.
    """
    result = classify_message_intent(text)
    compact = "".join((text or "").lower().split())
    if result.intent == "PROGRESS_UPDATE" and "รอ" in compact:
        return "waiting"
    return intent_to_status_signal(result.intent)



TOPIC_HINT_TERMS = (
    "ชุมแสง", "ตาสิทธิ์", "ประแส", "ระยอง", "gps", "มิเตอร์", "กล้อง", "cctv",
    "flow account", "tsp", "futong", "fiber", "ไฟเบอร์", "po", "ประกัน", "datasheet",
    "เทศบาล", "อบต.", "อบต", "สายไฟ", "ตู้", "ครุภัณฑ์",
)


def split_operational_update_segments(text: str | None) -> list[str]:
    """Split a long human update into topic-sized chunks without inventing meaning.

    A single LINE reply may update several projects at once. Treating the whole message
    as one status hint is a major source of cross-task contamination. We split only on
    strong human boundaries (blank lines / explicit 'ส่วน...' transitions) and keep the
    original wording intact.
    """
    raw = (text or "").strip()
    if len(raw) < 120:
        return []
    parts = [x.strip() for x in re.split(r"\n\s*\n+", raw) if x.strip()]
    expanded: list[str] = []
    transition = re.compile(r"(?=(?:ส่วน(?:งาน|ของ)?|แล้วก็จะมีในส่วนของ|อีกส่วน(?:หนึ่ง)?|ส่วนประแส|ส่วนชุมแสง)\s*)", re.I)
    for part in parts:
        subs = [x.strip(" -:;,\n") for x in transition.split(part) if x.strip(" -:;,\n")]
        if len(subs) > 1:
            expanded.extend(subs)
        else:
            expanded.append(part)
    # Avoid fragments too short to carry topic meaning.
    expanded = [x for x in expanded if len(x) >= 18]
    if len(expanded) < 2:
        return []
    anchors = []
    for seg in expanded:
        low = seg.lower()
        anchors.append({term for term in TOPIC_HINT_TERMS if term in low})
    nonempty = [a for a in anchors if a]
    distinct = set().union(*nonempty) if nonempty else set()
    # Require at least two distinct topic hints before declaring a multi-topic update.
    return expanded if len(distinct) >= 2 else []


async def handle_multi_topic_update(
    group_id: str,
    user_id: str | None,
    sender_name: str | None,
    reply_token: str | None,
    text: str,
) -> bool:
    """Safely ingest a multi-topic reply without mixing projects together.

    Only strong title/project matches are stored. Unmatched segments remain unattached
    and are reported privately to the owner. The group receives one natural thank-you,
    never internal Task/matcher language.
    """
    segments = split_operational_update_segments(text)
    if not segments:
        return False

    matched: list[tuple[str, str]] = []
    unmatched: list[str] = []
    with SessionLocal() as db:
        tasks = open_tasks(db, group_id)
        canonical_sender = resolve_canonical_name(db, sender_name) or sender_name
        used_task_ids: set[int] = set()
        for seg in segments:
            rows = rank_status_targets(tasks, canonical_sender, None, seg, user_id)
            best = rows[0] if rows else None
            second = rows[1] if len(rows) > 1 else None
            # Strong content only. Identity must not rescue a weak topic match here.
            if not best or best["content"] < 0.75:
                unmatched.append(seg)
                continue
            if second and second["content"] >= 0.65 and (best["content"] - second["content"]) < 0.15:
                unmatched.append(seg)
                continue
            target = best["task"]
            if target.id in used_task_ids:
                # Two different sections mapping to the same task is suspicious; keep
                # the later section unattached instead of merging contexts silently.
                unmatched.append(seg)
                continue
            used_task_ids.add(target.id)
            record_task_event(
                db, target, "COMMENT", actor_name=canonical_sender, actor_user_id=user_id,
                text=seg, new_status=target.status, commit=False,
            )
            memory = summarize_progress_update(seg)
            if memory:
                record_task_event(
                    db, target, "TASK_MEMORY_UPDATED", actor_name=canonical_sender, actor_user_id=user_id,
                    text=memory, old_status=target.status, new_status=target.status, commit=False,
                )
            update_task_progress_snapshot(
                db, target, seg, actor_name=canonical_sender, actor_user_id=user_id,
                confidence=float(best["content"]), commit=False,
            )
            target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender or '-'}: {seg}").strip()
            matched.append((target.task_code, target.title))
        db.commit()

    # Quiet-by-default: multi-topic operational chatter is learned silently.
    # Repetitive acknowledgements after every human update made the assistant feel
    # robotic and discouraged replies. Diagnostics stay private with the owner.
    print("multi-topic public acknowledgement suppressed", {"matched": len(matched), "unmatched": len(unmatched)})

    if settings.owner_status_updates and settings.owner_line_user_id:
        lines = ["สรุปข้อความอัปเดตหลายเรื่องค่ะ"]
        if matched:
            lines.append("\nผูกข้อมูลได้:")
            lines.extend(f"• {code} {title}" for code, title in matched)
        if unmatched:
            lines.append("\nส่วนที่ยังไม่ผูกอัตโนมัติ:")
            lines.extend(f"• {seg[:180]}" for seg in unmatched)
        lines.append("\nส่วนที่ยังไม่แน่ใจจะไม่ถูกนำไปปนกับงานอื่นค่ะ")
        await safe_push_text(settings.owner_line_user_id, "\n".join(lines), label="multi-topic owner summary")
    print("multi-topic update:", {"matched": matched, "unmatched": len(unmatched)})
    return True


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
        "progress_summary": t.progress_summary,
        "waiting_on": t.waiting_on,
        "next_action": t.next_action,
        "last_progress_at": (
            t.last_progress_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M")
            if t.last_progress_at else None
        ),
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


@app.post("/api/people/{person_id}/aliases")
def api_people_add_alias(person_id: int, alias: str = Query(...), token: str | None = Query(default=None)):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            row = add_alias_to_person(db, person_id, alias)
            return {"ok": True, "alias": row.alias}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/people/{person_id}/aliases")
def api_people_delete_alias(person_id: int, alias: str = Query(...), token: str | None = Query(default=None)):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            delete_person_alias(db, person_id, alias)
            return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/people/{person_id}/profile")
def api_people_profile(
    person_id: int, canonical_name: str = Query(...), call_name: str = Query(default=""),
    role: str = Query(default="EMPLOYEE"), active: bool = Query(default=True),
    display_name: str = Query(default=""), token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            person, updated = update_person_profile(
                db, person_id, canonical_name=canonical_name, call_name=call_name or None,
                role=role, active=active, display_name=display_name or None,
            )
            return {
                "ok": True, "person_id": person.id, "canonical_name": person.canonical_name,
                "updated_tasks": updated,
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/people/merge")
def api_people_merge(
    person_a_id: int = Query(...), person_b_id: int = Query(...),
    token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            person, updated = merge_people(db, person_a_id, person_b_id)
            return {
                "ok": True, "person_id": person.id, "canonical_name": person.canonical_name,
                "line_bound": bool(person.line_user_id), "updated_tasks": updated,
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/dashboard/people/merge-confirm", response_class=HTMLResponse)
def dashboard_people_merge_confirm(
    person_a_id: int = Query(...), person_b_id: int = Query(...), token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    with SessionLocal() as db:
        people = {p["id"]: p for p in list_people(db)}
    pa, pb = people.get(person_a_id), people.get(person_b_id)
    if not pa or not pb:
        raise HTTPException(status_code=404, detail="person not found")
    # Safety preview only; actual merge rules stay in service.merge_people.
    final_name = pb["canonical_name"] if pa["line_bound"] and not pb["line_bound"] else (pa["canonical_name"] if pb["line_bound"] and not pa["line_bound"] else pa["canonical_name"])
    t = escape(token or "", quote=True)
    confirm_url = f"/dashboard/people/merge-do?person_a_id={person_a_id}&person_b_id={person_b_id}&token={t}"
    back_url = f"/dashboard?token={t}"
    return HTMLResponse(f"""<!doctype html><html lang='th'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>ยืนยันรวมบุคคล</title><style>body{{font-family:Arial,sans-serif;background:#f6f7f8;color:#202124}}.box{{max-width:620px;margin:60px auto;background:#fff;padding:24px;border:1px solid #ddd;border-radius:14px}}a{{display:inline-block;padding:10px 14px;border-radius:8px;text-decoration:none;margin-right:8px}}.ok{{background:#0f9d58;color:white}}.cancel{{background:#6b7280;color:white}}code{{background:#f2f2f2;padding:2px 5px;border-radius:4px}}</style></head><body><div class='box'>
    <h2>ยืนยันการรวมบุคคล</h2><p>ต้องการรวม <b>{escape(pa['canonical_name'])}</b> กับ <b>{escape(pb['canonical_name'])}</b> เป็นบุคคลเดียวกันหรือไม่?</p>
    <p>ชื่อมาตรฐานหลังรวม: <b>{escape(final_name)}</b></p><p>LINE ID ที่ผูกไว้จะถูกเก็บตามกฎความปลอดภัย และ Task เดิมจะถูกปรับอัตโนมัติ</p>
    <a class='ok' href='{confirm_url}'>ยืนยันการรวม</a><a class='cancel' href='{back_url}'>ยกเลิก</a></div></body></html>""")


@app.get("/dashboard/people/merge-do")
def dashboard_people_merge_do(
    person_a_id: int = Query(...), person_b_id: int = Query(...), token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    try:
        with SessionLocal() as db:
            merge_people(db, person_a_id, person_b_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(url=f"/dashboard?token={token}", status_code=303)


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
            f"<td>{escape(d['title'])}"
            + (f"<br><small><b>ล่าสุด:</b> {escape(d['progress_summary'])}</small>" if d.get('progress_summary') else "")
            + (f"<br><small><b>ถัดไป:</b> {escape(d['next_action'])}</small>" if d.get('next_action') else "")
            + "</td>"
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
        bound = "ผูกแล้ว" if p["line_bound"] else "ยังไม่ผูก"
        active_label = "ใช้งาน" if p["active"] else "ปิดใช้งาน"
        uid_short = (p["line_user_id"][:10] + "…") if p["line_user_id"] else "-"
        suggestions = p.get("duplicate_suggestions") or []
        merge_buttons = ""
        for suggestion in suggestions[:3]:
            merge_url = (
                f'/dashboard/people/merge-confirm?person_a_id={p["id"]}'
                f'&person_b_id={suggestion["id"]}&token={safe_token}'
            )
            merge_buttons += (
                f' <a class="mergebtn" href="{merge_url}">'
                f'รวมกับ {escape(suggestion["canonical_name"])}</a>'
            )
        people_rows.append(
            "<tr>"
            f"<td><b>{escape(p['canonical_name'])}</b><br><small>{escape(active_label)}</small></td>"
            f"<td>{escape(p['call_name'] or p['canonical_name'])}</td>"
            f"<td>{escape(p['display_name'] or '-')}</td>"
            f"<td>{escape(p['role'])}</td>"
            f"<td>{escape(bound)}<br><small>{escape(uid_short)}</small></td>"
            f"<td>{escape(aliases)}</td>"
            f"<td><button onclick=\"editPerson({p['id']})\">แก้ไข</button>{merge_buttons}</td>"
            "</tr>"
        )
    people_html = "".join(people_rows) or '<tr><td colspan="7">ยังไม่มีข้อมูลบุคลากร</td></tr>'
    people_json = json.dumps(people, ensure_ascii=False)

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
button.secondary{{background:#6b7280}} .mergebtn{{display:inline-block;padding:8px 10px;border-radius:8px;background:#0f9d58;color:white;text-decoration:none;margin-top:6px;white-space:nowrap}} table{{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden}}
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

<div class="section"><h2>ทะเบียนผู้รับผิดชอบ (People Registry)</h2>
<div class="sub">LINE User ID เป็นตัวตนหลัก หากพบรายชื่อซ้ำ ระบบจะแสดงปุ่มสีเขียว “รวมกับ …” ให้กดยืนยันรวมเป็นคนเดียวกันได้</div>
<form onsubmit="saveAlias(event)">
<input id="aliasName" placeholder="ชื่อที่พบ เช่น Tong Thanakrit" required>
<input id="canonicalName" placeholder="ชื่อมาตรฐาน เช่น พี่ต้อง" required>
<button type="submit">เพิ่ม/รวม Alias</button>
</form>
<table><thead><tr><th>ชื่อมาตรฐาน</th><th>ชื่อที่ใช้เรียก</th><th>LINE Display Name</th><th>Role</th><th>LINE ID</th><th>Aliases</th><th>จัดการ</th></tr></thead><tbody>{people_html}</tbody></table>
</div>
</main>
<div id="personModal" class="modal" onclick="if(event.target===this)closePerson()"><div class="modalbox">
<div style="display:flex;justify-content:space-between;gap:10px"><h2>แก้ไขบุคลากร</h2><button class="secondary" onclick="closePerson()">ปิด</button></div>
<form id="personForm" onsubmit="savePerson(event)" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
<input type="hidden" id="personId">
<label>ชื่อมาตรฐาน<input id="personCanonical" required style="width:100%"></label>
<label>ชื่อที่ OA ใช้เรียก<input id="personCall" style="width:100%"></label>
<label>LINE Display Name<input id="personDisplay" style="width:100%"></label>
<label>Role<select id="personRole" style="width:100%"><option>EMPLOYEE</option><option>OWNER</option><option>MANAGER</option><option>ADMIN</option></select></label>
<label style="grid-column:1/-1"><input type="checkbox" id="personActive"> ใช้งานบุคคลนี้</label>
<div style="grid-column:1/-1;border-top:1px solid #e5e7eb;padding-top:12px">
  <b>ชื่ออื่น / Aliases</b><div class="sub">เพิ่มได้หลายชื่อ เช่น MARCH, มาช, พี่มาช, พี่มาร์ท — ทั้งหมดจะชี้ไปยัง LINE ID คนเดียวกัน</div>
  <div id="personAliases" style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0"></div>
  <div style="display:flex;gap:6px"><input id="newPersonAlias" placeholder="เพิ่มชื่อเรียก เช่น พี่มาช" style="flex:1"><button type="button" onclick="addPersonAlias()">+ เพิ่มชื่อ</button></div>
</div>
<div style="grid-column:1/-1"><button type="submit">บันทึกข้อมูลบุคลากร</button></div>
</form></div></div>
<div id="timelineModal" class="modal" onclick="if(event.target===this)closeTimeline()"><div class="modalbox">
<div style="display:flex;justify-content:space-between;gap:10px"><h2 id="timelineTitle">ประวัติงาน</h2><button class="secondary" onclick="closeTimeline()">ปิด</button></div>
<div id="timelineBody"></div></div></div>
<script>
const token={token_js};
const people={people_json};
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
async function mergePerson(a,b){{
  const pa=people.find(x=>Number(x.id)===Number(a)), pb=people.find(x=>Number(x.id)===Number(b));
  if(!pa||!pb){{alert('ไม่พบข้อมูลบุคคล กรุณารีเฟรชหน้า Dashboard แล้วลองใหม่');return;}}
  const finalName=(pa.line_bound&&!pb.line_bound)?pb.canonical_name:((pb.line_bound&&!pa.line_bound)?pa.canonical_name:pa.canonical_name);
  const msg='ยืนยันรวม “'+pa.canonical_name+'” กับ “'+pb.canonical_name+'” เป็นบุคคลเดียวกัน?\\n\\nชื่อมาตรฐานหลังรวม: '+finalName+'\\nLINE ID ที่ผูกไว้จะถูกเก็บไว้ และงานเดิมจะถูกปรับอัตโนมัติ';
  if(!window.confirm(msg)) return;
  try{{
    const params=new URLSearchParams({{person_a_id:String(a),person_b_id:String(b),token:String(token||'')}});
    const r=await fetch('/api/people/merge?'+params.toString(),{{method:'POST'}});
    const body=await r.text();
    if(!r.ok){{alert('รวมบุคคลไม่สำเร็จ: '+body);return;}}
    const d=JSON.parse(body);
    alert('รวมบุคคลเรียบร้อยเป็น “'+d.canonical_name+'” และปรับงานเดิม '+d.updated_tasks+' รายการ');
    window.location.reload();
  }}catch(err){{
    console.error(err);
    alert('เกิดข้อผิดพลาดขณะรวมบุคคล กรุณารีเฟรชหน้าแล้วลองอีกครั้ง');
  }}
}}
function editPerson(id){{
  const p=people.find(x=>x.id===id); if(!p) return;
  document.getElementById('personId').value=p.id;
  document.getElementById('personCanonical').value=p.canonical_name||'';
  document.getElementById('personCall').value=p.call_name||'';
  document.getElementById('personDisplay').value=p.display_name||'';
  document.getElementById('personRole').value=p.role||'EMPLOYEE';
  document.getElementById('personActive').checked=!!p.active;
  renderPersonAliases(p);
  document.getElementById('newPersonAlias').value='';
  document.getElementById('personModal').style.display='flex';
}}
function renderPersonAliases(p){{
  const box=document.getElementById('personAliases'); box.innerHTML='';
  (p.aliases||[]).forEach(a=>{{
    const chip=document.createElement('span');
    chip.style.cssText='display:inline-flex;align-items:center;gap:5px;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:5px 9px';
    const text=document.createElement('span'); text.textContent=a; chip.appendChild(text);
    if(String(a).trim().toLowerCase()!==String(p.canonical_name||'').trim().toLowerCase()){{
      const x=document.createElement('button'); x.type='button'; x.textContent='×'; x.title='ลบ Alias';
      x.style.cssText='background:transparent;color:#6b7280;padding:0;border:0;font-size:18px';
      x.onclick=()=>removePersonAlias(a); chip.appendChild(x);
    }}
    box.appendChild(chip);
  }});
}}
async function addPersonAlias(){{
  const id=document.getElementById('personId').value; const input=document.getElementById('newPersonAlias'); const alias=input.value.trim();
  if(!alias) return;
  const u='/api/people/'+encodeURIComponent(id)+'/aliases?alias='+encodeURIComponent(alias)+'&token='+encodeURIComponent(token);
  const r=await fetch(u,{{method:'POST'}});
  if(!r.ok){{alert(await r.text());return;}}
  const p=people.find(x=>Number(x.id)===Number(id)); if(p&&!p.aliases.includes(alias)) p.aliases.push(alias);
  input.value=''; if(p) renderPersonAliases(p);
}}
async function removePersonAlias(alias){{
  if(!confirm('ลบชื่อเรียก “'+alias+'” ?')) return;
  const id=document.getElementById('personId').value;
  const u='/api/people/'+encodeURIComponent(id)+'/aliases?alias='+encodeURIComponent(alias)+'&token='+encodeURIComponent(token);
  const r=await fetch(u,{{method:'DELETE'}});
  if(!r.ok){{alert(await r.text());return;}}
  const p=people.find(x=>Number(x.id)===Number(id)); if(p){{p.aliases=p.aliases.filter(x=>x!==alias);renderPersonAliases(p);}}
}}
function closePerson(){{document.getElementById('personModal').style.display='none';}}
async function savePerson(ev){{
  ev.preventDefault();
  const id=document.getElementById('personId').value;
  const params=new URLSearchParams({{
    canonical_name:document.getElementById('personCanonical').value.trim(),
    call_name:document.getElementById('personCall').value.trim(),
    display_name:document.getElementById('personDisplay').value.trim(),
    role:document.getElementById('personRole').value,
    active:String(document.getElementById('personActive').checked), token:token
  }});
  const r=await fetch('/api/people/'+encodeURIComponent(id)+'/profile?'+params.toString(),{{method:'POST'}});
  if(r.ok){{const d=await r.json();alert('บันทึกแล้ว และปรับงานเดิม '+d.updated_tasks+' รายการ');location.reload();}} else alert(await r.text());
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
    """Quiet-by-default acknowledgement policy.

    Public group acknowledgement is reserved for state-changing moments that are useful
    to humans (completed / waiting). Routine in-progress updates and ambiguous/none
    updates stay silent to avoid bot fatigue and repetitive canned messages.
    """
    reply_pool = {
        "completed": [
            "รับทราบค่ะ งานนี้เรียบร้อยแล้วนะคะ",
            "ขอบคุณค่ะ เรื่องนี้เรียบร้อยแล้วนะคะ",
        ],
        "waiting": [
            "รับทราบค่ะ ตอนนี้ยังรออยู่ เดี๋ยวติดตามต่อจากจุดนี้นะคะ",
            "ขอบคุณที่อัปเดตค่ะ เดี๋ยวติดตามต่อจากข้อมูลนี้นะคะ",
        ],
    }
    if status_signal not in reply_pool:
        print("public acknowledgement suppressed:", status_signal)
        return
    text = random.choice(reply_pool[status_signal])
    try:
        if reply_token:
            await reply_text(reply_token, text)
        else:
            await push_text(group_id, text)
    except Exception as exc:
        # Acknowledgement failure must never create another public fallback message.
        print("task reply acknowledgement failed:", repr(exc))


async def safe_push_text(to: str | None, text: str, *, label: str = "notification") -> bool:
    """Best-effort LINE push that must never invalidate an already-committed task update."""
    if not to:
        return False
    try:
        await push_text(to, text)
        return True
    except Exception as exc:
        print(f"{label} push failed:", repr(exc))
        return False


async def safe_reply_or_push(reply_token: str | None, group_id: str, text: str, *, label: str = "group reply") -> bool:
    try:
        if reply_token:
            await reply_text(reply_token, text)
        else:
            await push_text(group_id, text)
        return True
    except Exception as exc:
        print(f"{label} failed:", repr(exc))
        return False


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



async def handle_query_or_followup_intent(
    *, group_id: str, user_id: str | None, sender_name: str | None,
    reply_token: str | None, quoted_message_id: str | None,
    message_id: str, text: str, intent_result,
    mentioned_name: str | None = None, mentioned_user_id: str | None = None,
) -> bool:
    """Handle STATUS_QUERY/FOLLOW_UP without creating or completing tasks."""
    with SessionLocal() as db:
        target = None
        ambiguous = []
        confidence = float(getattr(intent_result, "confidence", 0.0) or 0.0)

        # 1) Exact quoted/replied assistant message.
        if quoted_message_id:
            link = db.scalar(select(OutboundTaskMessage).where(
                OutboundTaskMessage.line_message_id == str(quoted_message_id),
                OutboundTaskMessage.group_id == group_id,
            ))
            if link:
                target = db.get(Task, link.task_id)

        # 2) Explicit FU id in text.
        if not target:
            m = re.search(r"FU-\d{6}-\d{4}", text or "", flags=re.I)
            if m:
                target = get_task_by_code(db, m.group(0))
                if target and target.group_id != group_id:
                    target = None

        # 3+) Safe content/history matching.
        if not target:
            target, match_confidence, ambiguous = find_task_for_explicit_query(
                db, group_id, text, sender_name=sender_name,
                assignee_name=mentioned_name, assignee_user_id=mentioned_user_id
            )
            confidence = min(confidence, match_confidence) if match_confidence else confidence

        event_type = "STATUS_QUERY" if intent_result.intent == "STATUS_QUERY" else "FOLLOW_UP"
        if target:
            record_task_event(
                db, target, event_type, actor_name=sender_name, actor_user_id=user_id,
                text=text, new_status=target.status, commit=True,
                message_id=message_id, confidence=confidence,
            )
            print("[INTENT]", {"message": text, "intent": intent_result.intent, "confidence": intent_result.confidence})
            print("[TASK_MATCH]", {"task_id": target.task_code, "confidence": confidence})
            print("[ACTION]", {"action": "ADD_TIMELINE", "status_change": "NONE"})

            if intent_result.intent == "STATUS_QUERY":
                if target.status == "COMPLETED":
                    response = f"เรื่อง {target.title} เรียบร้อยแล้วค่ะ"
                elif target.status == "WAITING":
                    response = f"เรื่อง {target.title} ยังอยู่ระหว่างรอข้อมูล/การตอบกลับค่ะ"
                elif target.status == "IN_PROGRESS":
                    response = f"เรื่อง {target.title} ยังอยู่ระหว่างดำเนินการค่ะ"
                else:
                    response = f"เรื่อง {target.title} ยังอยู่ระหว่างติดตามค่ะ"
                await safe_reply_or_push(reply_token, group_id, response, label="status query")
            else:
                # A follow-up request updates the existing timeline; it does not create a new FU.
                await safe_reply_or_push(
                    reply_token, group_id,
                    f"รับทราบค่ะ จะติดตามเรื่อง {target.title} ต่อจากงานเดิมให้นะคะ",
                    label="followup existing task",
                )
            return True

        if ambiguous:
            choices = "\n".join(f"{i}. {t.title}" for i, t in enumerate(ambiguous[:3], 1))
            await safe_reply_or_push(
                reply_token, group_id,
                f"หมายถึงเรื่องไหนคะ\n{choices}",
                label="ambiguous status query",
            )
            print("[ACTION]", {"action": "ASK_CLARIFICATION", "status_change": "NONE"})
            return True

        # Explicit query/follow-up with no confident match: do not create a duplicate.
        await safe_reply_or_push(
            reply_token, group_id,
            "ขอชื่อโครงการหรือเรื่องที่ต้องการติดตามเพิ่มอีกนิดได้ไหมคะ จะได้ตามต่อให้ตรงเรื่องค่ะ",
            label="unmatched followup clarification",
        )
        print("[ACTION]", {"action": "ASK_CLARIFICATION", "status_change": "NONE"})
        return True

async def process_message(event: dict):
    """Process one LINE message without allowing an obvious status update to fail silently.

    v0.6.14: group preprocessing (profile / People Registry / message logging) is
    useful context, but it must not be a single point of failure. If anything raises
    before the normal status handler can respond, an explicit Thai status update
    receives a last-resort acknowledgement in the group.
    """
    msg = event.get("message") or {}
    source = event.get("source") or {}
    source_type = source.get("type", "unknown")
    source_id = source.get("groupId") or source.get("userId") or source.get("roomId")
    reply_token = event.get("replyToken")
    text = (msg.get("text") or "").strip()
    local_status = infer_local_status_signal(text) if source_type == "group" else "none"
    try:
        await _process_message(event)
    except Exception as exc:
        print("process_message failed:", repr(exc), "source_type=", source_type, "text=", repr(text))
        if source_type == "group" and source_id and local_status != "none":
            # Never expose technical processing failure or a canned fallback in the work group.
            # Keep the group quiet; diagnostics go only to the owner.
            if settings.owner_status_updates and settings.owner_line_user_id:
                await safe_push_text(
                    settings.owner_line_user_id,
                    f"มีข้อความอัปเดตที่ประมวลผลไม่สำเร็จและยังไม่ได้ผูกกับงานใดค่ะ\n\n"
                    f"ผู้ส่ง: {source.get('userId') or '-'}\nข้อความ: {text}\n"
                    f"รายละเอียดสำหรับตรวจสอบ: {type(exc).__name__}: {exc}",
                    label="processing diagnostic owner",
                )


async def _process_message(event: dict):
    msg = event["message"]
    source = event.get("source", {})
    source_type = source.get("type", "unknown")
    source_id = source.get("groupId") or source.get("userId") or source.get("roomId")
    user_id = source.get("userId")
    reply_token = event.get("replyToken")
    if not source_id:
        return

    # Parse the text/status before non-essential network/registry work.
    # In v0.6.13 these calls happened first, so a profile/People Registry error could
    # stop an obvious message such as "จ่ายค่าประกันเรียบร้อย" before the fast
    # local-status path was ever reached.
    text = msg.get("text", "").strip()
    local_status = infer_local_status_signal(text) if source_type == "group" else "none"
    quoted_message_id = msg.get("quotedMessageId")

    display_name = None
    if source_type == "group" and user_id:
        try:
            display_name = await get_member_profile(source_id, user_id)
        except Exception as exc:
            print("get_member_profile failed:", repr(exc))

    mentions = []
    if source_type == "group":
        try:
            mentions = await resolve_message_mentions(msg, source_id)
        except Exception as exc:
            print("resolve_message_mentions failed:", repr(exc))

    # People Registry enrichment is best-effort. A registry/schema problem must not
    # prevent task status processing or user acknowledgement.
    if source_type == "group" and user_id and display_name:
        try:
            with SessionLocal() as db:
                bind_person_identity(db, display_name, user_id, display_name)
                db.commit()
        except Exception as exc:
            print("bind_person_identity failed:", repr(exc))

    # Message logging/dedup is also best-effort for availability. If the database is
    # temporarily unavailable, the later task update may still fail, but process_message
    # will now return an explicit acknowledgement rather than silence.
    try:
        with SessionLocal() as db:
            if db.scalar(select(Message).where(Message.line_message_id == msg["id"])):
                return
            db.add(Message(
                line_message_id=msg["id"], source_type=source_type, source_id=source_id,
                user_id=user_id, display_name=display_name, text=text
            ))
            db.commit()
    except Exception as exc:
        print("message logging failed:", repr(exc))

    if source_type == "user" and settings.owner_line_user_id.strip().upper() == "TEMP":
        await push_text(user_id, f"เชื่อมต่อสำเร็จค่ะ\nLINE User ID ของคุณคือ:\n{user_id}\n\nให้นำค่านี้ไปใส่ใน Render ที่ OWNER_LINE_USER_ID แล้ว Deploy ใหม่ค่ะ")
        return

    if source_type == "user" and user_id == settings.owner_line_user_id:
        await handle_owner_command(user_id, text)
        return

    if source_type != "group":
        return

    # v0.6.26 TASK CREATION GUARD
    # Ordinary leave/attendance notices are informational and must not become FU
    # tasks. Run this before status parsing and before the LLM so a sentence such
    # as "ขอลากิจ 2 วัน" cannot be promoted to a task by model uncertainty.
    precreation_guard = classify_precreation_guard(text)
    if precreation_guard.block_task_creation:
        print("non-task notice ignored:", precreation_guard.reason, repr(text))
        return

    # v0.6.27 INTENT SAFETY LAYER
    # Classify intent before any state transition or task creation. Question/follow-up
    # messages are handled against existing tasks and can never close or duplicate them.
    intent_result = classify_message_intent(text)
    primary_human_mention = select_primary_mention(mentions, None)
    mentioned_name = primary_human_mention.get("display_name") if primary_human_mention else None
    mentioned_user_id = primary_human_mention.get("user_id") if primary_human_mention else None
    direct_human_task_request = bool(primary_human_mention) and is_direct_task_request(text)

    # v0.6.28 HUMAN-DIRECTED REQUEST GUARD
    if direct_human_task_request and intent_result.intent == "STATUS_QUERY":
        print("[INTENT_OVERRIDE]", {"from": "STATUS_QUERY", "to": "NEW_TASK", "reason": "explicit_human_action_request"})
        intent_result = type(intent_result)("NEW_TASK", 0.98, "explicit_human_action_request")

    print("[INTENT]", {"message": text, "intent": intent_result.intent, "confidence": intent_result.confidence, "reason": intent_result.reason})
    if intent_result.intent in {"STATUS_QUERY", "FOLLOW_UP"}:
        await handle_query_or_followup_intent(
            group_id=source_id, user_id=user_id, sender_name=display_name,
            reply_token=reply_token, quoted_message_id=quoted_message_id,
            message_id=msg["id"], text=text, intent_result=intent_result,
            mentioned_name=mentioned_name, mentioned_user_id=mentioned_user_id,
        )
        return

    # v0.6.14 FAST LOCAL STATUS PATH
    # Detect obvious Thai status updates *before* calling the LLM. This is important
    # because a slow/failed OpenAI request previously caused messages such as
    # "จ่ายค่าประกันเรียบร้อย" to disappear silently before the local fallback ran.
    if local_status != "none":
        extraction = TaskExtraction(
            is_task=False,
            confidence=1.0,
            status_signal=local_status,
            is_task_reply=True,
            related_task_hint=text,
            reason="deterministic local status path",
        )
        print("fast local status:", local_status, repr(text))
    else:
        try:
            extraction = await asyncio.to_thread(extract_task, text, display_name)
        except Exception as exc:
            # Do not kill the webhook worker silently if the LLM is temporarily
            # unavailable. Non-obvious messages can wait for the next human message,
            # while obvious status messages never reach this branch.
            print("extract_task failed:", repr(exc), "text=", repr(text))
            return

    # v0.6.28: explicit actionable @mention remains a task request even if AI
    # focuses on the question clause and returns is_task=False.
    if direct_human_task_request and not extraction.is_task:
        extraction = TaskExtraction(
            is_task=True, confidence=0.98, title=text[:180],
            assignee_name=mentioned_name, status_signal="none", is_task_reply=False,
            related_task_hint=text, reason="explicit human action request fallback",
        )

    # v0.5.4: if the user used LINE's quote/reply feature on one of the
    # assistant's reminder messages, resolve the exact task by quotedMessageId.
    # This is much more reliable than guessing from display names such as
    # "พราว" vs "Proud🤍" or from short reply text.
    if quoted_message_id:
        changed = await handle_quoted_task_reply(
            source_id, user_id, display_name, quoted_message_id, extraction, text, msg["id"]
        )
        if changed is not None:
            await acknowledge_task_reply(reply_token, source_id, extraction.status_signal)
            if changed and settings.owner_status_updates and settings.owner_line_user_id:
                await safe_push_text(settings.owner_line_user_id, changed, label="quoted status owner")
            return

    # Long replies can contain updates for several projects. Never feed the full
    # mixed message into a single-task matcher; split and attach only strong segments.
    if not quoted_message_id and extraction.is_task_reply:
        if await handle_multi_topic_update(source_id, user_id, display_name, reply_token, text):
            return

    if extraction.status_signal != "none":
        changed = await try_update_task_from_status(source_id, user_id, display_name, extraction, text, msg["id"])
        if changed is not None:
            await acknowledge_task_reply(reply_token, source_id, extraction.status_signal)
            if changed and settings.owner_status_updates and settings.owner_line_user_id:
                await safe_push_text(settings.owner_line_user_id, changed, label="status owner")
            return
        # A status-like reply that cannot be matched confidently must never close a
        # random task. Surface it privately for manual review instead.
        # Never leave an explicit status update completely silent. Keep the group
        # response short; detailed candidate information stays private with the owner.
        # Never expose internal matching/Task terminology in the LINE group.
        # If confidence is insufficient, acknowledge the human update naturally and
        # send diagnostic/candidate detail only to the owner.
        # Quiet ambiguity: do not expose uncertainty or send a canned acknowledgement
        # into the work group. The original human message is already visible; silently
        # retain it in Message history and send diagnostics only to the owner.
        print("unmatched status kept silent in group:", repr(text))

        if settings.owner_status_updates and settings.owner_line_user_id:
            with SessionLocal() as db:
                candidates = rank_status_targets_with_history(
                    db, open_tasks(db, source_id),
                    resolve_canonical_name(db, display_name) or display_name,
                    resolve_canonical_name(db, extraction.assignee_name) or extraction.assignee_name,
                    extraction.related_task_hint or text,
                    user_id,
                )[:3]
            candidate_lines = []
            for row in candidates:
                # Only show plausible alternatives; never imply that one was updated.
                if row["content"] >= 0.20 or row["identity"] > 0:
                    t = row["task"]
                    candidate_lines.append(f"• {t.task_code} {t.title}")
            candidate_text = ""
            if candidate_lines:
                candidate_text = "\n\nงานที่อาจเกี่ยวข้อง:\n" + "\n".join(candidate_lines)
            await safe_push_text(
                settings.owner_line_user_id,
                f"มีข้อความอัปเดตที่ยังผูกกับงานเดิมได้ไม่มั่นใจค่ะ\n\n"
                f"ผู้ส่ง: {display_name or '-'}\n"
                f"ข้อความ: {text}"
                f"{candidate_text}\n\n"
                f"ยังไม่มีการเปลี่ยนสถานะงานอัตโนมัติค่ะ",
                label="unmatched status owner",
            )
        return

    if extraction.is_task_reply:
        with SessionLocal() as db:
            tasks = open_tasks(db, source_id)
            canonical_sender = resolve_canonical_name(db, display_name) or display_name
            match_hint = extraction.related_task_hint or text
            target = recent_reminder_context_target(db, source_id, user_id, canonical_sender, match_hint)
            if not target:
                target = choose_status_target_with_history(db, tasks, canonical_sender, extraction.assignee_name, match_hint, user_id)
            if target:
                record_task_event(
                    db, target, "COMMENT", actor_name=canonical_sender, actor_user_id=user_id,
                    text=text, new_status=target.status, commit=False,
                )
                target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender or '-'}: {text}").strip()
                db.commit()
                print("task comment recorded silently:", target.task_code)
                # Progress-only comments are learned silently. Acknowledgements are
                # reserved for explicit status changes or exact quoted replies.
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
            # Ambiguous operational chatter stays silent in the group. The message is
            # already persisted and can inform future context; diagnostics are private.
            print("unmatched comment kept silent in group:", repr(text))
            if settings.owner_status_updates and settings.owner_line_user_id:
                await safe_push_text(
                    settings.owner_line_user_id,
                    f"มีข้อมูลอัปเดตที่ยังผูกกับงานเดิมได้ไม่มั่นใจค่ะ\n\n"
                    f"ผู้ส่ง: {canonical_sender or '-'}\nข้อความ: {text}\n\n"
                    f"จึงยังไม่ได้นำข้อมูลนี้ไปต่อกับงานใดอัตโนมัติค่ะ",
                    label="unmatched comment owner",
                )
            return

    if extraction.is_task and extraction.confidence >= settings.auto_create_confidence:
        primary_mention = select_primary_mention(mentions, extraction.assignee_name)
        mention_name = primary_mention.get("display_name") if primary_mention else None
        mention_user_id = primary_mention.get("user_id") if primary_mention else None
        with SessionLocal() as db:
            # If there is no @mention, resolve explicit assignment wording against the
            # People Registry. This safely handles ambiguous names such as "ต้อง" in
            # "ให้ต้องเป็นผู้รับผิดชอบ" without treating "ต้องส่งวันนี้" as a person.
            contextual_person = None if primary_mention else resolve_assignee_from_text(db, text)
            assignee_override = mention_name
            assignee_uid = mention_user_id
            if contextual_person:
                assignee_override = contextual_person.canonical_name
                assignee_uid = contextual_person.line_user_id

            # v0.6.27 DUPLICATE FOLLOW-UP PREVENTION
            # Before creating any FU, search active tasks by content/history/project/assignee.
            # A high-confidence match becomes a timeline FOLLOW_UP on the existing task.
            existing, duplicate_confidence, ambiguous_dupes = find_existing_followup_task(
                db, source_id, text, sender_name=display_name,
                assignee_name=assignee_override or extraction.assignee_name,
                assignee_user_id=assignee_uid, min_confidence=0.80,
            )
            if existing:
                record_task_event(
                    db, existing, "FOLLOW_UP", actor_name=display_name, actor_user_id=user_id,
                    text=text, new_status=existing.status, commit=True,
                    message_id=msg["id"], confidence=duplicate_confidence,
                )
                print("[TASK_MATCH]", {"task_id": existing.task_code, "similarity": round(duplicate_confidence, 3)})
                print("[ACTION]", {"action": "ADD_TIMELINE", "status_change": "NONE", "duplicate_prevented": True})
                await safe_reply_or_push(
                    reply_token, source_id,
                    f"รับทราบค่ะ จะติดตามเรื่อง {existing.title} ต่อจากงานเดิมให้นะคะ",
                    label="duplicate followup",
                )
                return
            if ambiguous_dupes:
                choices = "\n".join(f"{i}. {t.title}" for i, t in enumerate(ambiguous_dupes[:3], 1))
                await safe_reply_or_push(
                    reply_token, source_id,
                    f"เรื่องนี้คล้ายกับงานที่กำลังติดตามอยู่ค่ะ หมายถึงเรื่องไหนคะ\n{choices}",
                    label="duplicate clarification",
                )
                print("[ACTION]", {"action": "ASK_CLARIFICATION", "status_change": "NONE", "duplicate_prevented": True})
                return

            task = create_task(
                db, source_id, msg["id"], extraction, source_text=text,
                actor_name=display_name, actor_user_id=user_id,
                assignee_name_override=assignee_override, assignee_user_id=assignee_uid,
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
    group_id: str, user_id: str | None, sender_name: str | None, quoted_message_id: str, extraction, text: str,
    message_id: str | None = None,
) -> str | None:
    """Apply a LINE quote-reply to the exact task message safely.

    v0.6.20: the quoted LINE message is the strongest task identifier. Status changes
    are committed in a small transaction *before* optional People Registry enrichment.
    Identity/alias problems must never prevent a correctly quoted task from closing.

    Returns:
      None  -> quote wasn't linked to a tracked task; continue normal matching.
      ""    -> handled but no owner message is needed.
      str   -> owner notification text.
    """
    mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
    signal = getattr(extraction, "status_signal", "none")
    safety_intent = classify_message_intent(text)
    extraction_confidence = float(getattr(extraction, "confidence", 0.0) or 0.0)
    if signal == "completed":
        unsafe_intents = {"STATUS_QUERY", "FOLLOW_UP", "NOT_COMPLETED", "PROGRESS_UPDATE"}
        if safety_intent.intent in unsafe_intents or extraction_confidence < 0.90:
            print("[COMPLETION_BLOCKED]", {"intent": safety_intent.intent, "intent_confidence": safety_intent.confidence, "extraction_confidence": extraction_confidence, "text": text})
            signal = "in_progress" if safety_intent.intent in {"PROGRESS_UPDATE", "NOT_COMPLETED"} else "none"
    new_status = mapping.get(signal)

    # Phase 1: resolve exact quoted task and commit the operational update only.
    task_id = None
    canonical_sender = sender_name or "-"
    with SessionLocal() as db:
        try:
            link = db.scalar(select(OutboundTaskMessage).where(
                OutboundTaskMessage.line_message_id == str(quoted_message_id),
                OutboundTaskMessage.group_id == group_id,
            ))
            target = db.get(Task, link.task_id) if link else None
            if not target:
                target = db.scalar(select(Task).where(
                    Task.source_message_id == str(quoted_message_id),
                    Task.group_id == group_id,
                ))
            if not target:
                return None
            if target.status not in {"OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"}:
                return ""

            task_id = target.id
            old_status = target.status
            canonical_sender = resolve_canonical_name(db, sender_name) or sender_name or "-"

            # Exact quote identifies the task. Do not make status correctness depend
            # on People Registry writes or alias uniqueness.
            if new_status:
                target.status = new_status
                commitment_at = extract_followup_commitment_at(text) if new_status != "COMPLETED" else None
                if new_status == "COMPLETED":
                    target.next_reminder_at = None
                elif commitment_at:
                    target.next_reminder_at = commitment_at
                elif new_status == "WAITING":
                    target.next_reminder_at = datetime.utcnow() + timedelta(hours=24)
                else:
                    target.next_reminder_at = datetime.utcnow() + timedelta(hours=6)
                event_type = "QUOTED_STATUS_REPLY"
            else:
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
                event_type = "QUOTED_COMMENT_REPLY"

            target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender}: {text}").strip()
            intent_event = "COMPLETION_CONFIRMATION" if new_status == "COMPLETED" else ("PROGRESS_UPDATE" if new_status else "COMMENT")
            record_task_event(
                db, target, intent_event, actor_name=canonical_sender, actor_user_id=user_id, text=text,
                old_status=old_status, new_status=target.status, commit=False,
                message_id=message_id, confidence=float(getattr(extraction, "confidence", 0.0) or 0.0),
            )
            if new_status and old_status != target.status:
                record_task_event(
                    db, target, "STATUS_CHANGE", actor_name=canonical_sender, actor_user_id=user_id, text=text,
                    old_status=old_status, new_status=target.status, commit=False,
                    message_id=message_id, confidence=float(getattr(extraction, "confidence", 0.0) or 0.0),
                )

            # Memory is useful but cannot be allowed to abort the status update.
            try:
                memory = summarize_progress_update(text)
                if memory:
                    record_task_event(
                        db, target, "TASK_MEMORY_UPDATED", actor_name=canonical_sender,
                        actor_user_id=user_id, text=memory, old_status=target.status,
                        new_status=target.status, commit=False,
                    )
                update_task_progress_snapshot(
                    db, target, text, actor_name=canonical_sender, actor_user_id=user_id,
                    message_id=message_id, confidence=float(getattr(extraction, "confidence", 0.0) or 0.0),
                    commit=False,
                )
            except Exception as memory_exc:
                print("quoted reply memory skipped:", type(memory_exc).__name__, repr(memory_exc))

            if new_status != "COMPLETED":
                commitment_at = extract_followup_commitment_at(text)
                if commitment_at:
                    local_commitment = commitment_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.timezone))
                    record_task_event(
                        db, target, "FOLLOW_UP_SCHEDULED", actor_name=canonical_sender, actor_user_id=user_id,
                        text=f"ติดตามอีกครั้ง {local_commitment.strftime('%d/%m/%Y %H:%M')}",
                        old_status=target.status, new_status=target.status, commit=False,
                        message_id=message_id, confidence=1.0,
                    )
                    print("[FOLLOW_UP_SCHEDULED]", target.task_code, local_commitment.isoformat(), "source=quoted_reply")

            db.commit()
            db.refresh(target)
            task_code, task_title, final_status = target.task_code, target.title, target.status
            print("quoted status update committed:", task_code, old_status, "->", final_status,
                  "sender=", canonical_sender, "text=", repr(text))
        except Exception as exc:
            db.rollback()
            print("quoted task CORE update failed:", type(exc).__name__, repr(exc),
                  "quoted_message_id=", quoted_message_id, "sender=", repr(sender_name), "text=", repr(text))
            raise

    # Phase 2: enrich People Registry / bind assignee only after the task update is safe.
    # Any failure here is logged and ignored; it must not turn a successful task update
    # into a user-visible processing error.
    if task_id and user_id:
        with SessionLocal() as db:
            try:
                target = db.get(Task, task_id)
                sender_person = db.scalar(select(Person).where(Person.line_user_id == user_id))
                if not sender_person and sender_name:
                    sender_person = bind_person_identity(db, sender_name, user_id, sender_name)
                elif sender_person and sender_name:
                    # Learn latest display name only on the existing LINE identity.
                    sender_person = bind_person_identity(db, sender_person.canonical_name, user_id, sender_name)

                if sender_person:
                    canonical_sender = sender_person.canonical_name

                # Only attach an unbound task. Never silently reassign a task already
                # bound to another LINE account.
                if target and not target.assignee_user_id:
                    task_canonical = resolve_canonical_name(db, target.assignee_name) or target.assignee_name
                    same_identity = (
                        not target.assignee_name
                        or normalize_name(task_canonical) == normalize_name(canonical_sender)
                        or normalize_name(target.assignee_name) == normalize_name(sender_name)
                    )
                    if same_identity:
                        target.assignee_user_id = user_id
                        if sender_person:
                            target.assignee_name = sender_person.canonical_name
                db.commit()
            except Exception as identity_exc:
                db.rollback()
                print("quoted reply identity enrichment skipped:", type(identity_exc).__name__, repr(identity_exc),
                      "task_id=", task_id, "user_id=", user_id)

    status_th = STATUS_THAI.get(final_status, final_status)
    return (
        f"มีการตอบกลับงานแล้วค่ะ\n\n"
        f"{task_code} {task_title}\n"
        f"ผู้ตอบ: {canonical_sender}\n"
        f"ข้อความ: {text}\n"
        f"สถานะ: {status_th}"
    )


async def try_update_task_from_status(group_id: str, user_id: str | None, sender_name: str | None, extraction, text: str, message_id: str | None = None) -> str | None:
    """Resolve and update one task atomically.

    Matching failure returns None. Database/update failure is logged with a precise stage
    and re-raised so the webhook can acknowledge a processing error without pretending
    that a different task was changed. Notification failures happen outside this function.
    """
    with SessionLocal() as db:
        try:
            tasks = open_tasks(db, group_id)
            canonical_sender = resolve_canonical_name(db, sender_name) or sender_name
            extracted_name = getattr(extraction, "assignee_name", None)
            canonical_extracted = resolve_canonical_name(db, extracted_name) or extracted_name
            match_hint = getattr(extraction, "related_task_hint", None) or text
            # Conversation continuity: people frequently answer the most recent reminder
            # without using LINE quote/reply. Prefer that recent context only when the
            # sender/topic evidence is safe; otherwise fall back to normal matching.
            target = recent_reminder_context_target(db, group_id, user_id, canonical_sender, match_hint)
            if target:
                print("recent reminder context target:", target.task_code, target.title, repr(text))
            else:
                target = choose_status_target_with_history(db, tasks, canonical_sender, canonical_extracted, match_hint, user_id)
            rows = rank_status_targets_with_history(db, tasks, canonical_sender, canonical_extracted, match_hint, user_id)
            print("status candidates same-group:", [
                {
                    "task": r["task"].task_code,
                    "title": r["task"].title,
                    "content": round(r["content"], 3),
                    "title_score": round(r.get("title_content", 0.0), 3),
                    "history_score": round(r.get("history_content", 0.0), 3),
                    "identity": round(r["identity"], 3),
                } for r in rows[:5]
            ])

            # Recovery path for a task that was created in another LINE group/room.
            # This is intentionally strict: only the owner may update any unique global
            # task; other users may recover only tasks already bound to their LINE userId.
            if not target:
                global_tasks = open_tasks(db, None)
                if user_id == settings.owner_line_user_id:
                    eligible_global = global_tasks
                elif user_id:
                    eligible_global = [t for t in global_tasks if t.assignee_user_id == user_id]
                else:
                    eligible_global = []

                # Remove current-group tasks because they were already evaluated above.
                eligible_global = [t for t in eligible_global if t.group_id != group_id]
                if eligible_global:
                    global_rows = rank_status_targets_with_history(
                        db, eligible_global, canonical_sender, canonical_extracted, match_hint, user_id
                    )
                    print("status candidates cross-group:", [
                        {
                            "task": r["task"].task_code,
                            "title": r["task"].title,
                            "content": round(r["content"], 3),
                            "history_score": round(r.get("history_content", 0.0), 3),
                            "identity": round(r["identity"], 3),
                        } for r in global_rows[:5]
                    ])
                    strong_global = [r for r in global_rows if r["content"] >= 0.75]
                    if len(strong_global) == 1:
                        target = strong_global[0]["task"]
                    elif len(strong_global) > 1:
                        best, second = strong_global[0], strong_global[1]
                        if best["content"] - second["content"] >= 0.15:
                            target = best["task"]

            if not target:
                print("status match: no confident target", repr(text), "same_group_open_tasks=", len(tasks))
                return None

            mapping = {"completed": "COMPLETED", "in_progress": "IN_PROGRESS", "waiting": "WAITING"}
            signal = getattr(extraction, "status_signal", "none")
            safety_intent = classify_message_intent(text)
            extraction_confidence = float(getattr(extraction, "confidence", 0.0) or 0.0)
            if signal == "completed":
                unsafe_intents = {"STATUS_QUERY", "FOLLOW_UP", "NOT_COMPLETED", "PROGRESS_UPDATE"}
                if safety_intent.intent in unsafe_intents or extraction_confidence < 0.90:
                    print("[COMPLETION_BLOCKED]", {"intent": safety_intent.intent, "intent_confidence": safety_intent.confidence, "extraction_confidence": extraction_confidence, "text": text})
                    signal = "in_progress" if safety_intent.intent in {"PROGRESS_UPDATE", "NOT_COMPLETED"} else "none"
            new_status = mapping.get(signal)
            if not new_status:
                return None

            old_status = target.status
            target.status = new_status
            target.notes = ((target.notes or "") + f"\n{datetime.now()}: {canonical_sender or '-'}: {text}").strip()
            commitment_at = extract_followup_commitment_at(text) if new_status != "COMPLETED" else None
            if new_status == "COMPLETED":
                target.next_reminder_at = None
            elif commitment_at:
                # A human supplied a concrete future checkpoint (e.g. "นัดเซ็นวันศุกร์").
                # Respect that checkpoint instead of asking again tomorrow.
                target.next_reminder_at = commitment_at
            elif new_status == "WAITING":
                # External dependencies need breathing room; do not chase the assignee every few hours.
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=24)
            elif target.due_at and datetime.utcnow() > target.due_at:
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=settings.reminder_repeat_hours)
            else:
                target.next_reminder_at = datetime.utcnow() + timedelta(hours=6)

            intent_event = "COMPLETION_CONFIRMATION" if new_status == "COMPLETED" else "PROGRESS_UPDATE"
            record_task_event(
                db, target, intent_event, actor_name=canonical_sender, actor_user_id=user_id, text=text,
                old_status=old_status, new_status=new_status, commit=False,
                message_id=message_id, confidence=extraction_confidence,
            )
            if old_status != new_status:
                record_task_event(
                    db, target, "STATUS_CHANGE", actor_name=canonical_sender, actor_user_id=user_id, text=text,
                    old_status=old_status, new_status=new_status, commit=False,
                    message_id=message_id, confidence=extraction_confidence,
                )
            # Store a compact task-memory snapshot so future reminders continue from this update.
            memory = summarize_progress_update(text)
            if memory:
                record_task_event(
                    db, target, "TASK_MEMORY_UPDATED", actor_name=canonical_sender, actor_user_id=user_id,
                    text=memory, old_status=new_status, new_status=new_status, commit=False,
                )
            update_task_progress_snapshot(
                db, target, text, actor_name=canonical_sender, actor_user_id=user_id,
                message_id=message_id, confidence=extraction_confidence, commit=False,
            )
            if commitment_at:
                local_commitment = commitment_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.timezone))
                record_task_event(
                    db, target, "FOLLOW_UP_SCHEDULED", actor_name=canonical_sender, actor_user_id=user_id,
                    text=f"ติดตามอีกครั้ง {local_commitment.strftime('%d/%m/%Y %H:%M')}",
                    old_status=new_status, new_status=new_status, commit=False,
                    message_id=message_id, confidence=1.0,
                )
                print("[FOLLOW_UP_SCHEDULED]", target.task_code, local_commitment.isoformat(), "source=status_update")
            db.commit()
            db.refresh(target)
            print("status update committed:", target.task_code, old_status, "->", new_status, repr(text))

            if old_status == new_status and new_status != "COMPLETED":
                return ""
            status_th = STATUS_THAI.get(new_status, new_status)
            return (
                f"อัปเดตงานจากบทสนทนาในกลุ่มแล้วค่ะ\n\n"
                f"{target.task_code} {target.title}\n"
                f"ผู้ตอบ: {canonical_sender or '-'}\nสถานะใหม่: {status_th}"
            )
        except Exception as exc:
            db.rollback()
            print("status update transaction failed:", type(exc).__name__, repr(exc), "text=", repr(text))
            raise


async def handle_owner_command(user_id: str, text: str):
    raw = text.strip()
    low = raw.lower()

    if low.startswith("ลบ "):
        parts = raw.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if not re.fullmatch(r"FU-\d{6}-\d{4,}", code, flags=re.IGNORECASE):
            await push_text(user_id, "รูปแบบ: ลบ FU-xxxxxx-xxxx ค่ะ")
            return
        with SessionLocal() as db:
            task = get_task_by_code(db, code)
            if not task:
                await push_text(user_id, f"ไม่พบงาน {code} ค่ะ")
                return
            title = task.title
            deleted = delete_task_by_code(db, code)
        if deleted:
            await push_text(user_id, f"ลบงาน {code} ออกจากระบบเรียบร้อยค่ะ\n{title}")
        return

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
        "• ปิด FU-xxxxxx-xxxx\n"
        "• ลบ FU-xxxxxx-xxxx"
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


def _local_minutes(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def in_followup_window(local_dt: datetime | None = None) -> bool:
    """True only during the configured daily working window.

    The end time is exclusive: at 17:30 group follow-up stops. Owner daily brief
    may still be sent by its dedicated 17:30 job.
    """
    local_dt = local_dt or datetime.now(ZoneInfo(settings.timezone))
    start = settings.followup_start_hour * 60 + settings.followup_start_minute
    end = settings.followup_end_hour * 60 + settings.followup_end_minute
    current = _local_minutes(local_dt)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def in_quiet_hours() -> bool:
    # Backward-compatible name used by /health and dashboard. For reminder
    # purposes, anything outside working hours is quiet.
    return not in_followup_window()


def _local_day_utc_bounds(now_utc: datetime) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.timezone)
    aware_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))
    local = aware_utc.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, end_utc


def followups_sent_today(db, task: Task, now_utc: datetime) -> int:
    start_utc, end_utc = _local_day_utc_bounds(now_utc)
    return int(db.scalar(select(func.count(TaskEvent.id)).where(
        TaskEvent.task_id == task.id,
        TaskEvent.event_type == "REMINDER_SENT",
        TaskEvent.created_at >= start_utc,
        TaskEvent.created_at < end_utc,
    )) or 0)


def _task_stagger_minutes(task: Task, max_minutes: int = 45) -> int:
    token = task.task_code or str(task.id or 0)
    return sum((i + 1) * ord(ch) for i, ch in enumerate(token)) % (max_minutes + 1)


def next_work_window_utc(base_utc: datetime, task: Task | None = None, *, next_day: bool = False) -> datetime:
    """Clamp a UTC-naive reminder time into 08:30-17:30 local working hours."""
    tz = ZoneInfo(settings.timezone)
    local = base_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    start_min = settings.followup_start_hour * 60 + settings.followup_start_minute
    end_min = settings.followup_end_hour * 60 + settings.followup_end_minute
    current = _local_minutes(local)
    stagger = _task_stagger_minutes(task) if task else 0

    if next_day:
        local = (local + timedelta(days=1)).replace(
            hour=settings.followup_start_hour, minute=settings.followup_start_minute, second=0, microsecond=0
        ) + timedelta(minutes=stagger)
    elif current < start_min:
        local = local.replace(
            hour=settings.followup_start_hour, minute=settings.followup_start_minute, second=0, microsecond=0
        ) + timedelta(minutes=stagger)
    elif current >= end_min:
        local = (local + timedelta(days=1)).replace(
            hour=settings.followup_start_hour, minute=settings.followup_start_minute, second=0, microsecond=0
        ) + timedelta(minutes=stagger)

    # A large stagger must never push a message past the work window.
    if _local_minutes(local) >= end_min:
        local = (local + timedelta(days=1)).replace(
            hour=settings.followup_start_hour, minute=settings.followup_start_minute, second=0, microsecond=0
        )
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def schedule_next_followup(task: Task, candidate_utc: datetime, *, force_next_day: bool = False) -> datetime:
    return next_work_window_utc(candidate_utc, task, next_day=force_next_day)


def repair_missing_reminder_schedule(db, now_utc: datetime) -> int:
    """Repair open tasks that lost their reminder schedule.

    Older tasks or interrupted status updates can leave ``next_reminder_at`` NULL.
    The reminder scanner historically ignored those rows forever.  This repair is
    intentionally conservative: it only touches active tasks with no schedule at
    all.  Existing future commitments/schedules are never moved.
    """
    tasks = list(db.scalars(select(Task).where(
        Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
        Task.next_reminder_at == None,
    )).all())
    repaired = 0
    for task in tasks:
        # If an unfinished task is already past its due date, make it eligible
        # in the current working window.  Otherwise use the due date, or the
        # next working window for undated tasks.
        if task.due_at and task.due_at > now_utc:
            candidate = task.due_at
        else:
            candidate = now_utc
        task.next_reminder_at = schedule_next_followup(task, candidate)
        record_task_event(
            db, task, "REMINDER_SCHEDULE_REPAIRED",
            actor_name="LINE Follow-up Assistant",
            text="ซ่อมคิวติดตามอัตโนมัติสำหรับงานที่ยังเปิดอยู่",
            old_status=task.status, new_status=task.status, commit=False,
        )
        repaired += 1
    if repaired:
        db.commit()
        print("reminder schedule repaired:", repaired)
    return repaired


async def reminder_scan(force: bool = False):
    stats = {"due": 0, "sent": 0, "failed": 0, "skipped_quiet": 0, "skipped_daily_cap": 0, "deferred_spread": 0, "repaired_schedule": 0}
    now = datetime.utcnow()

    # Self-heal legacy/open tasks that have no next reminder at all. Without
    # this, they are invisible to the due query and can remain unfollowed forever.
    with SessionLocal() as repair_db:
        stats["repaired_schedule"] = repair_missing_reminder_schedule(repair_db, now)

    # Group follow-up is strictly limited to the working window 08:30-17:30.
    # Due reminders are preserved and delivered after the next window opens.
    if not force and not in_followup_window():
        with SessionLocal() as db:
            stats["due"] = db.query(Task).filter(
                Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
                Task.next_reminder_at != None,
                Task.next_reminder_at <= now,
            ).count()
        stats["skipped_quiet"] = stats["due"]
        print("reminder scan skipped: outside follow-up window, due=", stats["due"] )
        return stats

    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(
            Task.status.in_(["OPEN", "IN_PROGRESS", "WAITING", "OVERDUE"]),
            Task.next_reminder_at != None,
            Task.next_reminder_at <= now,
        ).order_by(Task.next_reminder_at.asc())).all())
        stats["due"] = len(tasks)
        group_sent_counts: dict[str, int] = {}

        for t in tasks:
            # Avoid a robotic burst of many reminders in the same cron tick.
            if not force and stats["sent"] >= settings.max_followups_per_scan:
                stats["deferred_spread"] += 1
                continue
            if not force and group_sent_counts.get(t.group_id, 0) >= settings.max_group_followups_per_scan:
                t.next_reminder_at = max(t.next_reminder_at or now, now + timedelta(minutes=5))
                db.commit()
                stats["deferred_spread"] += 1
                continue

            # Daily cap: normal tasks max 2 follow-ups/day; WAITING max 1/day.
            # This preserves daily follow-up while avoiding repetitive pressure.
            sent_today = followups_sent_today(db, t, now)
            daily_cap = settings.waiting_max_followups_per_day if t.status == "WAITING" else settings.max_followups_per_task_per_day
            if not force and sent_today >= daily_cap:
                t.next_reminder_at = schedule_next_followup(t, now, force_next_day=True)
                db.commit()
                stats["skipped_daily_cap"] += 1
                continue

            try:
                became_overdue = bool(t.due_at and now > t.due_at and t.status != "OVERDUE")
                if t.due_at and now > t.due_at:
                    t.status = "OVERDUE"

                assignee = t.assignee_name.strip() if t.assignee_name else "ทีม"
                person = None
                if t.assignee_user_id:
                    person = db.scalar(select(Person).where(Person.line_user_id == t.assignee_user_id))
                if not person and t.assignee_name:
                    person = db.scalar(select(Person).where(Person.canonical_name == t.assignee_name))
                if person and person.call_name:
                    assignee = person.call_name.strip()
                use_mention = bool(t.assignee_user_id)
                greeting = "{assignee}คะ" if use_mention else (f"{assignee}คะ" if assignee != "ทีม" else "ทีมคะ")
                topic = task_reference_label(db, t)
                is_pre_due = bool(t.due_at and now < t.due_at)

                if is_pre_due:
                    due_text = format_due_local(t)
                    body = (
                        f"{greeting} เรื่อง{topic}กำหนด {due_text} นะคะ\n"
                        f"ตอนนี้ยังเป็นไปตามแผนอยู่ไหมคะ"
                    )
                    # After the advance reminder, the next check is the due time itself.
                    t.next_reminder_at = schedule_next_followup(t, t.due_at)
                elif t.status in ("OVERDUE", "WAITING", "IN_PROGRESS"):
                    body, _ = contextual_followup_text(
                        db, t, greeting[:-2] if greeting.endswith("คะ") else greeting, settings.owner_display_name
                    )

                    if t.status == "WAITING":
                        t.next_reminder_at = schedule_next_followup(t, now + timedelta(hours=24))
                    elif t.status == "IN_PROGRESS":
                        t.next_reminder_at = schedule_next_followup(t, now + timedelta(hours=6))
                    else:
                        t.next_reminder_at = schedule_next_followup(t, now + timedelta(hours=settings.reminder_repeat_hours))
                else:
                    body, _ = contextual_followup_text(
                        db, t, greeting[:-2] if greeting.endswith("คะ") else greeting, settings.owner_display_name
                    )
                    t.next_reminder_at = schedule_next_followup(t, now + timedelta(hours=settings.reminder_repeat_hours))

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
                group_sent_counts[t.group_id] = group_sent_counts.get(t.group_id, 0) + 1
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
