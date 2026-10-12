"""
app/routers/settings.py
Router for dynamic study tracks and user execution settings.
"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import UserSetting
from app.schemas import TrackConfig, UserSettingsRead, UserSettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_TRACKS: list[dict] = [
    {
        "id": "coding",
        "label": "Coding & Systems",
        "default_duration_min": 60,
        "requires_code_watchdog": True,
        "friction_threshold": 65,
        "verification_prompt_override": "Evaluate technical precision, algorithmic complexity, and code accuracy.",
    },
    {
        "id": "tnp",
        "label": "T&P Aptitude",
        "default_duration_min": 45,
        "requires_code_watchdog": False,
        "friction_threshold": 75,
        "verification_prompt_override": "Verify speed math and quantitative aptitude step-by-step logic.",
    },
    {
        "id": "theory",
        "label": "CS Theory & Concepts",
        "default_duration_min": 50,
        "requires_code_watchdog": False,
        "friction_threshold": 70,
        "verification_prompt_override": "Evaluate conceptual depth, operating system, and architectural precision.",
    },
    {
        "id": "writing",
        "label": "Technical Writing & Docs",
        "default_duration_min": 45,
        "requires_code_watchdog": False,
        "friction_threshold": 60,
        "verification_prompt_override": "Evaluate structural clarity, technical rigor, and conciseness.",
    },
    {
        "id": "research",
        "label": "Deep Research & Analysis",
        "default_duration_min": 60,
        "requires_code_watchdog": False,
        "friction_threshold": 60,
        "verification_prompt_override": "Verify analytical depth, synthesis of sources, and critical deduction.",
    },
]


def _get_or_create_settings(db: Session, user_id: str) -> UserSetting:
    setting = db.query(UserSetting).filter(UserSetting.user_id == user_id).first()
    if not setting:
        setting = UserSetting(
            user_id=user_id,
            tracks=DEFAULT_TRACKS,
            ntfy_topic="focus_tanishq_engine",
            peripheral_boundary_meters=3,
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


@router.get("", response_model=UserSettingsRead)
@router.get("/", response_model=UserSettingsRead)
def get_user_settings(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    tracks_data = setting.tracks if isinstance(setting.tracks, list) else json.loads(setting.tracks or "[]")
    if not tracks_data:
        tracks_data = DEFAULT_TRACKS
    return UserSettingsRead(
        user_id=setting.user_id,
        tracks=[TrackConfig(**t) for t in tracks_data],
        ntfy_topic=setting.ntfy_topic,
        peripheral_boundary_meters=setting.peripheral_boundary_meters or 3,
    )


@router.post("", response_model=UserSettingsRead)
@router.post("/", response_model=UserSettingsRead)
def update_user_settings(
    payload: UserSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    if payload.ntfy_topic is not None:
        setting.ntfy_topic = payload.ntfy_topic.strip()
    if payload.peripheral_boundary_meters is not None:
        setting.peripheral_boundary_meters = payload.peripheral_boundary_meters
    db.commit()
    db.refresh(setting)
    tracks_data = setting.tracks if isinstance(setting.tracks, list) else json.loads(setting.tracks or "[]")
    if not tracks_data:
        tracks_data = DEFAULT_TRACKS
    return UserSettingsRead(
        user_id=setting.user_id,
        tracks=[TrackConfig(**t) for t in tracks_data],
        ntfy_topic=setting.ntfy_topic,
        peripheral_boundary_meters=setting.peripheral_boundary_meters or 3,
    )


@router.get("/tracks", response_model=List[TrackConfig])
def list_tracks(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    tracks_data = setting.tracks if isinstance(setting.tracks, list) else json.loads(setting.tracks or "[]")
    if not tracks_data:
        tracks_data = DEFAULT_TRACKS
        setting.tracks = DEFAULT_TRACKS
        db.commit()
    return [TrackConfig(**t) for t in tracks_data]


@router.post("/tracks", response_model=TrackConfig, status_code=status.HTTP_201_CREATED)
def create_or_update_track(
    track: TrackConfig,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    tracks_data = list(setting.tracks if isinstance(setting.tracks, list) else json.loads(setting.tracks or "[]"))
    
    # Check if track already exists -> update
    clean_id = track.id.lower().strip().replace(" ", "_")
    track_dict = track.model_dump()
    track_dict["id"] = clean_id

    updated = False
    for i, t in enumerate(tracks_data):
        if t.get("id") == clean_id:
            tracks_data[i] = track_dict
            updated = True
            break
    if not updated:
        tracks_data.append(track_dict)

    setting.tracks = tracks_data
    db.commit()
    db.refresh(setting)
    return TrackConfig(**track_dict)


@router.delete("/tracks/{track_id}")
def delete_track(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    setting = _get_or_create_settings(db, current_user)
    tracks_data = list(setting.tracks if isinstance(setting.tracks, list) else json.loads(setting.tracks or "[]"))
    
    clean_id = track_id.lower().strip()
    new_tracks = [t for t in tracks_data if t.get("id") != clean_id]
    if len(new_tracks) == len(tracks_data):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Track '{track_id}' not found.",
        )

    setting.tracks = new_tracks
    db.commit()
    return {"status": "deleted", "track_id": clean_id, "remaining_count": len(new_tracks)}
