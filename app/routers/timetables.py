"""
app/routers/timetables.py
Moderate Window-Anchored Study Timetable & Scheduling subsystem:
- Stage 7-day moderate window timetable proposals
- Enforce pending / commit states with human-in-the-loop flexibility
- Window timing evaluation (window_start vs window_deadline vs server time)
- Session binding upon block start
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.auth import get_current_user
from app.database import get_db

logger = logging.getLogger("focus_engine.timetables")

router = APIRouter(prefix="/timetables", tags=["timetables"])


def _utcnow() -> datetime:
    """Returns timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


@router.post("/stage", response_model=schemas.StudyTimetableResponse, status_code=status.HTTP_200_OK)
def stage_timetable(
    payload: schemas.StudyTimetableCreate,
    commit: bool = Query(True, description="Immediately commit and activate the timetable"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Stages or directly commits a 7-day window-anchored study timetable.
    If commit=True, deactivates previous ACTIVE timetables for this user and activates the new one.
    """
    # If committing, archive/deactivate existing ACTIVE timetables for this user
    if commit:
        active_tables = (
            db.query(models.StudyTimetable)
            .filter(
                models.StudyTimetable.user_id == current_user,
                models.StudyTimetable.status == "ACTIVE",
            )
            .all()
        )
        for t in active_tables:
            t.status = "ARCHIVED"

    timetable = models.StudyTimetable(
        user_id=current_user,
        title=payload.title,
        track_id=payload.track_id,
        total_days=payload.total_days or len(payload.blocks),
        status="ACTIVE" if commit else "PENDING_COMMIT",
    )
    db.add(timetable)
    db.flush()  # Populates timetable.id

    # Create associated blocks
    for b in payload.blocks:
        block = models.TimetableBlock(
            timetable_id=timetable.id,
            day_number=b.day_number,
            target_date=b.target_date,
            window_start=b.window_start,
            window_deadline=b.window_deadline,
            duration_minutes=b.duration_minutes,
            topic=b.topic,
            deliverable_spec=b.deliverable_spec,
            verification_rules=b.verification_rules,
            status=getattr(b, "status", "PENDING") or "PENDING",
        )
        db.add(block)

    db.commit()
    db.refresh(timetable)
    return timetable


@router.get("/active", response_model=schemas.TimetableActiveResponse)
def get_active_timetable(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Retrieves the user's active timetable and today's relevant block.
    Computes real-time window status: UPCOMING, ACTIVE_WINDOW, DEADLINE_PASSED, or NO_BLOCK_TODAY.
    """
    timetable = (
        db.query(models.StudyTimetable)
        .filter(
            models.StudyTimetable.user_id == current_user,
            models.StudyTimetable.status == "ACTIVE",
        )
        .order_by(models.StudyTimetable.id.desc())
        .first()
    )

    if not timetable:
        return schemas.TimetableActiveResponse(
            active_timetable=None,
            today_block=None,
            is_window_open=False,
            window_status="NO_BLOCK_TODAY",
        )

    # Find today's block or the first pending block
    today = date.today()
    now_time = datetime.now().time()

    today_block = (
        db.query(models.TimetableBlock)
        .filter(
            models.TimetableBlock.timetable_id == timetable.id,
            models.TimetableBlock.target_date == today,
        )
        .first()
    )

    # Fallback to the lowest day_number pending block if no block exists for exact date today
    if not today_block:
        today_block = (
            db.query(models.TimetableBlock)
            .filter(
                models.TimetableBlock.timetable_id == timetable.id,
                models.TimetableBlock.status == "PENDING",
            )
            .order_by(models.TimetableBlock.day_number.asc())
            .first()
        )

    if not today_block:
        return schemas.TimetableActiveResponse(
            active_timetable=timetable,
            today_block=None,
            is_window_open=False,
            window_status="NO_BLOCK_TODAY",
        )

    # Calculate window status
    if now_time < today_block.window_start:
        is_window_open = False
        window_status = "UPCOMING"
    elif today_block.window_start <= now_time <= today_block.window_deadline:
        is_window_open = True
        window_status = "ACTIVE_WINDOW"
    else:
        is_window_open = False
        window_status = "DEADLINE_PASSED"

    return schemas.TimetableActiveResponse(
        active_timetable=timetable,
        today_block=today_block,
        is_window_open=is_window_open,
        window_status=window_status,
    )


@router.post("/blocks/{block_id}/start", response_model=schemas.BlockStartResponse)
def start_timetable_block(
    block_id: int,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Starts an execution block from the active timetable:
    - Verifies block exists and status is PENDING
    - Validates server time against window_start and window_deadline:
      * early_start=True if before window_start
      * late_start=True if after window_deadline
    - Transitions block status to IN_PROGRESS and timestamps actual_started_at
    - Creates and binds a corresponding FocusSession
    """
    block = (
        db.query(models.TimetableBlock)
        .join(models.StudyTimetable)
        .filter(
            models.TimetableBlock.id == block_id,
            models.StudyTimetable.user_id == current_user,
        )
        .first()
    )

    if not block:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Timetable block {block_id} not found or unauthorized",
        )

    if block.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Block {block_id} cannot be started because status is '{block.status}' (expected 'PENDING')",
        )

    now = _utcnow()
    current_local_time = datetime.now().time()

    early_start = current_local_time < block.window_start
    late_start = current_local_time > block.window_deadline

    # Mark block in-progress
    block.status = "IN_PROGRESS"
    block.actual_started_at = now

    # Ensure a corresponding Task exists or create a transient scheduled task for this block topic
    task = (
        db.query(models.Task)
        .filter(
            models.Task.user_id == current_user,
            models.Task.title == block.topic,
        )
        .first()
    )

    if not task:
        task = models.Task(
            user_id=current_user,
            title=block.topic,
            category=block.timetable.track_id or "coding",
            priority=2,
            estimated_duration_min=block.duration_minutes,
            min_goal=max(15, int(block.duration_minutes * 0.4)),
            target_goal=block.duration_minutes,
            stretch_goal=int(block.duration_minutes * 1.3),
        )
        db.add(task)
        db.flush()

    # Create associated focus session
    session = models.FocusSession(
        user_id=current_user,
        task_id=task.id,
        is_five_min_intervention=False,
        started_at=now,
        start_latency_min=max(0, int((current_local_time.hour * 60 + current_local_time.minute) - (block.window_start.hour * 60 + block.window_start.minute))) if late_start else 0,
        verification_status="unverified",
    )
    db.add(session)
    db.commit()
    db.refresh(block)
    db.refresh(session)

    if early_start:
        message = f"Early start recorded. Window formally begins at {block.window_start.strftime('%H:%M')}."
    elif late_start:
        message = f"Late start flagged. Window deadline was {block.window_deadline.strftime('%H:%M')}."
    else:
        message = f"Session started cleanly within anchored window ({block.window_start.strftime('%H:%M')} - {block.window_deadline.strftime('%H:%M')})."

    return schemas.BlockStartResponse(
        block=block,
        session_id=session.id,
        early_start=early_start,
        late_start=late_start,
        message=message,
    )
