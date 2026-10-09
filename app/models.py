"""
app/models.py
SQLAlchemy ORM models matching the Focus Engine schema.
All timestamps are timezone-aware UTC (TIMESTAMPTZ in Postgres).
User-scoping via user_id across all core tables.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow() -> datetime:
    """Returns timezone-aware UTC datetime for TIMESTAMPTZ compatibility."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    title = Column(String(255), nullable=False)
    category = Column(
        Enum("coding", "tnp", "theory", name="task_category"),
        nullable=False,
        default="coding",
    )
    priority = Column(Integer, nullable=False, default=3)  # 1 = highest
    estimated_duration_min = Column(Integer, nullable=False, default=60)

    # 3-tier goals (in minutes)
    min_goal = Column(Integer, nullable=False, default=25)
    target_goal = Column(Integer, nullable=False, default=50)
    stretch_goal = Column(Integer, nullable=False, default=90)

    is_completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    schedules = relationship("Schedule", back_populates="task", cascade="all, delete")
    sessions = relationship("FocusSession", back_populates="task", cascade="all, delete")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    scheduled_start = Column(DateTime(timezone=True), nullable=False)
    scheduled_end = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        Enum("pending", "started", "snoozed", "completed", "abandoned", name="schedule_status"),
        nullable=False,
        default="pending",
    )
    snooze_count = Column(Integer, nullable=False, default=0)

    task = relationship("Task", back_populates="schedules")
    sessions = relationship("FocusSession", back_populates="schedule", cascade="all, delete")
    excuse_logs = relationship("ExcuseLog", back_populates="schedule", cascade="all, delete")


# ---------------------------------------------------------------------------
# FocusSession
# ---------------------------------------------------------------------------
class FocusSession(Base):
    __tablename__ = "focus_sessions"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    schedule_id = Column(String(36), ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    is_five_min_intervention = Column(Boolean, nullable=False, default=False)

    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # max(0, actual_start - scheduled_start) in minutes
    start_latency_min = Column(Integer, nullable=False, default=0)
    actual_focused_seconds = Column(Integer, nullable=False, default=0)

    verification_status = Column(
        Enum(
            "unverified",
            "pending_viva",
            "verified",
            "rejected",
            name="verification_status",
        ),
        nullable=False,
        default="unverified",
    )
    pasted_burst_detected = Column(Boolean, nullable=False, default=False)
    proof_text = Column(Text, nullable=True)  # pasted code/notes/scorecard
    viva_question = Column(Text, nullable=True)
    viva_answer = Column(Text, nullable=True)
    viva_feedback = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    task = relationship("Task", back_populates="sessions")
    schedule = relationship("Schedule", back_populates="sessions")
    distraction_logs = relationship(
        "DistractionLog", back_populates="session", cascade="all, delete"
    )


# ---------------------------------------------------------------------------
# DistractionLog
# ---------------------------------------------------------------------------
class DistractionLog(Base):
    __tablename__ = "distraction_logs"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    session_id = Column(
        String(36), ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=False
    )
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    interruption_type = Column(
        Enum("phone", "youtube", "overthinking", "other", name="interruption_type"),
        nullable=False,
        default="other",
    )

    session = relationship("FocusSession", back_populates="distraction_logs")


# ---------------------------------------------------------------------------
# ExcuseLog
# ---------------------------------------------------------------------------
class ExcuseLog(Base):
    __tablename__ = "excuse_logs"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    schedule_id = Column(
        String(36), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    stated_reason = Column(Text, nullable=False)
    is_valid_blocker = Column(Boolean, nullable=False, default=False)
    ai_audit_response = Column(Text, nullable=True)

    schedule = relationship("Schedule", back_populates="excuse_logs")
