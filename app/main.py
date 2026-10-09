"""
app/main.py
Focus Engine – FastAPI application entrypoint.
Supabase PostgreSQL & SQLite support, JWT auth injection, and PWA static assets.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud, mobile_bridge, models, schemas
from app.auth import AUTH_DISABLED, get_current_user
from app.database import Base, engine, get_db, init_db
from app.verification import audit_excuse_with_ai, evaluate_viva_answer, generate_viva_question

# ---------------------------------------------------------------------------
# YouTube Audit – inline schema
# ---------------------------------------------------------------------------
class YouTubeAuditPayload(BaseModel):
    video_title: str
    channel_name: Optional[str] = None
    url: str


# Broad CS / academic keyword allow-list (lower-cased, matched as substrings)
_ALLOWED_YT_KEYWORDS: frozenset[str] = frozenset({
    # DSA & competitive
    "dsa", "data structure", "algorithm", "leetcode", "competitive programming",
    "graph", "tree", "dynamic programming", "dp ", "binary search", "sorting",
    "recursion", "backtracking", "greedy", "segment tree", "trie",
    # CS theory
    "operating system", "os concept", "process scheduling", "memory management",
    "dbms", "database", "sql", "normalization", "transaction",
    "computer network", "networking", "tcp", "http", "dns", "osi model",
    "system design", "distributed system", "microservice", "load balancer",
    "compiler", "automata", "theory of computation",
    # Development
    "tutorial", "programming", "python", "java", "c++", "javascript",
    "react", "fastapi", "django", "flask", "node", "docker", "kubernetes",
    "git", "linux", "bash", "api",
    # T&P / aptitude
    "aptitude", "quantitative", "logical reasoning", "verbal ability",
    "placement", "campus", "interview", "coding interview",
    # Lectures / theory
    "lecture", "course", "study", "concept", "explanation", "learn",
    "class", "mit", "nptel", "gate", "exam",
})

# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create tables and auto-migrate SQLite if needed
init_db()

app = FastAPI(
    title="Focus Engine",
    description="Behavioral execution & accountability system with Supabase & Google OAuth",
    version="2.0.0",
)

# Mount static directory for frontend HUD & PWA assets
_STATIC_DIR = Path(__file__).parent.parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Root – serve the frontend HUD
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def serve_frontend():
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"message": "Focus Engine API is running. Frontend not found."})
    return FileResponse(str(index))


# ---------------------------------------------------------------------------
# Auth status endpoint
# ---------------------------------------------------------------------------
@app.get("/auth/status")
def auth_status(current_user: str = Depends(get_current_user)):
    return {
        "auth_disabled": AUTH_DISABLED,
        "user_id": current_user,
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@app.post("/tasks", response_model=schemas.TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    data: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    return crud.create_task(db, data, user_id=current_user)


@app.get("/tasks", response_model=list[schemas.TaskRead])
def list_tasks(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    return crud.get_tasks(db, user_id=current_user)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
@app.post("/schedules", response_model=schemas.ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_schedule(
    data: schemas.ScheduleCreate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    task = crud.get_task(db, data.task_id, user_id=current_user)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or unauthorized")
    return crud.create_schedule(db, data, user_id=current_user)


@app.post("/schedules/{schedule_id}/snooze", response_model=schemas.SnoozeResponse)
def snooze_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    sched = crud.snooze_schedule(db, schedule_id, user_id=current_user)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found or unauthorized")
    return schemas.SnoozeResponse(
        id=sched.id,
        snooze_count=sched.snooze_count,
        new_scheduled_start=sched.scheduled_start,
        new_scheduled_end=sched.scheduled_end,
        excuse_required=sched.snooze_count >= 2,
    )


# ---------------------------------------------------------------------------
# Excuse Audit
# ---------------------------------------------------------------------------
@app.post("/excuses/audit", response_model=schemas.ExcuseAuditResponse)
def audit_excuse(
    data: schemas.ExcuseAuditRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    sched = crud.get_schedule(db, data.schedule_id, user_id=current_user)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found or unauthorized")

    task = crud.get_task(db, sched.task_id, user_id=current_user)
    category = task.category if task else "coding"

    is_valid, ai_response = audit_excuse_with_ai(data.stated_reason, category)
    log = crud.audit_excuse(
        db,
        data,
        user_id=current_user,
        ai_response=ai_response,
        is_valid=is_valid,
    )

    return schemas.ExcuseAuditResponse(
        id=log.id,
        user_id=log.user_id,
        schedule_id=log.schedule_id,
        timestamp=log.timestamp,
        stated_reason=log.stated_reason,
        is_valid_blocker=log.is_valid_blocker,
        ai_audit_response=log.ai_audit_response,
        five_min_floor_enforced=not log.is_valid_blocker,
    )


# ---------------------------------------------------------------------------
# Focus Sessions
# ---------------------------------------------------------------------------
@app.post("/sessions/start", response_model=schemas.SessionRead, status_code=status.HTTP_201_CREATED)
def start_session(
    data: schemas.SessionStart,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    task = crud.get_task(db, data.task_id, user_id=current_user)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or unauthorized")
    session = crud.start_session(db, data, user_id=current_user)
    duration = task.estimated_duration_min or 25
    background_tasks.add_task(mobile_bridge.dispatch_session_start, task.title, duration)
    return session


@app.post("/sessions/{session_id}/end", response_model=schemas.SessionRead)
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


# ---------------------------------------------------------------------------
# Paste Event (VS Code Watchdog)
# ---------------------------------------------------------------------------
@app.post("/sessions/active/paste-event", response_model=schemas.PasteEventResponse)
def receive_paste_event(
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
    logger.warning(
        "🚨 PASTE BURST DETECTED | user=%s | session=%s | file=%s | chars=%d | line=%d",
        current_user,
        active.id,
        data.file_name,
        data.char_count,
        data.line_number,
    )
    return schemas.PasteEventResponse(
        flagged=True,
        session_id=active.id,
        message=(
            f"⚠️ Paste burst flagged on session {active.id}. "
            f"File: {data.file_name}, {data.char_count} chars at line {data.line_number}. "
            "Viva will include paste justification."
        ),
    )


# ---------------------------------------------------------------------------
# YouTube Tab Auditor
# ---------------------------------------------------------------------------
@app.post("/sessions/active/youtube-audit")
def youtube_audit(
    data: YouTubeAuditPayload,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Called by the browser extension whenever the user watches a YouTube video
    during a focus session. Returns approval or flags it as a distraction.
    """
    active = crud.get_active_session(db, user_id=current_user)
    if not active:
        return {"status": "no_session", "is_distraction": False}

    task = crud.get_task(db, active.task_id, user_id=current_user)
    task_title = (task.title or "").lower() if task else ""

    title_lower = data.video_title.lower()

    # Build a per-task keyword set from the task title words (≥4 chars)
    task_keywords = {w for w in re.split(r'\W+', task_title) if len(w) >= 4}

    # Check 1 – task title overlap
    task_match = any(kw in title_lower for kw in task_keywords if kw)

    # Check 2 – global CS / academic allow-list
    allowed_match = any(kw in title_lower for kw in _ALLOWED_YT_KEYWORDS)

    is_distraction = not (task_match or allowed_match)

    if is_distraction:
        crud.log_distraction(
            db,
            schemas.DistractionCreate(
                session_id=active.id,
                interruption_type="youtube",
            ),
            user_id=current_user,
        )
        logger.warning(
            "📺 YOUTUBE DISTRACTION | user=%s | session=%s | title=%r | url=%s",
            current_user,
            active.id,
            data.video_title,
            data.url,
        )
        return {
            "status": "flagged",
            "is_distraction": True,
            "message": (
                f"⚠️ Off-task video detected: '{data.video_title}'. "
                "Distraction logged against your session."
            ),
        }

    logger.info(
        "✅ YOUTUBE APPROVED | user=%s | session=%s | title=%r",
        current_user,
        active.id,
        data.video_title,
    )
    return {
        "status": "approved",
        "is_distraction": False,
        "message": f"On-task video approved: '{data.video_title}'",
    }


