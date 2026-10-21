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
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Time,
    case,
    func,
)
from sqlalchemy.ext.hybrid import hybrid_property
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
    category = Column(String(64), nullable=False, default="coding")
    priority = Column(Integer, nullable=False, default=3)  # 1 = highest
    estimated_duration_min = Column(Integer, nullable=False, default=60)

    # 3-tier goals (in minutes)
    min_goal = Column(Integer, nullable=False, default=25)
    target_goal = Column(Integer, nullable=False, default=50)
    stretch_goal = Column(Integer, nullable=False, default=90)

    is_completed = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)
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
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    schedule_id = Column(String(36), ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    timetable_block_id = Column(Integer, ForeignKey("timetable_blocks.id", ondelete="SET NULL"), nullable=True)
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
    _status = Column("status", String(32), nullable=True, default="IN_PROGRESS")
    created_at = Column(DateTime(timezone=True), nullable=True, default=_utcnow)
    last_heartbeat_at = Column(DateTime, nullable=True)
    heartbeat_violation_count = Column(Integer, default=0, nullable=False)
    drift_seconds = Column(Integer, default=0, nullable=False)

    task = relationship("Task", back_populates="sessions")
    schedule = relationship("Schedule", back_populates="sessions")
    distraction_logs = relationship(
        "DistractionLog", back_populates="session", cascade="all, delete"
    )
    parked_thoughts = relationship(
        "ParkedThought", back_populates="session", cascade="all, delete"
    )
    heartbeats = relationship(
        "SessionHeartbeat", back_populates="session", cascade="all, delete-orphan"
    )

    @hybrid_property
    def is_verified(self):
        return self.verification_status == "verified"

    @is_verified.setter
    def is_verified(self, val: bool):
        self.verification_status = "verified" if val else "unverified"

    @is_verified.expression
    def is_verified(cls):
        return cls.verification_status == "verified"

    @hybrid_property
    def start_time(self):
        return self.started_at

    @start_time.setter
    def start_time(self, val):
        self.started_at = val

    @start_time.expression
    def start_time(cls):
        return cls.started_at

    @hybrid_property
    def end_time(self):
        return self.ended_at

    @end_time.setter
    def end_time(self, val):
        self.ended_at = val

    @end_time.expression
    def end_time(cls):
        return cls.ended_at

    @hybrid_property
    def status(self):
        if self._status:
            return self._status
        if self.ended_at is not None:
            return "COMPLETED"
        return "IN_PROGRESS"

    @status.setter
    def status(self, val: str):
        self._status = val
        if val == "COMPLETED" and not self.ended_at:
            self.ended_at = _utcnow()
        elif val in ("IN_PROGRESS", "ACTIVE") and not self.started_at:
            self.started_at = _utcnow()

    @status.expression
    def status(cls):
        return func.coalesce(
            cls._status,
            case(
                (cls.ended_at.isnot(None), "COMPLETED"),
                else_="IN_PROGRESS",
            ),
        )

    @hybrid_property
    def duration_minutes(self):
        if self.actual_focused_seconds:
            return max(1, int(self.actual_focused_seconds / 60))
        if self.started_at and self.ended_at:
            st = self.started_at
            et = self.ended_at
            if st.tzinfo is None and et.tzinfo is not None:
                st = st.replace(tzinfo=timezone.utc)
            elif st.tzinfo is not None and et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            return max(1, int((et - st).total_seconds() / 60))
        return 0

    @duration_minutes.setter
    def duration_minutes(self, val: int):
        self.actual_focused_seconds = max(0, int(val * 60))

    @hybrid_property
    def verified_minutes(self):
        return self.duration_minutes

    @verified_minutes.setter
    def verified_minutes(self, val: int):
        self.duration_minutes = val


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
# ParkedThought (Distraction Scratchpad)
# ---------------------------------------------------------------------------
class ParkedThought(Base):
    __tablename__ = "parked_thoughts"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    session_id = Column(
        String(36), ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=True
    )
    task_id = Column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    content = Column(Text, nullable=False)
    urgency = Column(String(16), nullable=False, default="low")  # low | medium | high
    processed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    session = relationship("FocusSession", back_populates="parked_thoughts")


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


# ---------------------------------------------------------------------------
# UserSetting
# ---------------------------------------------------------------------------
class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    user_id = Column(String(64), nullable=False, index=True, default="dev-user")
    
    # Study Tracks & Verification
    tracks = Column(JSON, nullable=False, default=list)
    custom_tracks = Column(JSON, nullable=True, default=list)
    workspace_path = Column(String(255), nullable=False, default=r"D:\code\workspace")
    viva_strictness = Column(String(32), nullable=False, default="standard")
    auto_fail_paste_bursts = Column(Boolean, nullable=False, default=True)

    # Connectivity & Alerts
    ntfy_topic = Column(String(128), nullable=False, default="focus_engine_ntfy")
    peripheral_boundary_enforced = Column(Boolean, nullable=False, default=True)
    auto_clear_samsung_dnd = Column(Boolean, nullable=False, default=True)
    peripheral_boundary_meters = Column(Integer, nullable=False, default=3)

    # Session Mechanics
    default_min_minutes = Column(Integer, nullable=False, default=25)
    default_target_minutes = Column(Integer, nullable=False, default=60)
    default_stretch_minutes = Column(Integer, nullable=False, default=90)
    enable_micro_start_gate = Column(Boolean, nullable=False, default=True)
    emergency_bail_hold_seconds = Column(Integer, nullable=False, default=3)
    penalize_early_abort_streak = Column(Boolean, nullable=False, default=True)

    # HUD & Audio
    default_soundscape = Column(String(32), nullable=False, default="off")
    default_volume = Column(Float, nullable=False, default=0.7)
    auto_zen_mode = Column(Boolean, nullable=False, default=False)
    blind_focus_mode = Column(Boolean, nullable=False, default=False)

    # Scorecard Floor & Streaks
    daily_target_minutes = Column(Integer, nullable=False, default=180)
    current_streak_days = Column(Integer, nullable=False, default=0)
    longest_streak_days = Column(Integer, nullable=False, default=0)
    daily_floor_target = Column(Integer, nullable=False, default=2)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# StudyTimetable & TimetableBlock
# ---------------------------------------------------------------------------
class StudyTimetable(Base):
    __tablename__ = "study_timetables"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    track_id = Column(String(64), nullable=False, default="coding")
    total_days = Column(Integer, nullable=False, default=7)
    status = Column(String(32), nullable=False, default="ACTIVE")  # ACTIVE, COMPLETED, ARCHIVED
    audit_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, default=func.now(), onupdate=func.now())

    blocks = relationship(
        "TimetableBlock",
        back_populates="timetable",
        cascade="all, delete-orphan",
        order_by="TimetableBlock.day_number",
    )


