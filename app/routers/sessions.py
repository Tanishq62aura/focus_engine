"""
app/routers/sessions.py
Focus Sessions router with behavioral countermeasures & gating:
- Intercepts high-friction session starts for critical tracks (tp_aptitude, theory)
- Injects 5-minute intervention flag & 25-minute Pomodoro duration cap
- Triggers immediate peripheral lockdown via ntfy.sh
- Detects meta-work / config procrastination (neovim, plugins, theme) as Digital Drift
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud, mobile_bridge, models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.verification import evaluate_viva_answer, generate_viva_question

logger = logging.getLogger("focus_engine.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])

CRITICAL_TRACKS = {"tp_aptitude", "tnp", "theory"}
CONFIG_METAWORK_REGEX = re.compile(r"\b(neovim|nvim|vimrc|plugins?|themes?|dotfiles|keymaps?|tmux)\b", re.IGNORECASE)


@router.post("/start", response_model=schemas.SessionRead, status_code=status.HTTP_201_CREATED)
def start_session(
    data: schemas.SessionStart,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Starts a focus session with dynamic behavioral gating:
    - Critical tracks (tp_aptitude, theory) automatically enforce 5-minute micro-start interventions,
      cap session target duration to 25m Pomodoro, and trigger physical phone lockdown.
    """
    task = crud.get_task(db, data.task_id, user_id=current_user)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or unauthorized")

    cat = (task.category or "").lower().strip()
    is_critical = cat in CRITICAL_TRACKS

    # 1. Behavioral Countermeasures: Dynamic Track Penalty Injection
    if is_critical:
        # Mandatory 5-minute intervention floor
        data.is_five_min_intervention = True
        # Cap target duration to 25m Pomodoro
        target_duration = 25
        # Dispatch peripheral lockdown hook via ntfy.sh
        background_tasks.add_task(mobile_bridge.dispatch_peripheral_lockdown, cat)
        logger.info(
            "🚨 Critical track [%s] detected for task %r. Injected 5-min intervention & 25m cap.",
            cat,
            task.title,
        )
    else:
        target_duration = task.estimated_duration_min or 25
        background_tasks.add_task(mobile_bridge.dispatch_session_start, task.title, target_duration)

    session = crud.start_session(db, data, user_id=current_user)
    session.target_duration_min = target_duration

    return session