# ---------------------------------------------------------------------------
# Distractions
# ---------------------------------------------------------------------------
@app.post("/distractions", response_model=schemas.DistractionRead, status_code=status.HTTP_201_CREATED)
def log_distraction(
    data: schemas.DistractionCreate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    session = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.id == data.session_id, models.FocusSession.user_id == current_user)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found or unauthorized")
    return crud.log_distraction(db, data, user_id=current_user)


# ---------------------------------------------------------------------------
# Viva – multi-step AI verification
# ---------------------------------------------------------------------------
@app.post("/sessions/{session_id}/request-viva", response_model=schemas.VivaResult)
def request_viva(
    session_id: str,
    data: schemas.VivaRequest,
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
    if session.verification_status not in ("pending_viva", "unverified"):
        raise HTTPException(
            status_code=400,
            detail=f"Session verification status is already '{session.verification_status}'",
        )

    task = crud.get_task(db, session.task_id, user_id=current_user)
    category = task.category if task else "coding"

    question = generate_viva_question(
        proof_text=data.proof_text,
        category=category,
        pasted_burst_detected=session.pasted_burst_detected,
    )
    crud.set_viva_question(db, session_id, question, data.proof_text, user_id=current_user)
    return schemas.VivaResult(
        session_id=session_id,
        verification_status="pending_viva",
        viva_question=question,
        viva_feedback=None,
    )


@app.post("/sessions/{session_id}/submit-viva", response_model=schemas.VivaResult)
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

    passed, feedback = evaluate_viva_answer(
        question=session.viva_question,
        answer=data.answer,
        proof_text=session.proof_text or "",
        category=category,
    )
    updated = crud.resolve_viva(db, session_id, data.answer, feedback, passed, user_id=current_user)
    return schemas.VivaResult(
        session_id=session_id,
        verification_status=updated.verification_status if updated else "unverified",
        viva_question=updated.viva_question if updated else None,
        viva_feedback=updated.viva_feedback if updated else None,
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@app.get("/reports/today", response_model=schemas.TodayReport)
def today_report(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    return crud.get_today_report(db, user_id=current_user)


@app.get("/reports/friction-analysis", response_model=schemas.FrictionAnalysisReport)
def friction_analysis(
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    return crud.get_weekly_friction_report(db, user_id=current_user, days=days)