class TimetableBlock(Base):
    __tablename__ = "timetable_blocks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    timetable_id = Column(
        Integer,
        ForeignKey("study_timetables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_number = Column(Integer, nullable=False)
    target_date = Column(Date, nullable=False)
    window_start = Column(Time, nullable=False)  # e.g. 19:00:00
    window_deadline = Column(Time, nullable=False)  # e.g. 21:00:00
    duration_minutes = Column(Integer, nullable=False, default=120)
    topic = Column(String(128), nullable=False)
    deliverable_spec = Column(Text, nullable=False)
    verification_rules = Column(Text, nullable=False)
    resources = Column(JSON, nullable=True, default=list)  # URLs, docs, video references
    status = Column(String(32), nullable=False, default="PENDING")  # PENDING, IN_PROGRESS, COMPLETED, FORFEIT
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    actual_started_at = Column(DateTime(timezone=True), nullable=True)
    actual_completed_at = Column(DateTime(timezone=True), nullable=True)

    timetable = relationship("StudyTimetable", back_populates="blocks")

    @property
    def completed_at(self):
        return self.actual_completed_at

    @completed_at.setter
    def completed_at(self, value):
        self.actual_completed_at = value


# Model Aliases for backward compatibility
StudyTimetableBlock = TimetableBlock
StudyTimetableModel = StudyTimetable
StudySession = FocusSession


class SessionHeartbeat(Base):
    __tablename__ = "session_heartbeats"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String(36),
        ForeignKey("focus_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source = Column(String(20), nullable=False)  # "vscode" | "chrome"
    event_type = Column(String(50), nullable=False)  # "keystroke", "tab_active", "focus_lost"
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    is_on_task = Column(Boolean, default=True, nullable=False)
    metadata_payload = Column(JSON, nullable=True)

    session = relationship("FocusSession", back_populates="heartbeats")


# ---------------------------------------------------------------------------
# User (Local / SQLite compatibility)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=_new_uuid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    encrypted_password = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# IdempotencyRecord
# ---------------------------------------------------------------------------
class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    status_code = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


