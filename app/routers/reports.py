"""
app/routers/reports.py
Advanced reporting endpoints:
1. Circadian Resistance & Excuse Heatmap (7x24 matrix over 14 days)
2. Streak Counter & Strict Stakes Engine (daily floor verification & streak evaluation)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.auth import get_current_user
from app.database import get_db

logger = logging.getLogger("focus_engine.reports")

router = APIRouter(tags=["reports"])


def _get_or_create_user_settings(db: Session, user_id: str) -> models.UserSetting:
    setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == user_id).first()
    if not setting:
        setting = models.UserSetting(
            user_id=user_id,
            tracks=[],
            current_streak_days=0,
            longest_streak_days=0,
            daily_floor_target=2,
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


@router.get("/circadian-matrix", response_model=schemas.CircadianMatrixResponse)
def get_circadian_matrix(
    days: int = 14,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Aggregates 14-day telemetry joining focus_sessions and distraction_logs:
    - Group by Day of Week (0=Monday, 6=Sunday) and Hour of Day (0-23).
    - Calculates resistance score per bucket based on interruption count,
      early dropouts (<10m), and viva failures.
    - Returns a 7x24 matrix with values from 0.0 (clean focus) to 1.0 (severe friction).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    sessions = (
        db.query(models.FocusSession)
        .filter(
            models.FocusSession.user_id == current_user,
            models.FocusSession.started_at >= cutoff,
        )
        .all()
    )

    distractions = (
        db.query(models.DistractionLog)
        .filter(
            models.DistractionLog.user_id == current_user,
            models.DistractionLog.timestamp >= cutoff,
        )
        .all()
    )

    # 7 days x 24 hours stats buckets
    # Day index: 0=Mon, 1=Tue, ..., 6=Sun
    stats = [[{"sessions": 0, "dropouts": 0, "failures": 0, "distractions": 0} for _ in range(24)] for _ in range(7)]

    for s in sessions:
        if not s.started_at:
            continue
        st = s.started_at
        day_idx = st.weekday()  # Monday is 0 and Sunday is 6
        hour_idx = st.hour

        bucket = stats[day_idx][hour_idx]
        bucket["sessions"] += 1

        duration = s.actual_focused_seconds or 0
        status_val = (s.verification_status or "").lower()
        if duration < 600 and status_val != "verified":
            bucket["dropouts"] += 1
        if status_val == "rejected":
            bucket["failures"] += 1

    for d in distractions:
        if not d.timestamp:
            continue
        ts = d.timestamp
        day_idx = ts.weekday()
        hour_idx = ts.hour
        stats[day_idx][hour_idx]["distractions"] += 1

    matrix: list[list[float]] = []
    peak_score = -1.0
    peak_slot: Optional[dict[str, Any]] = None
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for d_idx in range(7):
        row: list[float] = []
        for h_idx in range(24):
            b = stats[d_idx][h_idx]
            sess_cnt = b["sessions"]
            dist_cnt = b["distractions"]
            drop_cnt = b["dropouts"]
            fail_cnt = b["failures"]

            if sess_cnt == 0 and dist_cnt == 0:
                score = 0.0
            elif sess_cnt == 0 and dist_cnt > 0:
                score = min(1.0, round(dist_cnt * 0.25, 2))
            else:
                interruption_factor = min(dist_cnt / max(sess_cnt, 1), 3.0) / 3.0 * 0.45
                dropout_factor = (drop_cnt / max(sess_cnt, 1)) * 0.35
                fail_factor = (fail_cnt / max(sess_cnt, 1)) * 0.20
                score = min(1.0, round(interruption_factor + dropout_factor + fail_factor, 2))

            row.append(score)
            if score > peak_score:
                peak_score = score
                peak_slot = {
                    "day_idx": d_idx,
                    "day_name": day_names[d_idx],
                    "hour": h_idx,
                    "score": score,
                    "sessions": sess_cnt,
                    "distractions": dist_cnt,
                }
        matrix.append(row)

    return schemas.CircadianMatrixResponse(
        days=day_names,
        hours=list(range(24)),
        matrix=matrix,
        total_sessions_analyzed=len(sessions),
        total_interruptions_analyzed=len(distractions),
        peak_friction_slot=peak_slot,
    )


@router.get("/streak", response_model=schemas.StreakReport)
def get_streak_report(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Evaluates streak according to strict daily floor target:
    - Requires at least `daily_floor_target` (default 2) verified (PASS) blocks per day.
    - Rollover evaluation: calculates consecutive qualified days ending today or yesterday.
    - Updates user profile settings with current and longest streak values.
    """
    setting = _get_or_create_user_settings(db, current_user)
    floor_target = setting.daily_floor_target or 2

    # Query all verified sessions for user
    passed_sessions = (
        db.query(models.FocusSession)
        .filter(
            models.FocusSession.user_id == current_user,
            models.FocusSession.verification_status == "verified",
        )
        .order_by(models.FocusSession.started_at.desc())
        .all()
    )

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    # Group passed sessions by UTC date
    counts_by_date: dict[str, int] = {}
    for s in passed_sessions:
        if s.started_at:
            ds = s.started_at.strftime("%Y-%m-%d")
            counts_by_date[ds] = counts_by_date.get(ds, 0) + 1

    today_count = counts_by_date.get(today_str, 0)
    floor_met_today = today_count >= floor_target

    # Calculate consecutive qualified days
    streak = 0
    # Start checking from today (if met) or yesterday
    check_date = now.date() if floor_met_today else (now - timedelta(days=1)).date()

    # Check if yesterday met the floor if today hasn't met it yet
    if not floor_met_today:
        yesterday_str = check_date.strftime("%Y-%m-%d")
        if counts_by_date.get(yesterday_str, 0) < floor_target:
            # Neither today nor yesterday met the floor -> streak is 0
            streak = 0
        else:
            # Yesterday qualified, count backwards
            while True:
                d_str = check_date.strftime("%Y-%m-%d")
                if counts_by_date.get(d_str, 0) >= floor_target:
                    streak += 1
                    check_date -= timedelta(days=1)
                else:
                    break
    else:
        # Today met, count today and backwards
        while True:
            d_str = check_date.strftime("%Y-%m-%d")
            if counts_by_date.get(d_str, 0) >= floor_target:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

    # Update database
    setting.current_streak_days = streak
    if streak > (setting.longest_streak_days or 0):
        setting.longest_streak_days = streak
    db.commit()
    db.refresh(setting)

    last_active = passed_sessions[0].started_at.strftime("%Y-%m-%d") if passed_sessions else None

    status_label = "ACTIVE" if streak > 0 else ("PENDING_TODAY" if today_count > 0 else "AT_RISK")

    return schemas.StreakReport(
        current_streak_days=setting.current_streak_days,
        longest_streak_days=setting.longest_streak_days,
        daily_floor_target=floor_target,
        today_verified_blocks=today_count,
        floor_met_today=floor_met_today,
        last_active_date=last_active,
        status=status_label,
    )


