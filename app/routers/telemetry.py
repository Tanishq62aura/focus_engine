import uuid
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import FocusSession, SessionHeartbeat, User

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

OFF_TASK_BLACKLIST = {
    "twitter.com", "x.com", "instagram.com", "reddit.com",
    "facebook.com", "tiktok.com", "netflix.com"
}
MAX_ALLOWED_VIOLATIONS = 3


class HeartbeatPayload(BaseModel):
    session_id: str
    source: Literal["vscode", "chrome"]
    event_type: str = Field(..., description="e.g. 'keystroke', 'tab_active', 'focus_lost'")
    active_target: Optional[str] = Field(None, description="Active file path or active browser URL")
    is_idle: bool = False


class HeartbeatResponse(BaseModel):
    status: Literal["OK", "WARNING", "SESSION_TERMINATED"]
    violation_count: int
    message: str


@router.post("/heartbeat", response_model=HeartbeatResponse)
def record_heartbeat(
    payload: HeartbeatPayload,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    now = datetime.now(timezone.utc)
    # Extract string user ID whether current_user is a dict, model, or string
    user_id = current_user.id if hasattr(current_user, "id") else (current_user.get("id") if isinstance(current_user, dict) else str(current_user))

    # Fetch active session strictly bound to authenticated user
    session = db.query(FocusSession).filter(
        FocusSession.id == payload.session_id,
        FocusSession.user_id == user_id,
        FocusSession.ended_at.is_(None),
        FocusSession.status.in_(["IN_PROGRESS", "ACTIVE"])
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active focus session found matching session_id"
        )

    # 2. Evaluate off-task heuristic
    is_on_task = True
    violation_reason = None

    if payload.source == "chrome" and payload.active_target:
        target_lower = payload.active_target.lower()
        if any(domain in target_lower for domain in OFF_TASK_BLACKLIST):
            is_on_task = False
            violation_reason = f"Blacklisted domain accessed: {payload.active_target}"

    if payload.source == "vscode" and (payload.is_idle or payload.event_type == "focus_lost"):
        is_on_task = False
        violation_reason = "Editor window lost focus or user idle"

    # 3. Append-only audit record
    heartbeat_record = SessionHeartbeat(
        id=str(uuid.uuid4()),
        session_id=session.id,
        source=payload.source,
        event_type=payload.event_type,
        timestamp=now,
        is_on_task=is_on_task,
        metadata_payload={
            "target": payload.active_target,
            "is_idle": payload.is_idle,
            "violation_reason": violation_reason
        }
    )
    db.add(heartbeat_record)

    # 4. State Machine Progression
    session.last_heartbeat_at = now

    if not is_on_task:
        session.heartbeat_violation_count = (session.heartbeat_violation_count or 0) + 1
        
        if session.heartbeat_violation_count >= MAX_ALLOWED_VIOLATIONS:
            session.status = "ABORTED"
            session.ended_at = now
            session.notes = ((session.notes or "") + f" [Auto-Aborted: {violation_reason}]").strip()
            db.commit()
            return HeartbeatResponse(
                status="SESSION_TERMINATED",
                violation_count=session.heartbeat_violation_count,
                message=f"Session auto-aborted: {violation_reason}"
            )
        
        db.commit()
        return HeartbeatResponse(
            status="WARNING",
            violation_count=session.heartbeat_violation_count,
            message=f"Focus intrusion detected ({session.heartbeat_violation_count}/{MAX_ALLOWED_VIOLATIONS})"
        )

    db.commit()
    return HeartbeatResponse(
        status="OK",
        violation_count=session.heartbeat_violation_count or 0,
        message="Heartbeat verified on-task"
    )
