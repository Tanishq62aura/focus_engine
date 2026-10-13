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
    session = db.query(models.FocusSession).filter(
        models.FocusSession.id == session_id,
        models.FocusSession.user_id == current_user,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")

    task = crud.get_task(db, session.task_id, user_id=current_user)
    category = task.category if task else "coding"

    status_str, score, feedback = evaluate_viva_answer(
        question=session.viva_question or "",
        user_answer=data.user_answer,
        category=category,
    )
    crud.set_viva_result(db, session_id, status_str, data.user_answer, feedback, score, user_id=current_user)

    return schemas.VivaResult(
        session_id=session_id,
        verification_status=status_str,
        viva_question=session.viva_question,
        viva_feedback=feedback,
        viva_score=score,
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
