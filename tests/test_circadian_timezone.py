"""
tests/test_circadian_timezone.py
Unit and regression tests for Track 2: Circadian Heatmap Timezone Normalization in app/routers/reports.py.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import get_current_user as auth_get_current_user
from app.database import SessionLocal
from app.main import app
from app.models import DistractionLog, FocusSession, UserSetting
from app.routers.auth import get_current_user as router_get_current_user
from app.routers.reports import get_user_zone

TEST_USER_ID = "tz-test-user"


@pytest.fixture
def client():
    app.dependency_overrides[auth_get_current_user] = lambda: TEST_USER_ID
    app.dependency_overrides[router_get_current_user] = lambda: TEST_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(auth_get_current_user, None)
    app.dependency_overrides.pop(router_get_current_user, None)


@pytest.fixture
def db():
    session = SessionLocal(expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


def test_get_user_zone_fallback():
    """Verify that valid, invalid, and empty timezone strings resolve safely."""
    # Valid timezones
    tz_kolkata = get_user_zone("Asia/Kolkata")
    assert tz_kolkata.key == "Asia/Kolkata"

    tz_ny = get_user_zone("America/New_York")
    assert tz_ny.key == "America/New_York"

    tz_utc = get_user_zone("UTC")
    assert tz_utc.key == "UTC"

    # None and empty strings fall back safely to UTC
    assert get_user_zone(None).key == "UTC"
    assert get_user_zone("").key == "UTC"

    # Invalid timezone strings fall back safely to UTC without raising unhandled exceptions
    assert get_user_zone("Invalid/Unknown_Timezone").key == "UTC"
    assert get_user_zone("Mars/Olympus_Mons").key == "UTC"
    assert get_user_zone("12345").key == "UTC"


def test_circadian_timestamp_conversion_to_asia_kolkata():
    """
    Test that a UTC timestamp (e.g., 2026-10-22 18:30:00 UTC) converts to the correct
    day and hour when mapped to 'Asia/Kolkata' (UTC+5:30 -> 2026-10-23 00:00:00, hour=0, next day).
    """
    utc_dt = datetime(2026, 10, 22, 18, 30, 0, tzinfo=timezone.utc)
    # Thursday: weekday() == 3
    assert utc_dt.weekday() == 3
    assert utc_dt.hour == 18

    target_tz = get_user_zone("Asia/Kolkata")
    local_dt = utc_dt.astimezone(target_tz)

    # In Asia/Kolkata (+5:30), 18:30 + 5:30 is 00:00 of the following day (Friday)
    assert local_dt.year == 2026
    assert local_dt.month == 10
    assert local_dt.day == 23
    assert local_dt.hour == 0
    assert local_dt.minute == 0
    # Friday: weekday() == 4
    assert local_dt.weekday() == 4


def test_circadian_matrix_api_with_timezone_normalization(client, db):
    """
    Test end-to-end circadian-matrix API endpoint:
    - Sets user timezone to Asia/Kolkata.
    - Stages a focus session at 18:30 UTC.
    - Verifies matrix buckets session into Friday (index 4) at hour 0.
    """
    # Clean up any existing test records
    db.query(DistractionLog).filter(DistractionLog.user_id == TEST_USER_ID).delete()
    db.query(FocusSession).filter(FocusSession.user_id == TEST_USER_ID).delete()
    db.query(UserSetting).filter(UserSetting.user_id == TEST_USER_ID).delete()
    db.commit()

    # Create user setting with Asia/Kolkata timezone
    setting = UserSetting(
        user_id=TEST_USER_ID,
        timezone="Asia/Kolkata",
        tracks=[],
        daily_floor_target=2,
    )
    db.add(setting)

    # Insert a session started 2 days ago at 18:30 UTC (Thursday)
    # Thursday 18:30 UTC -> Friday 00:00 in Asia/Kolkata
    base_utc = datetime.now(timezone.utc) - timedelta(days=2)
    # Adjust to a known Thursday (e.g. 2026-10-22 18:30:00 UTC)
    thursday_utc = datetime(2026, 10, 22, 18, 30, 0, tzinfo=timezone.utc)

    # Use a recent timestamp within the 14-day window that falls on a Thursday 18:30 UTC
    now_utc = datetime.now(timezone.utc)
    days_since_thursday = (now_utc.weekday() - 3) % 7
    if days_since_thursday == 0 and now_utc.hour < 19:
        days_since_thursday = 7
    session_st = (now_utc - timedelta(days=days_since_thursday)).replace(
        hour=18, minute=30, second=0, microsecond=0
    )
    # Ensure it's Thursday in UTC
    assert session_st.weekday() == 3

    session = FocusSession(
        user_id=TEST_USER_ID,
        task_id=None,
        started_at=session_st,
        ended_at=session_st + timedelta(minutes=45),
        actual_focused_seconds=2700,
        verification_status="verified",
    )
    db.add(session)
    db.flush()

    distraction = DistractionLog(
        user_id=TEST_USER_ID,
        session_id=session.id,
        timestamp=session_st,
        interruption_type="youtube",
    )
    db.add(distraction)
    db.commit()

    resp = client.get("/api/reports/circadian-matrix?days=14")
    assert resp.status_code == 200
    data = resp.json()

    assert "matrix" in data
    assert len(data["matrix"]) == 7
    assert len(data["matrix"][0]) == 24
    assert data["total_sessions_analyzed"] >= 1
    assert data["total_interruptions_analyzed"] >= 1

    # In Asia/Kolkata (+5:30), Thursday 18:30 UTC is Friday (day index 4) at hour 0!
    # Matrix at Friday (4), hour 0 should reflect friction score > 0
    assert data["matrix"][4][0] > 0.0

    # Switch timezone to UTC
    setting.timezone = "UTC"
    db.commit()

    resp_utc = client.get("/api/reports/circadian-matrix?days=14")
    assert resp_utc.status_code == 200
    data_utc = resp_utc.json()

    # In UTC, Thursday 18:30 UTC is Thursday (day index 3) at hour 18!
    assert data_utc["matrix"][3][18] > 0.0


def test_circadian_matrix_api_with_invalid_timezone_fallback(client, db):
    """
    Verify that an invalid timezone string in UserSetting safely defaults to UTC
    without causing an HTTP 500 error.
    """
    setting = db.query(UserSetting).filter(UserSetting.user_id == TEST_USER_ID).first()
    if not setting:
        setting = UserSetting(user_id=TEST_USER_ID, tracks=[], daily_floor_target=2)
        db.add(setting)
    setting.timezone = "Corrupted/Bogus_Timezone_123"
    db.commit()

    resp = client.get("/reports/circadian-matrix?days=14")
    assert resp.status_code == 200
    data = resp.json()
    assert "matrix" in data
    assert len(data["matrix"]) == 7
    assert len(data["matrix"][0]) == 24
