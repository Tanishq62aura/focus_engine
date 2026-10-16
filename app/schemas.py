"""
app/schemas.py
Pydantic v2 request/response schemas for the Focus Engine API.
"""
from __future__ import annotations

from datetime import date, datetime, time
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
    answer: Optional[str] = None
    user_answer: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_viva_answer(cls, data):
        if isinstance(data, dict):
            val = data.get("answer") or data.get("user_answer")
            if val:
                data["answer"] = str(val)
                data["user_answer"] = str(val)
        return data

    def get_answer(self) -> str:
        return self.answer or self.user_answer or ""


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
    friction_threshold: int = Field(default=3, ge=1, le=100)
    verification_prompt_override: Optional[str] = None
    custom_viva_prompt: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def sync_viva_prompt(cls, data):
        if isinstance(data, dict):
            if "custom_viva_prompt" in data and not data.get("verification_prompt_override"):
                data["verification_prompt_override"] = data["custom_viva_prompt"]
            elif "verification_prompt_override" in data and not data.get("custom_viva_prompt"):
                data["custom_viva_prompt"] = data["verification_prompt_override"]
        return data


class UserSettingsBase(BaseModel):
    custom_tracks: list[TrackConfig] = Field(default_factory=list)
    workspace_path: str = Field(default=r"D:\code\workspace", max_length=255)
    viva_strictness: Literal["lenient", "standard", "adversarial"] = "standard"
    auto_fail_paste_bursts: bool = True
    ntfy_topic: str = Field(default="focus_engine_ntfy", pattern=r"^[a-zA-Z0-9_\-]{3,64}$")
    peripheral_boundary_enforced: bool = True
    auto_clear_samsung_dnd: bool = True
    peripheral_boundary_meters: int = 3
    default_min_minutes: int = Field(default=25, ge=1, le=180)
    default_target_minutes: int = Field(default=60, ge=5, le=360)
    default_stretch_minutes: int = Field(default=90, ge=10, le=480)
    enable_micro_start_gate: bool = True
    emergency_bail_hold_seconds: int = Field(default=3, ge=1, le=30)
    penalize_early_abort_streak: bool = True
    default_soundscape: Literal["off", "brown_noise", "binaural_40hz", "rain"] = "off"
    default_volume: float = Field(default=0.7, ge=0.0, le=1.0)
    auto_zen_mode: bool = False
    blind_focus_mode: bool = False
    daily_target_minutes: int = Field(default=180, ge=1, le=1440)


class UserSettingsUpdate(BaseModel):
    custom_tracks: Optional[list[TrackConfig]] = None
    tracks: Optional[list[TrackConfig]] = None
    workspace_path: Optional[str] = Field(default=None, max_length=255)
    viva_strictness: Optional[Literal["lenient", "standard", "adversarial"]] = None
    auto_fail_paste_bursts: Optional[bool] = None
    ntfy_topic: Optional[str] = Field(default=None, pattern=r"^[a-zA-Z0-9_\-]{3,64}$")
    peripheral_boundary_enforced: Optional[bool] = None
    auto_clear_samsung_dnd: Optional[bool] = None
    peripheral_boundary_meters: Optional[int] = Field(default=None, ge=1, le=20)
    default_min_minutes: Optional[int] = Field(default=None, ge=1, le=180)
    default_target_minutes: Optional[int] = Field(default=None, ge=5, le=360)
    default_stretch_minutes: Optional[int] = Field(default=None, ge=10, le=480)
    enable_micro_start_gate: Optional[bool] = None
    emergency_bail_hold_seconds: Optional[int] = Field(default=None, ge=1, le=30)
    penalize_early_abort_streak: Optional[bool] = None
    default_soundscape: Optional[Literal["off", "brown_noise", "binaural_40hz", "rain"]] = None
    default_volume: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    auto_zen_mode: Optional[bool] = None
    blind_focus_mode: Optional[bool] = None
    daily_target_minutes: Optional[int] = Field(default=None, ge=1, le=1440)


class UserSettingsResponse(UserSettingsBase):
    user_id: str
    tracks: list[TrackConfig] = Field(default_factory=list)
    current_streak_days: int = 0
    longest_streak_days: int = 0
    daily_floor_target: int = 2

    model_config = {"from_attributes": True}


# UserSettingsRead alias for backward compatibility
UserSettingsRead = UserSettingsResponse


