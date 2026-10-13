"""
app/routers/settings.py
Router for user execution settings, dynamic study tracks, system endpoints probe,
device test ping, and telemetry export.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db

logger = logging.getLogger("focus_engine.settings")

router = APIRouter()

DEFAULT_TRACKS: list[dict] = [
    {
        "id": "coding",
        "label": "Coding / Systems",
        "default_duration_min": 60,
        "requires_code_watchdog": True,
        "friction_threshold": 3,
        "verification_prompt_override": "Evaluate technical precision, algorithmic complexity, and code accuracy.",
        "custom_viva_prompt": "Evaluate technical precision, algorithmic complexity, and code accuracy.",
    },
    {
        "id": "tnp",
        "label": "T&P Aptitude",
        "default_duration_min": 45,
        "requires_code_watchdog": False,
        "friction_threshold": 3,
        "verification_prompt_override": "Verify speed math and quantitative aptitude step-by-step logic.",
        "custom_viva_prompt": "Verify speed math and quantitative aptitude step-by-step logic.",
    },
    {
        "id": "theory",
        "label": "Core Theory / Systems",
        "default_duration_min": 50,
        "requires_code_watchdog": False,
        "friction_threshold": 2,
        "verification_prompt_override": "Evaluate conceptual depth, operating system, and architectural precision.",
        "custom_viva_prompt": "Evaluate conceptual depth, operating system, and architectural precision.",
    },
    {
        "id": "writing",
        "label": "Technical Writing & Docs",
        "default_duration_min": 45,
        "requires_code_watchdog": False,
        "friction_threshold": 3,
        "verification_prompt_override": "Evaluate structural clarity, technical rigor, and conciseness.",
        "custom_viva_prompt": "Evaluate structural clarity, technical rigor, and conciseness.",
    },
    {
        "id": "research",
        "label": "Deep Research & Analysis",
        "default_duration_min": 60,
        "requires_code_watchdog": False,
        "friction_threshold": 3,
        "verification_prompt_override": "Verify analytical depth, synthesis of sources, and critical deduction.",
        "custom_viva_prompt": "Verify analytical depth, synthesis of sources, and critical deduction.",
    },
]


def _get_or_create_settings(db: Session, user_id: str) -> models.UserSetting:
    setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == user_id).first()
    if not setting:
        setting = models.UserSetting(
            user_id=user_id,
            tracks=DEFAULT_TRACKS,
            custom_tracks=DEFAULT_TRACKS,
            workspace_path=r"D:\code\workspace",
            viva_strictness="standard",
            auto_fail_paste_bursts=True,
            ntfy_topic="focus_engine_ntfy",
            peripheral_boundary_enforced=True,
            auto_clear_samsung_dnd=True,
            peripheral_boundary_meters=3,
            default_min_minutes=25,
            default_target_minutes=60,
            default_stretch_minutes=90,
            enable_micro_start_gate=True,
            emergency_bail_hold_seconds=3,
            penalize_early_abort_streak=True,
            default_soundscape="off",
            default_volume=0.7,
            auto_zen_mode=False,
            blind_focus_mode=False,
            daily_target_minutes=180,
            current_streak_days=0,
            longest_streak_days=0,
            daily_floor_target=2,
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def _to_response(setting: models.UserSetting) -> schemas.UserSettingsResponse:
    raw_tracks = setting.custom_tracks or setting.tracks
    if isinstance(raw_tracks, str):
        try:
            raw_tracks = json.loads(raw_tracks)
        except Exception:
            raw_tracks = []
    if not raw_tracks:
        raw_tracks = DEFAULT_TRACKS

    track_objs = []
    for t in raw_tracks:
        if isinstance(t, dict):
            track_objs.append(schemas.TrackConfig(**t))
        elif isinstance(t, schemas.TrackConfig):
            track_objs.append(t)

    return schemas.UserSettingsResponse(
        user_id=setting.user_id,
        custom_tracks=track_objs,
        tracks=track_objs,
        workspace_path=setting.workspace_path or r"D:\code\workspace",
        viva_strictness=setting.viva_strictness or "standard",
        auto_fail_paste_bursts=bool(setting.auto_fail_paste_bursts),
        ntfy_topic=setting.ntfy_topic or "focus_engine_ntfy",
        peripheral_boundary_enforced=bool(setting.peripheral_boundary_enforced),
        auto_clear_samsung_dnd=bool(setting.auto_clear_samsung_dnd),
        peripheral_boundary_meters=setting.peripheral_boundary_meters or 3,
        default_min_minutes=setting.default_min_minutes or 25,
        default_target_minutes=setting.default_target_minutes or 60,
        default_stretch_minutes=setting.default_stretch_minutes or 90,
        enable_micro_start_gate=bool(setting.enable_micro_start_gate),
        emergency_bail_hold_seconds=setting.emergency_bail_hold_seconds or 3,
        penalize_early_abort_streak=bool(setting.penalize_early_abort_streak),
        default_soundscape=setting.default_soundscape or "off",
        default_volume=float(setting.default_volume if setting.default_volume is not None else 0.7),
        auto_zen_mode=bool(setting.auto_zen_mode),
        blind_focus_mode=bool(setting.blind_focus_mode),
        daily_target_minutes=setting.daily_target_minutes or 180,
        current_streak_days=setting.current_streak_days or 0,
        longest_streak_days=setting.longest_streak_days or 0,
        daily_floor_target=setting.daily_floor_target or 2,
    )


@router.get("", response_model=schemas.UserSettingsResponse)
@router.get("/", response_model=schemas.UserSettingsResponse)
def get_user_settings(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    return _to_response(setting)


@router.put("", response_model=schemas.UserSettingsResponse)
@router.put("/", response_model=schemas.UserSettingsResponse)
@router.post("", response_model=schemas.UserSettingsResponse)
@router.post("/", response_model=schemas.UserSettingsResponse)
def update_user_settings(
    payload: schemas.UserSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)

    if payload.custom_tracks is not None:
        serialized_tracks = [t.model_dump() for t in payload.custom_tracks]
        setting.custom_tracks = serialized_tracks
        setting.tracks = serialized_tracks
    elif payload.tracks is not None:
        serialized_tracks = [t.model_dump() for t in payload.tracks]
        setting.custom_tracks = serialized_tracks
        setting.tracks = serialized_tracks

    if payload.workspace_path is not None:
        setting.workspace_path = payload.workspace_path.strip()
    if payload.viva_strictness is not None:
        setting.viva_strictness = payload.viva_strictness
    if payload.auto_fail_paste_bursts is not None:
        setting.auto_fail_paste_bursts = payload.auto_fail_paste_bursts
    if payload.ntfy_topic is not None:
        setting.ntfy_topic = payload.ntfy_topic.strip()
    if payload.peripheral_boundary_enforced is not None:
        setting.peripheral_boundary_enforced = payload.peripheral_boundary_enforced
    if payload.auto_clear_samsung_dnd is not None:
        setting.auto_clear_samsung_dnd = payload.auto_clear_samsung_dnd
    if payload.peripheral_boundary_meters is not None:
        setting.peripheral_boundary_meters = payload.peripheral_boundary_meters
    if payload.default_min_minutes is not None:
        setting.default_min_minutes = payload.default_min_minutes
    if payload.default_target_minutes is not None:
        setting.default_target_minutes = payload.default_target_minutes
    if payload.default_stretch_minutes is not None:
        setting.default_stretch_minutes = payload.default_stretch_minutes
    if payload.enable_micro_start_gate is not None:
        setting.enable_micro_start_gate = payload.enable_micro_start_gate
    if payload.emergency_bail_hold_seconds is not None:
        setting.emergency_bail_hold_seconds = payload.emergency_bail_hold_seconds
    if payload.penalize_early_abort_streak is not None:
        setting.penalize_early_abort_streak = payload.penalize_early_abort_streak
    if payload.default_soundscape is not None:
        setting.default_soundscape = payload.default_soundscape
    if payload.default_volume is not None:
        setting.default_volume = payload.default_volume
    if payload.auto_zen_mode is not None:
        setting.auto_zen_mode = payload.auto_zen_mode
    if payload.blind_focus_mode is not None:
        setting.blind_focus_mode = payload.blind_focus_mode
    if payload.daily_target_minutes is not None:
        setting.daily_target_minutes = payload.daily_target_minutes

    db.commit()
    db.refresh(setting)
    return _to_response(setting)


@router.post("/test-ping")
def test_ping_notification(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Dispatches a priority notification to https://ntfy.sh/{ntfy_topic} verifying device connectivity.
    """
    setting = _get_or_create_settings(db, current_user)
    topic = (setting.ntfy_topic or "focus_engine_ntfy").strip()
    url = "https://ntfy.sh"
    message = f"Focus Engine hardware ping verified. Device boundary enforced for user {current_user}."
    payload = {
        "topic": topic,
        "title": "⚡ Focus Engine: Device Connected",
        "message": message,
        "tags": ["white_check_mark", "bell"],
        "priority": 4,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            status_code = response.status
            logger.info("Test ping sent to topic %s (status %d)", topic, status_code)
            return {
                "success": True,
                "topic": topic,
                "url": f"https://ntfy.sh/{topic}",
                "status_code": status_code,
                "message": "Priority test ping dispatched successfully to mobile bridge.",
            }
    except Exception as e:
        logger.warning("Test ping delivery failed to %s: %s", url, e)
        # Even if network drop, return structured feedback
        return {
            "success": False,
            "topic": topic,
            "url": url,
            "error": str(e),
            "message": f"Failed to dispatch ping to {topic}: {str(e)}",
        }


def _detect_lan_ipv4() -> str:
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


def _detect_tunnel_url() -> Optional[str]:
    # Look for logs/tunnel.log
    possible_paths = [
        Path("logs/tunnel.log"),
        Path(__file__).resolve().parent.parent.parent / "logs" / "tunnel.log",
        Path(__file__).resolve().parent.parent / "logs" / "tunnel.log",
    ]
    for p in possible_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", f.read())
                    if matches:
                        return matches[-1]
            except Exception:
                pass
    return None


@router.get("/system-endpoints")
def get_system_endpoints():
    """
    Detects LAN IPv4 via UDP socket probe to 8.8.8.8 and reads live Cloudflare Tunnel URL.
    """
    lan_ip = _detect_lan_ipv4()
    tunnel_url = _detect_tunnel_url()
    return {
        "lan_url": f"http://{lan_ip}:8000",
        "tunnel_url": tunnel_url or "https://initializing.trycloudflare.com",
    }


@router.get("/export-telemetry")
def export_telemetry(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Streams a JSON payload aggregating all focus_sessions, distraction_logs,
    viva_reviews, and excuse_logs for the authenticated user.
    """
    sessions = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.user_id == current_user)
        .order_by(models.FocusSession.started_at.desc())
        .all()
    )
    distractions = (
        db.query(models.DistractionLog)
        .filter(models.DistractionLog.user_id == current_user)
        .order_by(models.DistractionLog.timestamp.desc())
        .all()
    )
    excuses = (
        db.query(models.ExcuseLog)
        .filter(models.ExcuseLog.user_id == current_user)
        .order_by(models.ExcuseLog.timestamp.desc())
        .all()
    )
    thoughts = (
        db.query(models.ParkedThought)
        .filter(models.ParkedThought.user_id == current_user)
        .order_by(models.ParkedThought.created_at.desc())
        .all()
    )
    setting = _get_or_create_settings(db, current_user)

    viva_reviews = []
    session_list = []
    for s in sessions:
        s_dict = {
            "id": s.id,
            "task_id": s.task_id,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "actual_focused_seconds": s.actual_focused_seconds,
            "verification_status": s.verification_status,
            "pasted_burst_detected": s.pasted_burst_detected,
            "notes": s.notes,
        }
        session_list.append(s_dict)
        if s.viva_question or s.viva_answer or s.viva_feedback:
            viva_reviews.append({
                "session_id": s.id,
                "question": s.viva_question,
                "answer": s.viva_answer,
                "feedback": s.viva_feedback,
                "status": s.verification_status,
            })

    distraction_list = [
        {
            "id": d.id,
            "session_id": d.session_id,
            "timestamp": d.timestamp.isoformat() if d.timestamp else None,
            "interruption_type": d.interruption_type,
        }
        for d in distractions
    ]

    excuse_list = [
        {
            "id": e.id,
            "schedule_id": e.schedule_id,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "stated_reason": e.stated_reason,
            "is_valid_blocker": e.is_valid_blocker,
            "ai_audit_response": e.ai_audit_response,
        }
        for e in excuses
    ]

    thought_list = [
        {
            "id": t.id,
            "session_id": t.session_id,
            "content": t.content,
            "urgency": t.urgency,
            "processed": t.processed,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in thoughts
    ]

    payload = {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": current_user,
        "user_settings": _to_response(setting).model_dump(),
        "focus_sessions": session_list,
        "distraction_logs": distraction_list,
        "viva_reviews": viva_reviews,
        "excuse_logs": excuse_list,
        "parked_thoughts": thought_list,
        "summary": {
            "total_sessions": len(session_list),
            "total_distractions": len(distraction_list),
            "total_viva_reviews": len(viva_reviews),
            "total_excuses": len(excuse_list),
            "total_parked_thoughts": len(thought_list),
        },
    }

    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f"attachment; filename=focus_engine_telemetry_{current_user}_{int(datetime.now().timestamp())}.json"
        },
    )


# ---------------------------------------------------------------------------
# Track-specific Endpoints (for backward compatibility)
# ---------------------------------------------------------------------------
@router.get("/tracks", response_model=List[schemas.TrackConfig])
def list_tracks(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    raw = setting.custom_tracks or setting.tracks
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not raw:
        raw = DEFAULT_TRACKS
        setting.custom_tracks = DEFAULT_TRACKS
        setting.tracks = DEFAULT_TRACKS
        db.commit()
    return [schemas.TrackConfig(**t) for t in raw]


@router.post("/tracks", response_model=schemas.TrackConfig, status_code=status.HTTP_201_CREATED)
def create_or_update_track(
    track: schemas.TrackConfig,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    raw = list(setting.custom_tracks or setting.tracks or [])
    clean_id = track.id.lower().strip().replace(" ", "_")
    track_dict = track.model_dump()
    track_dict["id"] = clean_id

    updated = False
    for i, t in enumerate(raw):
        if t.get("id") == clean_id:
            raw[i] = track_dict
            updated = True
            break
    if not updated:
        raw.append(track_dict)

    setting.custom_tracks = raw
    setting.tracks = raw
    db.commit()
    db.refresh(setting)
    return schemas.TrackConfig(**track_dict)


@router.delete("/tracks/{track_id}")
def delete_track(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    raw = list(setting.custom_tracks or setting.tracks or [])
    clean_id = track_id.lower().strip()
    new_tracks = [t for t in raw if t.get("id") != clean_id]
    if len(new_tracks) == len(raw):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Track '{track_id}' not found.",
        )

    setting.custom_tracks = new_tracks
    setting.tracks = new_tracks
    db.commit()
    return {"status": "deleted", "track_id": clean_id, "remaining_count": len(new_tracks)}
