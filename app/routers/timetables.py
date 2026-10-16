"""
app/routers/timetables.py
Router Implementation: Moderate Window-Anchored Study Timetable & Scheduling subsystem.
Handles authorization, window checks, state transitions, and session startup.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.models import FocusSession, StudyTimetable, Task, TimetableBlock
from app.routers.auth import get_current_user
from app.schemas import StudyTimetableCreate, StudyTimetableResponse, TimetableBlockResponse

logger = logging.getLogger("focus_engine.timetables")

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


@router.get("/active", response_model=Optional[StudyTimetableResponse])
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
        return None

    # Find next target block: in-progress block, or first non-completed block
    target = next((b for b in timetable.blocks if b.status != "COMPLETED"), None)
    timetable.active_target_block = target
    timetable.target_block = target

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

    if block.status == "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail="Cannot start block: block is already COMPLETED",
        )

    # Check for existing running session for this user
    active_session = crud.get_active_session(db, user_id=user_id)

    if block.status == "IN_PROGRESS":
        if active_session:
            active_session.timetable_block_id = block.id
            db.commit()
            logger.info(
                "Timetable block %d is already IN_PROGRESS with active FocusSession %s. Reconnecting user %s.",
                block.id,
                active_session.id,
                user_id,
            )
            return {
                "status": "started",
                "block_id": block.id,
                "day_number": block.day_number,
                "duration_minutes": block.duration_minutes,
                "early_start": False,
                "late_start": False,
                "actual_started_at": block.actual_started_at or active_session.started_at,
                "session_id": active_session.id,
                "reconnected": True,
            }
        else:
            logger.info(
                "Timetable block %d was marked IN_PROGRESS but no active FocusSession exists. Allowing restart/recovery for user %s.",
                block.id,
                user_id,
            )
    elif block.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start block in '{block.status}' status",
        )

    now = datetime.now()
    now_time = now.time()
    cur_mins = now_time.hour * 60 + now_time.minute
    start_mins = block.window_start.hour * 60 + block.window_start.minute
    deadline_mins = block.window_deadline.hour * 60 + block.window_deadline.minute

    if deadline_mins > start_mins:
        early_start = cur_mins < start_mins
        late_start = cur_mins > deadline_mins
    else:
        # Cross-midnight window (e.g. 22:30 to 00:30)
        if cur_mins >= start_mins or cur_mins <= deadline_mins:
            early_start = False
            late_start = False
        elif cur_mins < start_mins and cur_mins > deadline_mins:
            if cur_mins - deadline_mins < start_mins - cur_mins:
                early_start = False
                late_start = True
            else:
                early_start = True
                late_start = False
        else:
            early_start = False
            late_start = False

    utc_now = datetime.now(timezone.utc)
    block.status = "IN_PROGRESS"
    block.actual_started_at = utc_now

    session_id = None
    if active_session:
        active_session.timetable_block_id = block.id
        session_id = active_session.id
    else:
        # Create corresponding task and session for seamless tracking
        task = (
            db.query(Task)
            .filter(Task.user_id == user_id, Task.title == block.topic)
            .first()
        )
        if not task:
            task = Task(
                user_id=user_id,
                title=block.topic,
                category="coding",
                estimated_duration_min=block.duration_minutes,
            )
            db.add(task)
            db.flush()

        new_sess = FocusSession(
            user_id=user_id,
            task_id=task.id,
            started_at=utc_now,
            ended_at=None,
            timetable_block_id=block.id,
        )
        db.add(new_sess)
        db.flush()
        session_id = new_sess.id

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
        "session_id": session_id,
    }