class DailyScorecardResponse(BaseModel):
    date: str  # ISO date format: YYYY-MM-DD
    score: float = Field(..., ge=0.0, le=100.0)
    verified_minutes: float
    target_minutes: int
    completed_blocks: int
    distractions: int
    paste_bursts: int
    status_grade: Literal["OPTIMAL", "SUBPAR", "CRITICAL_DRIFT"]


# ---------------------------------------------------------------------------
# Embedded AI Support & Query Assistant
# ---------------------------------------------------------------------------
class SupportChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None


class SupportChatResponse(BaseModel):
    reply: str
    session_id: Optional[str] = None
    action: Optional[str] = None
    data: Optional[dict[str, Any]] = None


class CategoryApprovalRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=30)
    id: Optional[str] = Field(None, max_length=30)
    label: Optional[str] = Field(None, max_length=30)
    requires_code_watchdog: bool = False
    friction_threshold: int = 3
    default_duration_min: int = 45
    custom_viva_prompt: Optional[str] = None
    justification: Optional[str] = None


# ---------------------------------------------------------------------------
# Native Authentication
# ---------------------------------------------------------------------------
class AuthRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


# ---------------------------------------------------------------------------
# Parked Thoughts (Distraction Scratchpad)
# ---------------------------------------------------------------------------
class ParkedThoughtCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    urgency: Literal["low", "medium", "high"] = "low"
    task_id: Optional[str] = None


class ParkedThoughtRead(BaseModel):
    id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    content: str
    urgency: str
    processed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Circadian Resistance Matrix & Streak Engine
# ---------------------------------------------------------------------------
class CircadianMatrixResponse(BaseModel):
    days: list[str]  # e.g. ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours: list[int]  # 0..23
    matrix: list[list[float]]  # 7x24 float values (0.0 clean focus to 1.0 severe resistance)
    total_sessions_analyzed: int
    total_interruptions_analyzed: int
    peak_friction_slot: Optional[dict[str, Any]] = None


class StreakReport(BaseModel):
    current_streak_days: int
    longest_streak_days: int
    daily_floor_target: int
    today_verified_blocks: int
    floor_met_today: bool
    last_active_date: Optional[str] = None
    status: str


# ---------------------------------------------------------------------------
# Window-Anchored Study Timetable & Scheduling
# ---------------------------------------------------------------------------
class TimetableBlockCreate(BaseModel):
    day_number: int = Field(..., ge=1, le=365)
    target_date: date
    window_start: time
    window_deadline: time
    duration_minutes: int = Field(default=120, gt=0)
    topic: str = Field(..., min_length=1, max_length=128)
    deliverable_spec: str = Field(..., min_length=1)
    verification_rules: str = Field(..., min_length=1)
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETED", "FORFEIT"] = "PENDING"

    @model_validator(mode="after")
    def validate_window_and_duration(self) -> TimetableBlockCreate:
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be > 0")
        if self.window_deadline <= self.window_start:
            raise ValueError("window_deadline must be strictly after window_start")
        return self


class TimetableBlockResponse(BaseModel):
    id: int
    timetable_id: int
    day_number: int
    target_date: date
    window_start: time
    window_deadline: time
    duration_minutes: int
    topic: str
    deliverable_spec: str
    verification_rules: str
    status: str
    actual_started_at: Optional[datetime] = None
    actual_completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class StudyTimetableCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    track_id: str = Field(default="coding", min_length=1, max_length=64)
    total_days: int = Field(default=7, ge=1, le=30)
    blocks: List[TimetableBlockCreate] = Field(..., min_length=1)

    @field_validator("blocks")
    @classmethod
    def validate_blocks_non_empty(cls, v: List[TimetableBlockCreate]) -> List[TimetableBlockCreate]:
        if not v:
            raise ValueError("Timetable must contain at least 1 block")
        return v


class StudyTimetableResponse(BaseModel):
    id: int
    user_id: str
    title: str
    track_id: str
    total_days: int
    status: str
    created_at: Optional[datetime] = None
    blocks: List[TimetableBlockResponse] = []

    model_config = {"from_attributes": True}


class TimetableActiveResponse(BaseModel):
    active_timetable: Optional[StudyTimetableResponse] = None
    today_block: Optional[TimetableBlockResponse] = None
    is_window_open: bool = False
    window_status: Literal["UPCOMING", "ACTIVE_WINDOW", "DEADLINE_PASSED", "NO_BLOCK_TODAY"] = "NO_BLOCK_TODAY"


class BlockStartResponse(BaseModel):
    block: TimetableBlockResponse
    session_id: str
    early_start: bool = False
    late_start: bool = False
    message: str