@router.post("/streak/break")
def force_break_streak(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Force-breaks user streak (e.g. when an active block is prematurely aborted
    before the minimum floor in Zen Kiosk Mode).
    """
    setting = _get_or_create_user_settings(db, current_user)
    prev = setting.current_streak_days
    setting.current_streak_days = 0
    db.commit()
    db.refresh(setting)
    logger.warning("💥 Streak broken for user %s (was %d days)", current_user, prev)
    return {
        "success": True,
        "message": f"Streak broken. Previous streak was {prev} days.",
        "current_streak_days": 0,
        "longest_streak_days": setting.longest_streak_days,
    }


def calculate_daily_scorecard(db: Session, user_id: str, target_date: date) -> dict:
    """
    Computes Daily Execution Index (DEI) for user on target_date:
    1. Query focus_sessions where user_id == user_id and session start date matches target_date
    2. Query infractions from distraction_logs and paste bursts
    3. Query Viva defense outcomes (pass rate)
    4. Compute DEI [0.0 - 100.0] and assign grade (OPTIMAL, SUBPAR, CRITICAL_DRIFT)
    """
    all_sessions = (
        db.query(models.FocusSession)
        .filter(models.FocusSession.user_id == user_id)
        .all()
    )

    day_sessions = [
        s for s in all_sessions
        if s.started_at and s.started_at.date() == target_date
    ]

    completed_sessions = [s for s in day_sessions if s.ended_at is not None]
    completed_blocks = len(completed_sessions)

    verified_sessions = [s for s in completed_sessions if s.verification_status == "verified"]
    total_verified_seconds = sum(s.actual_focused_seconds or 0 for s in verified_sessions)
    total_verified_minutes = round(total_verified_seconds / 60.0, 1)

    setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == user_id).first()
    target_floor_minutes = setting.daily_target_minutes if (setting and setting.daily_target_minutes) else 180

    session_ids = {s.id for s in day_sessions}

    distraction_logs = (
        db.query(models.DistractionLog)
        .filter(models.DistractionLog.user_id == user_id)
        .all()
    )
    today_distractions = [
        d for d in distraction_logs
        if (d.session_id in session_ids) or (d.timestamp and d.timestamp.date() == target_date)
    ]
    distraction_count = len(today_distractions)

    paste_from_logs = sum(1 for d in today_distractions if d.interruption_type == "paste_burst")
    paste_from_sessions = sum(1 for s in completed_sessions if s.pasted_burst_detected)
    paste_burst_count = max(paste_from_logs, paste_from_sessions)

    viva_sessions = [s for s in completed_sessions if s.verification_status in ("verified", "rejected")]
    if viva_sessions:
        passed_vivas = sum(1 for s in viva_sessions if s.verification_status == "verified")
        viva_pass_rate = passed_vivas / len(viva_sessions)
    else:
        viva_pass_rate = 1.0

    volume_ratio = min(1.0, total_verified_minutes / float(target_floor_minutes)) if target_floor_minutes > 0 else 1.0
    integrity_penalty = (distraction_count * 0.05) + (paste_burst_count * 0.15)
    integrity_multiplier = max(0.5, 1.0 - integrity_penalty)
    score = round(volume_ratio * integrity_multiplier * viva_pass_rate * 100.0, 1)

    if score >= 85.0:
        status_grade = "OPTIMAL"
    elif score >= 60.0:
        status_grade = "SUBPAR"
    else:
        status_grade = "CRITICAL_DRIFT"

    return {
        "date": target_date.isoformat(),
        "score": score,
        "verified_minutes": total_verified_minutes,
        "target_minutes": target_floor_minutes,
        "completed_blocks": completed_blocks,
        "distractions": distraction_count,
        "paste_bursts": paste_burst_count,
        "status_grade": status_grade,
    }


@router.get("/daily-scorecard", response_model=schemas.DailyScorecardResponse)
def get_daily_scorecard(
    date_str: Optional[str] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Returns the Daily Execution Scorecard for current user on the given date (default today).
    """
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")
    else:
        target_date = datetime.now().date()

    scorecard = calculate_daily_scorecard(db, current_user, target_date)
    return schemas.DailyScorecardResponse(**scorecard)
