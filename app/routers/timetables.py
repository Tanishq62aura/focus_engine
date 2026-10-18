"""
app/routers/timetables.py
Router Implementation: Moderate Window-Anchored Study Timetable & Scheduling subsystem.
Handles authorization, window checks, state transitions, and session startup.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.models import FocusSession, StudyTimetable, Task, TimetableBlock
from app.routers.auth import get_current_user
from app.schemas import (
    BlockRescheduleItem,
    StudyTimetableCreate,
    StudyTimetableResponse,
    TimetableBlockResponse,
    TimetableReschedulePayload,
)

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

    audit_notes = payload.audit_notes
    if not audit_notes:
        from app.routers.support import audit_timetable_feasibility
        audit_notes = audit_timetable_feasibility(payload.blocks)

    timetable = StudyTimetable(
        user_id=user_id,
        title=payload.title,
        track_id=payload.track_id,
        total_days=payload.total_days,
        audit_notes=audit_notes,
        status="ACTIVE",
    )
    db.add(timetable)
    db.flush()

    for block_data in payload.blocks:
        block_dict = block_data.model_dump()
        task = (
            db.query(Task)
            .filter(Task.user_id == user_id, func.lower(Task.title) == block_data.topic.lower())
            .first()
        )
        if not task:
            task = Task(
                user_id=user_id,
                title=block_data.topic,
                category=payload.track_id or "coding",
                priority=1,
                estimated_duration_min=block_data.duration_minutes,
                min_goal=min(25, block_data.duration_minutes),
                target_goal=block_data.duration_minutes,
                stretch_goal=int(block_data.duration_minutes * 1.2),
            )
            db.add(task)
            db.flush()
        else:
            if getattr(task, "is_archived", False):
                task.is_archived = False
                task.is_completed = False
            task.estimated_duration_min = block_data.duration_minutes
            task.target_goal = block_data.duration_minutes

        block_dict["task_id"] = task.id
        block = TimetableBlock(
            timetable_id=timetable.id,
            **block_dict,
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


def apply_timetable_reschedule(
    db: Session,
    timetable: StudyTimetable,
    payload: TimetableReschedulePayload,
) -> StudyTimetable:
    mutable_blocks = [b for b in timetable.blocks if b.status in ("PENDING", "IN_PROGRESS")]

    # 1. Apply day shift to mutable blocks
    if payload.shift_days:
        for b in mutable_blocks:
            b.target_date = b.target_date + timedelta(days=payload.shift_days)

    # 2. Apply explicit block adjustments
    for adj in (payload.block_adjustments or []):
        target_b = None
        for b in mutable_blocks:
            if adj.block_id is not None and b.id == adj.block_id:
                target_b = b
                break
            if adj.day_index is not None and (b.day_number == adj.day_index or (adj.day_index == 0 and b.day_number == 1)):
                target_b = b
                break

        if target_b:
            if adj.new_target_date is not None:
                target_b.target_date = adj.new_target_date

            eff_start = adj.new_window_start if adj.new_window_start is not None else target_b.window_start
            eff_deadline = adj.new_window_deadline if adj.new_window_deadline is not None else target_b.window_deadline

            if eff_start >= eff_deadline:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid time window for Day {target_b.day_number}: window_start ({eff_start}) must be earlier than window_deadline ({eff_deadline}).",
                )

            if adj.new_window_start is not None:
                target_b.window_start = adj.new_window_start
            if adj.new_window_deadline is not None:
                target_b.window_deadline = adj.new_window_deadline

    timetable.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(timetable)

    target = next((b for b in timetable.blocks if b.status != "COMPLETED"), None)
    timetable.active_target_block = target
    timetable.target_block = target
    return timetable


@router.patch("/active/reschedule", response_model=StudyTimetableResponse)
def reschedule_active_timetable_endpoint(
    payload: TimetableReschedulePayload,
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active study timetable found",
        )

    return apply_timetable_reschedule(db, timetable, payload)


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
                "task_id": block.task_id,
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

    task = None
    if block.task_id:
        task = db.query(Task).filter(Task.id == block.task_id).first()

    if not task:
        task = (
            db.query(Task)
            .filter(Task.user_id == user_id, func.lower(Task.title) == block.topic.lower())
            .first()
        )
        if not task:
            task = Task(
                user_id=user_id,
                title=block.topic,
                category="coding",
                estimated_duration_min=block.duration_minutes,
                min_goal=min(25, block.duration_minutes),
                target_goal=block.duration_minutes,
                stretch_goal=int(block.duration_minutes * 1.2),
            )
            db.add(task)
            db.flush()
        else:
            if getattr(task, "is_archived", False):
                task.is_archived = False
                task.is_completed = False
        block.task_id = task.id

    session_id = None
    if active_session:
        active_session.timetable_block_id = block.id
        session_id = active_session.id
    else:
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
        "task_id": block.task_id,
        "day_number": block.day_number,
        "duration_minutes": block.duration_minutes,
        "early_start": early_start,
        "late_start": late_start,
        "actual_started_at": block.actual_started_at,
        "session_id": session_id,
    }


class BlockCompletePayload(BaseModel):
    session_id: Optional[str] = None
    passed: bool = True


@router.post("/blocks/{block_id}/complete")
def complete_block_endpoint(
    block_id: int,
    payload: Optional[BlockCompletePayload] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Atomically marks a timetable block and its active session as COMPLETED and verified.
    """
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

    now_utc = datetime.utcnow()
    block.status = "COMPLETED"
    block.actual_completed_at = now_utc

    # Query active StudySession for current_user where status in ['IN_PROGRESS', 'ACTIVE']
    session = None
    req_session_id = payload.session_id if payload else None
    if req_session_id:
        session = (
            db.query(FocusSession)
            .filter(FocusSession.id == req_session_id, FocusSession.user_id == user_id)
            .first()
        )
    if not session:
        session = (
            db.query(FocusSession)
            .filter(
                FocusSession.user_id == user_id,
                FocusSession.timetable_block_id == block.id,
                FocusSession.status.in_(["IN_PROGRESS", "ACTIVE"]),
            )
            .first()
        )
    if not session and block.task_id:
        session = (
            db.query(FocusSession)
            .filter(
                FocusSession.user_id == user_id,
                FocusSession.task_id == block.task_id,
                FocusSession.status.in_(["IN_PROGRESS", "ACTIVE"]),
            )
            .first()
        )
    if not session:
        session = (
            db.query(FocusSession)
            .filter(
                FocusSession.user_id == user_id,
                FocusSession.status.in_(["IN_PROGRESS", "ACTIVE"]),
            )
            .order_by(FocusSession.started_at.desc())
            .first()
        )
    if not session:
        session = crud.get_active_session(db, user_id=user_id)

    if session:
        session.is_verified = True
        session.status = "COMPLETED"
        session.end_time = now_utc
        if not session.start_time:
            session.start_time = now_utc - timedelta(minutes=1)
        st = session.start_time
        et = session.end_time
        if st.tzinfo is None and et.tzinfo is not None:
            st = st.replace(tzinfo=timezone.utc)
        elif st.tzinfo is not None and et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        session.duration_minutes = max(1, int((et - st).total_seconds() / 60))
        session.verified_minutes = session.duration_minutes
        session.timetable_block_id = block.id

    timetable = (
        db.query(StudyTimetable)
        .filter(StudyTimetable.id == block.timetable_id)
        .first()
    )
    if timetable:
        remaining = (
            db.query(TimetableBlock)
            .filter(
                TimetableBlock.timetable_id == timetable.id,
                TimetableBlock.id != block.id,
                TimetableBlock.status != "COMPLETED",
            )
            .count()
        )
        if remaining == 0:
            timetable.status = "COMPLETED"

    # Single atomic transaction commit
    db.commit()
    if session:
        db.refresh(session)
    db.refresh(block)

    return {
        "status": "COMPLETED",
        "block_id": block.id,
        "session_id": session.id if session else None,
        "actual_completed_at": block.actual_completed_at,
        "verified": True,
    }

