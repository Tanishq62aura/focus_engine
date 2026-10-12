"""
app/schemas.py
Pydantic v2 request/response schemas for the Focus Engine API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="coding", min_length=1, max_length=64)
    priority: int = Field(default=3, ge=1, le=5)
    estimated_duration_min: int = Field(default=60, ge=5)
    min_goal: int = Field(default=25, ge=1)
    target_goal: int = Field(default=50, ge=1)
    stretch_goal: int = Field(default=90, ge=1)

    @field_validator("stretch_goal")
    @classmethod
    def goals_order(cls, v: int, info) -> int:
        data = info.data
        if "target_goal" in data and v < data["target_goal"]:
            raise ValueError("stretch_goal must be >= target_goal")
        return v


class TaskRead(BaseModel):
    id: str
    user_id: Optional[str] = None
    title: str
    category: str
    priority: int = 3
    estimated_duration_min: int
    min_goal: Optional[int] = 25
    target_goal: Optional[int] = 50
    stretch_goal: Optional[int] = 90
    is_completed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
class ScheduleCreate(BaseModel):
    task_id: str
    scheduled_start: datetime
    scheduled_end: datetime

    @field_validator("scheduled_end")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
        data = info.data
        if "scheduled_start" in data and v <= data["scheduled_start"]:
            raise ValueError("scheduled_end must be after scheduled_start")
        return v


class ScheduleRead(BaseModel):
    id: str
    user_id: Optional[str] = None
    task_id: str
    scheduled_start: datetime
    scheduled_end: datetime
    status: str
    snooze_count: int

    model_config = {"from_attributes": True}


class SnoozeResponse(BaseModel):
    id: str
    snooze_count: int
    new_scheduled_start: datetime
    new_scheduled_end: datetime
    excuse_required: bool  # True if snooze_count >= 2


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class SessionStart(BaseModel):
    task_id: str
    schedule_id: Optional[str] = None
    is_five_min_intervention: bool = False


class SessionEnd(BaseModel):
    notes: Optional[str] = None


class SessionRead(BaseModel):
    id: str
    user_id: Optional[str] = None
    task_id: str
    schedule_id: Optional[str]
    is_five_min_intervention: bool
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    start_latency_min: int
    actual_focused_seconds: int
    verification_status: str
    pasted_burst_detected: bool
    notes: Optional[str]
    target_duration_min: Optional[int] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Distraction Log
# ---------------------------------------------------------------------------
class DistractionCreate(BaseModel):
    session_id: str
    interruption_type: Literal["phone", "youtube", "overthinking", "other"] = "other"
    note: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_distraction_type(cls, data):
        if isinstance(data, dict):
            if "type" in data and "interruption_type" not in data:
                data["interruption_type"] = data["type"]
        return data


class DistractionRead(BaseModel):
    id: str
    user_id: Optional[str] = None
    session_id: str
    timestamp: datetime
    interruption_type: str
    classification: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Excuse Audit
# ---------------------------------------------------------------------------
class ExcuseAuditRequest(BaseModel):
    schedule_id: str
    stated_reason: str = Field(..., min_length=3, max_length=2000)


class ExcuseAuditResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    schedule_id: str
    timestamp: datetime
    stated_reason: str
    is_valid_blocker: bool
    ai_audit_response: str
    five_min_floor_enforced: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Paste Event (from VS Code extension)
# ---------------------------------------------------------------------------
class PasteEvent(BaseModel):
    file_name: str
    char_count: int = Field(..., ge=1)
    line_number: int = Field(..., ge=1)


class PasteEventResponse(BaseModel):
    flagged: bool
    session_id: Optional[str]
    message: str


# ---------------------------------------------------------------------------
# Viva
# ---------------------------------------------------------------------------
class VivaRequest(BaseModel):
    proof_text: str = Field(..., min_length=10, description="Pasted code / notes / scorecard as proof")


class VivaSubmit(BaseModel):
    answer: str = Field(..., min_length=5)


class VivaResult(BaseModel):
    session_id: str
    verification_status: str
    viva_question: Optional[str] = None
    viva_feedback: Optional[str] = None
    verified: bool = False


class SessionActiveComplete(BaseModel):
    code_snippet: Optional[str] = None
    proof_text: Optional[str] = None
    notes: Optional[str] = None
    duration_minutes: Optional[int] = None


class SessionActiveCompleteResponse(BaseModel):
    session_id: str
    task_id: str
    status: str = "completed"
    verification_status: str = "pending_viva"
    viva_question: Optional[str] = None
    actual_focused_seconds: int = 0
    pasted_burst_detected: bool = False


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class TodayReport(BaseModel):
    date: str
    total_focused_minutes: float
    verified_sessions: int
    rejected_sessions: int
    pending_sessions: int
    total_sessions: int
    average_start_latency_min: float
    total_distractions: int
    distraction_breakdown: dict
    execution_ratio_pct: float  # (focused / planned) * 100


class CategoryFrictionStats(BaseModel):
    category: str
    label: str
    icon: str
    total_sessions: int
    avg_friction_score: float
    avg_start_latency_min: float
    total_distractions: int
    verification_pass_rate_pct: float
    total_focused_minutes: float


class HighFrictionCulprit(BaseModel):
    category: str
    label: str
    icon: str
    avg_friction_score: float
    avg_start_latency_min: float
    insight: str


class FrictionAnalysisReport(BaseModel):
    period_days: int
    system_friction_index: float
    friction_status: Literal["optimal", "moderate", "severe"]
    total_sessions: int
    total_distractions: int
    overall_pass_rate_pct: float
    categories: list[CategoryFrictionStats]
    culprit: Optional[HighFrictionCulprit] = None
    high_friction_culprit: Optional[HighFrictionCulprit] = None


# ---------------------------------------------------------------------------
# Excuse Clustering & Behavioral Resistance
# ---------------------------------------------------------------------------
class ExcuseClusterItem(BaseModel):
    cluster_id: str
    name: str
    icon: str
    count: int
    percentage: float
    severity: float
    remediation: str
    tracks_impacted: dict[str, int]
    sample_quotes: list[str]


class TrackResistanceItem(BaseModel):
    track: str
    display_name: str
    total_sessions: int
    total_focused_hours: float
    total_interruptions: int
    distraction_count: int
    excuse_count: int
    early_distractions_first_10m: int
    early_distraction_ratio: float
    interruption_rate_per_hour: float
    avg_start_latency_min: float
    high_initiation_resistance: bool
    adjusted_baseline_latency_penalty_min: float
    resistance_score: float
    recommendation: str


class ExcuseClusterReport(BaseModel):
    period_days: int
    cutoff_timestamp: str
    total_interruptions_analyzed: int
    primary_friction_cluster: str
    average_resistance_score: float
    clusters: list[ExcuseClusterItem]
    tracks: dict[str, TrackResistanceItem]
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Dynamic Study Tracks & User Settings
# ---------------------------------------------------------------------------
class TrackConfig(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=128)
    default_duration_min: int = Field(default=60, ge=5, le=360)
    requires_code_watchdog: bool = Field(default=False)
    friction_threshold: int = Field(default=65, ge=1, le=100)
    verification_prompt_override: Optional[str] = None


class UserSettingsRead(BaseModel):
    user_id: str
    tracks: list[TrackConfig]
    ntfy_topic: Optional[str] = None
    peripheral_boundary_meters: int = 3


class UserSettingsUpdate(BaseModel):
    ntfy_topic: Optional[str] = None
    peripheral_boundary_meters: Optional[int] = Field(default=None, ge=1, le=20)


# ---------------------------------------------------------------------------
# Embedded AI Support & Query Assistant
# ---------------------------------------------------------------------------
class SupportChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None


class SupportChatResponse(BaseModel):
    reply: str
    session_id: Optional[str] = None

