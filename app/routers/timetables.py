"""
app/routers/timetables.py
Router Implementation: Moderate Window-Anchored Study Timetable & Scheduling subsystem.
Handles authorization, window checks, state transitions, and session startup.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import StudyTimetable, TimetableBlock
from app.routers.auth import get_current_user
from app.schemas import StudyTimetableCreate, StudyTimetableResponse, TimetableBlockResponse

router = APIRouter()


def _get_user_id(current_user: Any) -> str:
    """Defensively extract user ID whether current_user is a string or an object with .id attribute."""
    if hasattr(current_user, "id"):
        return str(current_user.id)
    return str(current_user)


@router.post("/stage", response_model=StudyTimetableResponse, status_code=status.HTTP_201_CREATED)
def stage_timetable(
    payload: StudyTimetableCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    db.query(StudyTimetable).filter(
        StudyTimetable.user_id == user_id,
        StudyTimetable.status == "ACTIVE",
    ).update({"status": "ARCHIVED"})

    timetable = StudyTimetable(
        user_id=user_id,
        title=payload.title,
        track_id=payload.track_id,
        total_days=payload.total_days,
        status="ACTIVE",
    )
    db.add(timetable)
    db.flush()

    for block_data in payload.blocks:
        block = TimetableBlock(
            timetable_id=timetable.id,
            **block_data.model_dump(),
        )
        db.add(block)

    db.commit()
    db.refresh(timetable)
    return timetable


@router.get("/active", response_model=StudyTimetableResponse)
def get_active_timetable(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    timetable = (
        db.query(StudyTimetable)
        .filter(
            StudyTimetable.user_id == user_id,
            StudyTimetable.status == "ACTIVE",
        )
        .first()
    )
    if not timetable:
        raise HTTPException(status_code=404, detail="No active study timetable found")
    return timetable


@router.post("/blocks/{block_id}/start")
def start_block_session(
    block_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    block = (
        db.query(TimetableBlock)
        .join(StudyTimetable)
        .filter(
            TimetableBlock.id == block_id,
            StudyTimetable.user_id == user_id,
        )
        .first()
    )

    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    if block.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start block in '{block.status}' status",
        )

    now = datetime.now()
    now_time = now.time()
    early_start = now_time < block.window_start
    late_start = now_time > block.window_deadline

    block.status = "IN_PROGRESS"
    block.actual_started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(block)

    return {
        "status": "started",
        "block_id": block.id,
        "day_number": block.day_number,
        "duration_minutes": block.duration_minutes,
        "early_start": early_start,
        "late_start": late_start,
        "actual_started_at": block.actual_started_at,
    }