@router.post("/{session_id}/end", response_model=schemas.SessionRead)
def end_session(
    session_id: str,
    data: schemas.SessionEnd,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    session = crud.end_session(db, session_id, data, user_id=current_user)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
    task = crud.get_task(db, session.task_id, user_id=current_user)
    task_title = task.title if task else "Focus Session"
    background_tasks.add_task(mobile_bridge.dispatch_session_end, task_title)
    return session


@router.get("/active", response_model=schemas.SessionRead)
def get_active_session_endpoint(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Retrieves current user's active focus session."""
    active = crud.get_active_session(db, user_id=current_user)
    if not active:
        raise HTTPException(status_code=404, detail="No active focus session found")
    return active


@router.get("/{session_id}", response_model=schemas.SessionRead)
def get_session_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Retrieves focus session by ID."""
    session = crud.get_session(db, session_id, user_id=current_user)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
    return session


from app.watchdogs.git_guard import check_workspace_activity, verify_workspace_activity

@router.post("/active/complete", response_model=schemas.SessionActiveCompleteResponse)
def complete_active_session(
    data: schemas.SessionActiveComplete,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    active = crud.get_active_session(db, user_id=current_user)
    if not active:
        raise HTTPException(status_code=404, detail="No active session found to complete")

    session = crud.end_session(db, active.id, schemas.SessionEnd(notes=data.notes), user_id=current_user)
    if data.duration_minutes:
        session.actual_focused_seconds = data.duration_minutes * 60
        db.commit()
        db.refresh(session)

    task = crud.get_task(db, session.task_id, user_id=current_user)
    task_title = task.title if task else "Focus Session"
    category = (task.category or "").lower().strip() if task else "coding"
    background_tasks.add_task(mobile_bridge.dispatch_session_end, task_title)

    proof = data.code_snippet or data.proof_text or ""
    question = None
    if proof:
        git_context = None
        if category == "coding":
            activity = check_workspace_activity(since_timestamp=session.started_at)
            if not activity.get("has_activity", False):
                raise HTTPException(
                    status_code=400,
                    detail="Zero file modifications detected in your workspace during this coding block. Make and save code changes before viva defense.",
                )
            git_context = activity.get("summary")

        question = generate_viva_question(
            proof_text=proof,
            category=category,
            pasted_burst_detected=session.pasted_burst_detected,
            git_context=git_context,
        )
        crud.set_viva_question(db, session.id, question, proof, user_id=current_user)

    return schemas.SessionActiveCompleteResponse(
        session_id=session.id,
        task_id=session.task_id,
        status="completed",
        verification_status=session.verification_status,
        viva_question=question,
        actual_focused_seconds=session.actual_focused_seconds,
        pasted_burst_detected=session.pasted_burst_detected,
    )


@router.post("/active/paste-event", response_model=schemas.PasteEventResponse)
def paste_event(
    data: schemas.PasteEvent,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    active = crud.get_active_session(db, user_id=current_user)
    if not active:
        return schemas.PasteEventResponse(
            flagged=False,
            session_id=None,
            message="No active session – paste event ignored.",
        )

    crud.flag_paste_on_session(db, active.id, user_id=current_user)
    return schemas.PasteEventResponse(
        flagged=True,
        session_id=active.id,
        message="High paste burst recorded by watchdog. Viva difficulty adjusted.",
    )


@router.post("/{session_id}/complete", response_model=schemas.VivaResult)
def complete_session(
    session_id: str,
    data: schemas.VivaRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Submits proof of work and completes session, verifying local file/git activity
    before unlocking AI viva defense.
    """
    return request_viva(session_id, data, db, current_user)


@router.post("/{session_id}/request-viva", response_model=schemas.VivaResult)
def request_viva(
    session_id: str,
    data: schemas.VivaRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    session = db.query(models.FocusSession).filter(
        models.FocusSession.id == session_id,
        models.FocusSession.user_id == current_user,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")

    task = crud.get_task(db, session.task_id, user_id=current_user)
    category = (task.category or "").lower().strip() if task else "coding"

    git_context = None
    if category == "coding":
        activity = check_workspace_activity(since_timestamp=session.started_at)
        if not activity.get("has_activity", False):
            raise HTTPException(
                status_code=400,
                detail="Zero file modifications detected in your workspace during this coding block. Make and save code changes before viva defense.",
            )
        git_context = activity.get("summary")

    question = generate_viva_question(
        proof_text=data.proof_text,
        category=category,
        pasted_burst_detected=session.pasted_burst_detected,
        git_context=git_context,
    )
    crud.set_viva_question(db, session_id, question, data.proof_text, user_id=current_user)

    return schemas.VivaResult(
        session_id=session_id,
        verification_status="pending_viva",
        viva_question=question,
    )


@router.post("/{session_id}/submit-viva", response_model=schemas.VivaResult)
def submit_viva(
    session_id: str,
    data: schemas.VivaSubmit,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    session = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.id == session_id, models.FocusSession.user_id == current_user)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
    if not session.viva_question:
        raise HTTPException(status_code=400, detail="No viva question set. Call /request-viva first.")

    task = crud.get_task(db, session.task_id, user_id=current_user)
    category = task.category if task else "coding"
    ans_text = data.get_answer() if hasattr(data, "get_answer") else (getattr(data, "answer", None) or getattr(data, "user_answer", ""))

    passed, feedback = evaluate_viva_answer(
        question=session.viva_question,
        answer=ans_text,
        proof_text=session.proof_text or "",
        category=category,
    )
    updated = crud.resolve_viva(db, session_id, ans_text, feedback, passed, user_id=current_user)
    return schemas.VivaResult(
        session_id=session_id,
        verification_status=updated.verification_status if updated else "unverified",
        viva_question=updated.viva_question if updated else None,
        viva_feedback=updated.viva_feedback if updated else None,
        verified=passed,
    )


@router.post("/active/submit-viva", response_model=schemas.VivaResult)
def submit_active_viva(
    data: schemas.VivaSubmit,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    active = crud.get_active_session(db, user_id=current_user)
    if not active:
        session = (
            db.query(models.FocusSession)
            .filter(
                models.FocusSession.user_id == current_user,
                models.FocusSession.verification_status.in_(["pending_viva", "unverified"]),
            )
            .order_by(models.FocusSession.ended_at.desc())
            .first()
        )
        if not session:
            raise HTTPException(status_code=404, detail="No active or pending viva session found")
    else:
        session = active

    return submit_viva(session.id, data, db, current_user)


# ---------------------------------------------------------------------------
# Parked Thoughts (Distraction Scratchpad)
# ---------------------------------------------------------------------------
@router.post("/{session_id}/park-thought", response_model=schemas.ParkedThoughtRead)
def park_thought(
    session_id: str,
    data: schemas.ParkedThoughtCreate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Instantly stores a fleeting thought / distraction to allow immediate refocusing.
    Associates the thought with the active or specified session and its underlying task.
    """
    actual_session = None
    if session_id == "active":
        actual_session = crud.get_active_session(db, user_id=current_user)
    else:
        actual_session = (
            db.query(models.FocusSession)
            .filter(
                models.FocusSession.id == session_id,
                models.FocusSession.user_id == current_user,
            )
            .first()
        )

    task_id = actual_session.task_id if actual_session else data.task_id
    sess_id = actual_session.id if actual_session else (None if session_id == "active" else session_id)

    thought = models.ParkedThought(
        user_id=current_user,
        session_id=sess_id,
        task_id=task_id,
        content=data.content.strip(),
        urgency=data.urgency,
        processed=False,
    )
    db.add(thought)
    db.commit()
    db.refresh(thought)
    logger.info("🧠 Thought parked for user %s | session=%s | urgency=%s", current_user, sess_id, data.urgency)
    return thought


@router.get("/{session_id}/parked-thoughts", response_model=list[schemas.ParkedThoughtRead])
def get_parked_thoughts(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Fetches parked thoughts for a session (or all recent thoughts if session_id is 'all').
    """
    query = db.query(models.ParkedThought).filter(models.ParkedThought.user_id == current_user)
    if session_id == "active":
        active = crud.get_active_session(db, user_id=current_user)
        if not active:
            return []
        query = query.filter(models.ParkedThought.session_id == active.id)
    elif session_id != "all":
        query = query.filter(models.ParkedThought.session_id == session_id)

    return query.order_by(models.ParkedThought.created_at.desc()).all()


# ---------------------------------------------------------------------------
# Zen Kiosk Emergency Exit / Strict Stakes Abort
# ---------------------------------------------------------------------------
@router.post("/{session_id}/emergency-abort")
def emergency_abort_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Emergency exit handler from Zen Kiosk Mode:
    - Logs an intentional distraction audit.
    - If aborted before the minimum floor (<10 minutes or before completion), triggers a streak break.
    - Ends the session with verification_status='FAIL'.
    """
    if session_id == "active":
        session = crud.get_active_session(db, user_id=current_user)
    else:
        session = (
            db.query(models.FocusSession)
            .filter(
                models.FocusSession.id == session_id,
                models.FocusSession.user_id == current_user,
            )
            .first()
        )

    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")

    # 1. Log intentional distraction audit
    distraction = models.DistractionLog(
        user_id=current_user,
        session_id=session.id,
        interruption_type="other",
    )
    db.add(distraction)

    # 2. Check if aborted before minimum floor (e.g., < 10 mins / 600s)
    now = datetime.now(timezone.utc)
    duration_sec = 0
    if session.started_at:
        s_start = session.started_at
        if s_start.tzinfo is None:
            s_start = s_start.replace(tzinfo=timezone.utc)
        duration_sec = int((now - s_start).total_seconds())
    session.actual_focused_seconds = duration_sec
    session.ended_at = now
    session.verification_status = "rejected"
    session.notes = (session.notes or "") + " [ZEN EMERGENCY ABORT]"

    streak_broken = False
    # If duration is under 10 minutes (600s), break streak
    if duration_sec < 600:
        setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == current_user).first()
        if setting and setting.current_streak_days > 0:
            logger.warning("💥 Streak broken due to early Zen emergency abort (%ds < 600s)", duration_sec)
            setting.current_streak_days = 0
            streak_broken = True

    db.commit()

    task = crud.get_task(db, session.task_id, user_id=current_user)
    task_title = task.title if task else "Focus Session"
    background_tasks.add_task(mobile_bridge.dispatch_session_end, f"ABORTED: {task_title}")

    return {
        "success": True,
        "session_id": session.id,
        "duration_seconds": duration_sec,
        "streak_broken": streak_broken,
        "message": "Session emergency-aborted. Intentional distraction audit logged.",
    }

