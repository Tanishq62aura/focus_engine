"""
app/main.py
Focus Engine – FastAPI application entrypoint.
Supabase PostgreSQL & SQLite support, JWT auth injection, and PWA static assets.
"""
from __future__ import annotations

import logging
import os
import re
import socket
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud, mobile_bridge, models, schemas
from app.routers import auth, reports, sessions, settings, support
from app.auth import (
    AUTH_DISABLED,
    DEV_USER_ID,
    SUPABASE_JWT_SECRET,
    HTTPAuthorizationCredentials,
    bearer_scheme,
    decode_token,
    get_current_user,
    jwt,
)
from app.database import Base, engine, get_db, init_db
from app.verification import audit_excuse_with_ai, evaluate_viva_answer, generate_viva_question
from app.watchdogs.git_guard import check_workspace_activity, verify_workspace_activity


def get_local_ip() -> str:
    """Extract the active LAN IPv4 address for local Wi-Fi connectivity."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def get_cloudflare_tunnel_url() -> Optional[str]:
    """Extract the most recent active *.trycloudflare.com URL from logs/tunnel.log."""
    log_file = Path(__file__).resolve().parent.parent / "logs" / "tunnel.log"
    if not log_file.exists():
        return None
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", f.read())
            if matches:
                return matches[-1]
    except Exception:
        pass
    return None

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


@app.on_event("startup")
def startup_banner():
    local_ip = get_local_ip()
    tunnel_url = get_cloudflare_tunnel_url() or "Initializing..."
    logger.info("=" * 60)
    logger.info("🚀 Focus Engine Online")
    logger.info("🌐 Cloudflare Tunnel: %s", tunnel_url)
    logger.info("⚡ Local LAN Direct:   http://%s:8000", local_ip)
    logger.info("💻 Localhost:          http://127.0.0.1:8000")
    logger.info("=" * 60)


@app.on_event("startup")
def startup_scheduler():
    try:
        from app.schedulers.scorecard_job import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning("Could not initialize background scheduler: %s", e)


@app.on_event("shutdown")
def shutdown_scheduler():
    try:
        from app.schedulers.scorecard_job import stop_scheduler
        stop_scheduler()
    except Exception as e:
        pass


# Mount static directory for frontend HUD & PWA assets
_STATIC_DIR = Path(__file__).parent.parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Mount API routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(settings.router, prefix="/settings", tags=["settings"])
app.include_router(support.router, prefix="/api/support", tags=["support"])
app.include_router(support.router, tags=["support"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(sessions.router, tags=["sessions"])


# ---------------------------------------------------------------------------
# Root – serve the frontend HUD
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def serve_frontend():
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"message": "Focus Engine API is running. Frontend not found."})
    return FileResponse(
        str(index),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ---------------------------------------------------------------------------
# Auth status & Dev token endpoints
# ---------------------------------------------------------------------------
@app.get("/auth/status")
def auth_status(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if AUTH_DISABLED:
        return {
            "authenticated": True,
            "auth_disabled": True,
            "user_id": DEV_USER_ID,
        }
    if not credentials or not credentials.credentials:
        return {
            "authenticated": False,
            "auth_disabled": False,
            "user_id": None,
        }
    try:
        payload = decode_token(credentials.credentials)
        uid = payload.get("sub") or payload.get("user_id") or payload.get("email") or DEV_USER_ID
        return {
            "authenticated": True,
            "auth_disabled": False,
            "user_id": str(uid),
        }
    except Exception:
        return {
            "authenticated": False,
            "auth_disabled": False,
            "user_id": None,
        }


DEV_MODE = os.getenv("DEV_MODE", "true").lower() in ("true", "1", "yes")

@app.post("/auth/dev-token")
def issue_dev_token():
    """Generates an authentic HS256 development JWT for local testing and dev mode."""
    if not DEV_MODE and not AUTH_DISABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Development bypass token is disabled in production.",
        )
    secret = SUPABASE_JWT_SECRET
    if not secret:
        if AUTH_DISABLED:
            secret = "dev-insecure-local-only-key"
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="SUPABASE_JWT_SECRET must be configured in environment variables.",
            )
    dev_user = DEV_USER_ID or "dev-user"
    if not jwt:
        raise HTTPException(status_code=500, detail="JWT library unavailable on server")
    token = jwt.encode({"sub": dev_user, "role": "authenticated", "aud": "authenticated"}, secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "user_id": dev_user}



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
    from app.routers import sessions
    return sessions.start_session(data, background_tasks, db, current_user)


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


@app.post("/sessions/active/complete", response_model=schemas.SessionActiveCompleteResponse)
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

    task = crud.get_task(db, session.task_id, user_id=current_user)
    cat = (task.category or "").lower() if task else "coding"
    classification = None

    note_text = (data.note or "").lower()
    if cat == "coding" and any(k in note_text for k in ("neovim", "nvim", "plugin", "theme", "config", "vimrc", "dotfile")):
        classification = "Digital Drift (Meta-Work Warning)"
        logger.warning("⚠️ Meta-work detected during coding session [%s]: %s", session.id, note_text)
        meta_note = f"[Digital Drift: Meta-Work]: {data.note}"
        session.notes = f"{session.notes} | {meta_note}" if session.notes else meta_note
        db.commit()

    log_entry = crud.log_distraction(db, data, user_id=current_user)
    if classification:
        log_entry.classification = classification
    return log_entry


# ---------------------------------------------------------------------------
# Viva – multi-step AI verification
# ---------------------------------------------------------------------------
@app.post("/sessions/{session_id}/complete", response_model=schemas.VivaResult)
def complete_session(
    session_id: str,
    data: schemas.VivaRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Submits proof of work and completes session after verifying local git/file activity."""
    return request_viva(session_id, data, db, current_user)


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
        verified=(updated.verification_status == "verified") if updated else False,
    )


@app.post("/sessions/active/submit-viva", response_model=schemas.VivaResult)
def submit_active_viva(
    data: schemas.VivaSubmit,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    session = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.user_id == current_user)
        .order_by(models.FocusSession.started_at.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No session found")
    return submit_viva(session.id, data, db, current_user)



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


@app.get("/reports/excuse-clusters", response_model=schemas.ExcuseClusterReport)
def excuse_clusters_report(
    days: int = 14,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Aggregates logged rationalizations and distractions, clusters behavioral friction triggers,
    and calculates initiation resistance and baseline latency penalties across tracks.
    """
    from app.analytics.friction_clustering import ExcuseClusterEngine

    engine = ExcuseClusterEngine(db, user_id=current_user, days=days)
    return engine.run_analysis()


# ---------------------------------------------------------------------------
# Network Endpoints (LAN Direct & Cloudflare Edge)
# ---------------------------------------------------------------------------
@app.get("/api/network/endpoints")
def network_endpoints():
    """
    Exposes active local LAN IP and Cloudflare Tunnel endpoints
    for zero-latency mobile Wi-Fi connect and remote edge sync.
    """
    local_ip = get_local_ip()
    tunnel_url = get_cloudflare_tunnel_url()
    return {
        "local_ip": local_ip,
        "lan_url": f"http://{local_ip}:8000",
        "tunnel_url": tunnel_url,
        "port": 8000,
    }


