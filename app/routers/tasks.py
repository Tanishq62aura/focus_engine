from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class AutoScheduleRequest(BaseModel):
    available_minutes: int = Field(..., gt=0, le=1440, description="Available window in minutes")
    max_tasks: int = Field(100, gt=0, le=500, description="Upper bound to evaluate")


class TaskResponse(BaseModel):
    id: str
    title: str
    priority: str
    estimated_duration: int
    status: str

    class Config:
        from_attributes = True
        coerce_numbers_to_str = True


@router.post("/auto-schedule", response_model=List[TaskResponse])
def generate_optimal_timetable(
    payload: AutoScheduleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if isinstance(current_user, str) and not hasattr(current_user, "id"):
        current_user = CurrentUser(current_user)

    # Fetch pending tasks, sorted by Priority (P1 > P4) then Shortest Job First
    backlog = (
        db.query(Task)
        .filter(
            Task.user_id == current_user.id,
            Task.status == "PENDING",
        )
        .order_by(
            asc(Task.priority),
            asc(Task.estimated_duration),
        )
        .limit(payload.max_tasks)
        .all()
    )

    if not backlog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending tasks available for scheduling.",
        )

    scheduled_tasks = []
    consumed_time = 0

    for task in backlog:
        if task.estimated_duration <= 0:
            continue

        if consumed_time + task.estimated_duration <= payload.available_minutes:
            scheduled_tasks.append(task)
            consumed_time += task.estimated_duration

        if payload.available_minutes - consumed_time < 5:
            break

    return scheduled_tasks
