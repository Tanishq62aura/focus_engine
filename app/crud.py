"""
app/crud.py
Database read/write operations with business-logic enforcement and user scoping.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models, schemas

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VALID_BLOCKER_KEYWORDS = {
    "emergency", "accident", "hospital", "sick", "fever", "death",
    "family emergency", "ambulance", "injury", "medical",
}
_RATIONALIZATION_KEYWORDS = {
    "tired", "sleepy", "bored", "not feeling", "lazy", "not in mood",
    "don't want", "distracted", "procrastin",
}


def _utcnow() -> datetime:
    """Returns timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _heuristic_excuse_audit(reason: str) -> tuple[bool, str]:
    """
    Returns (is_valid_blocker, audit_message).
    Pure heuristic fallback used when AI is unavailable.
    """
    lower = reason.lower()
    if any(kw in lower for kw in _VALID_BLOCKER_KEYWORDS):
        return True, (
            "✅ Valid blocker detected (medical/emergency). "
            "Session rescheduled cleanly. Take care of yourself first."
        )
    if any(kw in lower for kw in _RATIONALIZATION_KEYWORDS):
        return False, (
            "🚨 RATIONALIZATION DETECTED. "
            "Your reason does not constitute a valid blocker. "
            "The 5-minute micro-session floor is now enforced. "
            "Start for just 5 minutes — momentum will follow."
        )
    return False, (
        "⚠️ Reason unclear. Defaulting to 5-minute micro-start floor. "
        "Commit to 5 minutes now and reassess afterward."
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def create_task(db: Session, data: schemas.TaskCreate, user_id: str = "dev-user") -> models.Task:
    task = models.Task(
        id=_new_id(),
        user_id=user_id,
        title=data.title,
        category=data.category,
        priority=data.priority,
        estimated_duration_min=data.estimated_duration_min,
        min_goal=data.min_goal,
        target_goal=data.target_goal,
        stretch_goal=data.stretch_goal,
        created_at=_utcnow(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_tasks(db: Session, user_id: str = "dev-user") -> list[models.Task]:
    return (
        db.query(models.Task)
        .filter(models.Task.user_id == user_id)
        .order_by(models.Task.priority, models.Task.created_at)
        .all()
    )


def get_task(db: Session, task_id: str, user_id: Optional[str] = None) -> Optional[models.Task]:
    query = db.query(models.Task).filter(models.Task.id == task_id)
    if user_id:
        query = query.filter(models.Task.user_id == user_id)
    return query.first()


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
def create_schedule(
    db: Session, data: schemas.ScheduleCreate, user_id: str = "dev-user"
) -> models.Schedule:
    sched = models.Schedule(
        id=_new_id(),
        user_id=user_id,
        task_id=data.task_id,
        scheduled_start=data.scheduled_start,
        scheduled_end=data.scheduled_end,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


def get_schedule(
    db: Session, schedule_id: str, user_id: Optional[str] = None
) -> Optional[models.Schedule]:
    query = db.query(models.Schedule).filter(models.Schedule.id == schedule_id)
    if user_id:
        query = query.filter(models.Schedule.user_id == user_id)
    return query.first()


def snooze_schedule(
    db: Session, schedule_id: str, user_id: Optional[str] = None
) -> Optional[models.Schedule]:
    sched = get_schedule(db, schedule_id, user_id=user_id)
    if not sched:
        return None
    sched.snooze_count += 1
    sched.scheduled_start = sched.scheduled_start + timedelta(minutes=10)
    sched.scheduled_end = sched.scheduled_end + timedelta(minutes=10)
    sched.status = "snoozed"
    db.commit()
    db.refresh(sched)
    return sched


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def start_session(
    db: Session, data: schemas.SessionStart, user_id: str = "dev-user"
) -> models.FocusSession:
    now = _utcnow()
    latency = 0
    if data.schedule_id:
        sched = get_schedule(db, data.schedule_id, user_id=user_id)
        if sched and sched.scheduled_start:
            # Normalize naive / aware comparison if needed
            sched_start = sched.scheduled_start
            if sched_start.tzinfo is None:
                sched_start = sched_start.replace(tzinfo=timezone.utc)
            diff = (now - sched_start).total_seconds() / 60
            latency = max(0, int(diff))
            sched.status = "started"
            db.add(sched)

    session = models.FocusSession(
        id=_new_id(),
        user_id=user_id,
        task_id=data.task_id,
        schedule_id=data.schedule_id,
        is_five_min_intervention=data.is_five_min_intervention,
        started_at=now,
        start_latency_min=latency,
        verification_status="unverified",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def end_session(
    db: Session, session_id: str, data: schemas.SessionEnd, user_id: Optional[str] = None
) -> Optional[models.FocusSession]:
    query = db.query(models.FocusSession).filter(models.FocusSession.id == session_id)
    if user_id:
        query = query.filter(models.FocusSession.user_id == user_id)
    session = query.first()
    if not session:
        return None
    now = _utcnow()
    session.ended_at = now
    if session.started_at:
        started = session.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        session.actual_focused_seconds = int((now - started).total_seconds())
    session.verification_status = "pending_viva"
    if data.notes:
        session.notes = data.notes
    db.commit()
    db.refresh(session)
    return session


def get_active_session(db: Session, user_id: Optional[str] = None) -> Optional[models.FocusSession]:
    """Returns the most recently started session that has not ended yet for this user."""
    query = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.ended_at.is_(None))
    )
    if user_id:
        query = query.filter(models.FocusSession.user_id == user_id)
    return query.order_by(models.FocusSession.started_at.desc()).first()


def flag_paste_on_session(
    db: Session, session_id: str, user_id: Optional[str] = None
) -> None:
    query = db.query(models.FocusSession).filter(models.FocusSession.id == session_id)
    if user_id:
        query = query.filter(models.FocusSession.user_id == user_id)
    session = query.first()
    if session:
        session.pasted_burst_detected = True
        db.commit()


def set_viva_question(
    db: Session, session_id: str, question: str, proof: str, user_id: Optional[str] = None
) -> Optional[models.FocusSession]:
    query = db.query(models.FocusSession).filter(models.FocusSession.id == session_id)
    if user_id:
        query = query.filter(models.FocusSession.user_id == user_id)
    session = query.first()
    if not session:
        return None
    session.proof_text = proof
    session.viva_question = question
    session.verification_status = "pending_viva"
    db.commit()
    db.refresh(session)
    return session


def resolve_viva(
    db: Session,
    session_id: str,
    answer: str,
    feedback: str,
    passed: bool,
    user_id: Optional[str] = None,
) -> Optional[models.FocusSession]:
    query = db.query(models.FocusSession).filter(models.FocusSession.id == session_id)
    if user_id:
        query = query.filter(models.FocusSession.user_id == user_id)
    session = query.first()
    if not session:
        return None
    session.viva_answer = answer
    session.viva_feedback = feedback
    session.verification_status = "verified" if passed else "rejected"
    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Distractions
# ---------------------------------------------------------------------------
def log_distraction(
    db: Session, data: schemas.DistractionCreate, user_id: str = "dev-user"
) -> models.DistractionLog:
    log = models.DistractionLog(
        id=_new_id(),
        user_id=user_id,
        session_id=data.session_id,
        timestamp=_utcnow(),
        interruption_type=data.interruption_type,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Excuse Audit
# ---------------------------------------------------------------------------
def audit_excuse(
    db: Session,
    data: schemas.ExcuseAuditRequest,
    user_id: str = "dev-user",
    ai_response: Optional[str] = None,
    is_valid: Optional[bool] = None,
) -> models.ExcuseLog:
    if ai_response is None or is_valid is None:
        is_valid, ai_response = _heuristic_excuse_audit(data.stated_reason)

    log = models.ExcuseLog(
        id=_new_id(),
        user_id=user_id,
        schedule_id=data.schedule_id,
        timestamp=_utcnow(),
        stated_reason=data.stated_reason,
        is_valid_blocker=is_valid,
        ai_audit_response=ai_response,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def get_today_report(db: Session, user_id: str = "dev-user") -> dict:
    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sessions = (
        db.query(models.FocusSession)
        .filter(
            models.FocusSession.user_id == user_id,
            models.FocusSession.started_at >= today_start,
        )
        .all()
    )

    total_focused_sec = sum(s.actual_focused_seconds or 0 for s in sessions)
    verified = sum(1 for s in sessions if s.verification_status == "verified")
    rejected = sum(1 for s in sessions if s.verification_status == "rejected")
    pending = sum(1 for s in sessions if s.verification_status == "pending_viva")
    latencies = [s.start_latency_min for s in sessions if s.start_latency_min is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    # Total planned today
    schedules = (
        db.query(models.Schedule)
        .filter(
            models.Schedule.user_id == user_id,
            models.Schedule.scheduled_start >= today_start,
        )
        .all()
    )
    total_planned_min = sum(
        max(0, (s.scheduled_end - s.scheduled_start).total_seconds() / 60)
        for s in schedules
        if s.scheduled_end and s.scheduled_start
    )
    total_focused_min = total_focused_sec / 60
    execution_ratio = (total_focused_min / total_planned_min * 100) if total_planned_min > 0 else 0.0

    # Distractions for user's sessions today
    session_ids = [s.id for s in sessions]
    distraction_rows = (
        db.query(models.DistractionLog)
        .filter(
            models.DistractionLog.user_id == user_id,
            models.DistractionLog.session_id.in_(session_ids),
        )
        .all()
        if session_ids
        else []
    )
    breakdown: dict[str, int] = {}
    for d in distraction_rows:
        breakdown[d.interruption_type] = breakdown.get(d.interruption_type, 0) + 1

    return {
        "date": today_start.strftime("%Y-%m-%d"),
        "total_focused_minutes": round(total_focused_min, 2),
        "verified_sessions": verified,
        "rejected_sessions": rejected,
        "pending_sessions": pending,
        "total_sessions": len(sessions),
        "average_start_latency_min": round(avg_latency, 2),
        "total_distractions": len(distraction_rows),
        "distraction_breakdown": breakdown,
        "execution_ratio_pct": round(execution_ratio, 2),
    }


# ---------------------------------------------------------------------------
# Friction & Habit Analytics Engine (F in [0, 100])
# ---------------------------------------------------------------------------
def calculate_session_friction(session: models.FocusSession) -> float:
    """
    Computes the bounded behavioral friction score F in [0, 100] for a session:
    F = min(100, w1*(delta_t/15) + w2*snooze + w3*interrupt + w4*(1 - c_ratio)*10 + w5*intervention)

    Default weights:
      w1 = 20  (Start Latency factor; baseline 15 min normalization)
      w2 = 15  (Snooze count factor; penalties per delay)
      w3 = 10  (Interruption/distraction count factor)
      w4 = 25  (Completion factor: C_ratio = 1.0 verified, 0.5 unverified, 0.0 rejected/abandoned)
      w5 = 30  (Intervention penalty: applied if is_five_min_intervention is True)
    """
    w1, w2, w3, w4, w5 = 20.0, 15.0, 10.0, 25.0, 30.0

    delta_t_start = max(0, session.start_latency_min or 0)
    n_snooze = session.schedule.snooze_count if session.schedule and session.schedule.snooze_count else 0
    d_list = getattr(session, "distractions", None) or getattr(session, "distraction_logs", None)
    n_interrupt = len(d_list) if d_list else 0

    status = (session.verification_status or "").lower()
    if status == "verified":
        c_ratio = 1.0
    elif status in ("unverified", "pending_viva"):
        c_ratio = 0.5
    else:  # rejected, abandoned
        c_ratio = 0.0

    intervention = 1.0 if session.is_five_min_intervention else 0.0

    raw_f = (
        w1 * (delta_t_start / 15.0)
        + w2 * n_snooze
        + w3 * n_interrupt
        + w4 * (1.0 - c_ratio) * 10.0
        + w5 * intervention
    )
    return round(min(100.0, max(0.0, float(raw_f))), 1)


def get_weekly_friction_report(
    db: Session, user_id: str = "dev-user", days: int = 7
) -> dict:
    """
    Aggregates behavioral friction telemetry across the past N days.
    Groups metrics by category (coding, tnp, theory) and identifies the primary culprit.
    """
    from sqlalchemy.orm import joinedload

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = (
        db.query(models.FocusSession)
        .options(
            joinedload(models.FocusSession.task),
            joinedload(models.FocusSession.schedule),
            joinedload(models.FocusSession.distraction_logs),
        )
        .filter(
            models.FocusSession.user_id == user_id,
            models.FocusSession.started_at >= cutoff,
        )
        .all()
    )

    categories_config = {
        "coding": {"label": "Coding", "icon": "coding"},
        "tnp": {"label": "T&P Aptitude", "icon": "tnp"},
        "theory": {"label": "Theory", "icon": "theory"},
    }

    cat_data = {
        k: {
            "frictions": [],
            "latencies": [],
            "distractions": 0,
            "verified_count": 0,
            "total_focused_sec": 0,
            "total_sessions": 0,
        }
        for k in categories_config
    }

    all_frictions = []
    total_distractions = 0
    total_verified = 0

    for s in sessions:
        cat = (s.task.category if s.task and s.task.category in cat_data else "coding")
        f_score = calculate_session_friction(s)
        all_frictions.append(f_score)

        lat = max(0, s.start_latency_min or 0)
        dist_count = len(s.distraction_logs) if s.distraction_logs else 0
        total_distractions += dist_count

        is_verified = s.verification_status == "verified"
        if is_verified:
            total_verified += 1

        cat_data[cat]["frictions"].append(f_score)
        cat_data[cat]["latencies"].append(lat)
        cat_data[cat]["distractions"] += dist_count
        if is_verified:
            cat_data[cat]["verified_count"] += 1
        cat_data[cat]["total_focused_sec"] += s.actual_focused_seconds or 0
        cat_data[cat]["total_sessions"] += 1

    category_results = []
    for cat_key, conf in categories_config.items():
        c_stats = cat_data[cat_key]
        n = c_stats["total_sessions"]
        avg_f = round(sum(c_stats["frictions"]) / n, 1) if n > 0 else 0.0
        avg_lat = round(sum(c_stats["latencies"]) / n, 1) if n > 0 else 0.0
        pass_rate = round((c_stats["verified_count"] / n) * 100.0, 1) if n > 0 else 0.0
        focused_min = round(c_stats["total_focused_sec"] / 60.0, 1)

        category_results.append({
            "category": cat_key,
            "label": conf["label"],
            "icon": conf["icon"],
            "total_sessions": n,
            "avg_friction_score": avg_f,
            "avg_start_latency_min": avg_lat,
            "total_distractions": c_stats["distractions"],
            "verification_pass_rate_pct": pass_rate,
            "total_focused_minutes": focused_min,
        })

    total_sessions_count = len(sessions)
    system_friction_index = (
        round(sum(all_frictions) / total_sessions_count, 1)
        if total_sessions_count > 0
        else 0.0
    )

    if system_friction_index < 30.0:
        friction_status = "optimal"
    elif system_friction_index <= 65.0:
        friction_status = "moderate"
    else:
        friction_status = "severe"

    overall_pass_rate = (
        round((total_verified / total_sessions_count) * 100.0, 1)
        if total_sessions_count > 0
        else 0.0
    )

    active_categories = [c for c in category_results if c["total_sessions"] > 0]
    culprit = None
    if active_categories:
        culprit_cat = max(
            active_categories,
            key=lambda x: (x["avg_friction_score"], x["avg_start_latency_min"]),
        )
        insight = (
            f"Highest Initiation Resistance: {culprit_cat['label']} "
            f"(Friction Score: {culprit_cat['avg_friction_score']}/100, Avg Latency: {culprit_cat['avg_start_latency_min']}m)"
        )
        culprit = {
            "category": culprit_cat["category"],
            "label": culprit_cat["label"],
            "icon": culprit_cat["icon"],
            "avg_friction_score": culprit_cat["avg_friction_score"],
            "avg_start_latency_min": culprit_cat["avg_start_latency_min"],
            "insight": insight,
        }
    else:
        culprit = {
            "category": "none",
            "label": "None",
            "icon": "⚡",
            "avg_friction_score": 0.0,
            "avg_start_latency_min": 0.0,
            "insight": f"No active focus sessions recorded in the past {days} days. Start a task to generate behavioral friction telemetry.",
        }

    return {
        "period_days": days,
        "system_friction_index": system_friction_index,
        "friction_status": friction_status,
        "total_sessions": total_sessions_count,
        "total_distractions": total_distractions,
        "overall_pass_rate_pct": overall_pass_rate,
        "categories": category_results,
        "culprit": culprit,
        "high_friction_culprit": culprit,
    }
