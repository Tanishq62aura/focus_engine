import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user as auth_get_current_user
from app.database import SessionLocal
from app.main import app
from app.models import FocusSession, StudyTimetable, Task, TimetableBlock, UserSetting
from app.routers.auth import get_current_user as router_get_current_user

TEST_USER_ID = "usr_test_reporting_sync"


@pytest.fixture
def client():
    app.dependency_overrides[auth_get_current_user] = lambda: TEST_USER_ID
    app.dependency_overrides[router_get_current_user] = lambda: TEST_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(auth_get_current_user, None)
    app.dependency_overrides.pop(router_get_current_user, None)


@pytest.fixture(autouse=True)
def cleanup_database():
    db = SessionLocal()
    try:
        _cleanup_user_records(db)
        yield db
    finally:
        _cleanup_user_records(db)
        db.close()


def _cleanup_user_records(db):
    try:
        tts = db.query(StudyTimetable).filter(StudyTimetable.user_id == TEST_USER_ID).all()
        for tt in tts:
            db.query(TimetableBlock).filter(TimetableBlock.timetable_id == tt.id).delete()
            db.delete(tt)
        sessions = db.query(FocusSession).filter(FocusSession.user_id == TEST_USER_ID).all()
        for s in sessions:
            db.delete(s)
        tasks = db.query(Task).filter(Task.user_id == TEST_USER_ID).all()
        for t in tasks:
            db.delete(t)
        settings = db.query(UserSetting).filter(UserSetting.user_id == TEST_USER_ID).all()
        for st in settings:
            db.delete(st)
        db.commit()
    except Exception:
        db.rollback()


def test_early_completed_block_anchors_to_today(client: TestClient, cleanup_database):
    """
    Regression Test:
    1. Create a timetable block with target_date = tomorrow.
    2. Start block and complete/verify it today via viva defense.
    3. Assert GET /api/reports/daily-scorecard?date=today includes completed_blocks == 1.
    4. Assert streak metrics register the completed block today (today_verified_blocks == 1).
    5. Assert scorecard for tomorrow shows completed_blocks == 0.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # 1. Stage timetable with block targeting tomorrow
    stage_payload = {
        "title": "Advance Execution Test Plan",
        "track_id": "coding",
        "total_days": 1,
        "blocks": [
            {
                "day_number": 1,
                "target_date": tomorrow.isoformat(),
                "window_start": "09:00:00",
                "window_deadline": "12:00:00",
                "duration_minutes": 60,
                "topic": "Early Lock-Free Queue Delivery",
                "deliverable_spec": "CAS-based queue benchmark",
                "verification_rules": "Oral viva on memory barriers",
            }
        ],
    }
    stage_res = client.post("/api/timetables/stage", json=stage_payload)
    assert stage_res.status_code == 201, f"Failed staging: {stage_res.text}"
    staged = stage_res.json()
    block_id = staged["blocks"][0]["id"]

    # 2. Start block today (creates/links active FocusSession)
    start_res = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert start_res.status_code == 200, f"Failed starting block: {start_res.text}"
    session_id = start_res.json().get("session_id")
    assert session_id is not None

    # Complete and verify it today via oral viva defense
    viva_res = client.post(
        "/api/support/verify-viva",
        json={
            "session_id": session_id,
            "timetable_block_id": block_id,
            "answer": "Memory barriers prevent reordering of reads and writes across synchronization boundaries.",
            "passed": True,
        },
    )
    assert viva_res.status_code == 200, f"Viva verify failed: {viva_res.text}"
    viva_data = viva_res.json()
    assert viva_data["verified"] is True
    assert viva_data["block_status"] == "COMPLETED"

    # 3. Assert GET /api/reports/daily-scorecard?date=today includes completed_blocks == 1
    scorecard_res = client.get("/api/reports/daily-scorecard?date=today")
    assert scorecard_res.status_code == 200, f"Scorecard failed: {scorecard_res.text}"
    scorecard = scorecard_res.json()
    assert scorecard["completed_blocks"] == 1, f"Expected completed_blocks == 1, got {scorecard['completed_blocks']}"
    assert scorecard["verified_minutes"] >= 1, f"Expected verified_minutes >= 1, got {scorecard['verified_minutes']}"

    # Verify querying by explicit ISO date string also returns completed_blocks == 1
    iso_scorecard_res = client.get(f"/api/reports/daily-scorecard?date={today.isoformat()}")
    assert iso_scorecard_res.status_code == 200
    assert iso_scorecard_res.json()["completed_blocks"] == 1

    # 4. Assert streak metrics register the completed block today
    streak_res = client.get("/api/reports/streak")
    assert streak_res.status_code == 200, f"Streak endpoint failed: {streak_res.text}"
    streak_data = streak_res.json()
    assert streak_data["today_verified_blocks"] == 1, f"Expected today_verified_blocks == 1, got {streak_data['today_verified_blocks']}"
    assert streak_data["last_active_date"] == today.isoformat()

    # 5. Assert scorecard for tomorrow does NOT count this block
    tomorrow_scorecard_res = client.get(f"/api/reports/daily-scorecard?date={tomorrow.isoformat()}")
    assert tomorrow_scorecard_res.status_code == 200
    assert tomorrow_scorecard_res.json()["completed_blocks"] == 0


def test_atomic_session_to_block_viva_sync(client: TestClient, cleanup_database):
    """
    Verify atomic synchronization between FocusSession and TimetableBlock:
    - session.is_verified = True
    - session.status = 'COMPLETED'
    - session.end_time = utcnow
    - session.duration_minutes >= 1
    - block.status = 'COMPLETED'
    - block.actual_completed_at = utcnow
    - Both committed in a single database transaction
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)

    stage_res = client.post(
        "/api/timetables/stage",
        json={
            "title": "Atomic Viva Sync Test",
            "track_id": "coding",
            "total_days": 1,
            "blocks": [
                {
                    "day_number": 1,
                    "target_date": tomorrow.isoformat(),
                    "window_start": "10:00:00",
                    "window_deadline": "13:00:00",
                    "duration_minutes": 90,
                    "topic": "Atomic Sync Verification",
                    "deliverable_spec": "Write-ahead log atomicity",
                    "verification_rules": "Two-phase commit defense",
                }
            ],
        },
    )
    assert stage_res.status_code == 201
    block_id = stage_res.json()["blocks"][0]["id"]

    start_res = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert start_res.status_code == 200
    session_id = start_res.json()["session_id"]

    # Verify via /api/support/verify-viva
    viva_res = client.post(
        "/api/support/verify-viva",
        json={"timetable_block_id": block_id, "passed": True},
    )
    assert viva_res.status_code == 200

    cleanup_database.expire_all()
    sess = cleanup_database.query(FocusSession).filter(FocusSession.id == session_id).first()
    blk = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == block_id).first()

    assert sess.is_verified is True
    assert sess.verification_status == "verified"
    assert sess.status == "COMPLETED"
    assert sess.ended_at is not None
    assert sess.end_time is not None
    assert sess.duration_minutes >= 1

    assert blk.status == "COMPLETED"
    assert blk.actual_completed_at is not None
    assert blk.actual_completed_at.date() == today


def test_block_complete_endpoint_atomic_sync(client: TestClient, cleanup_database):
    """
    Verify POST /api/timetables/blocks/{block_id}/complete atomically syncs session and block.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)

    stage_res = client.post(
        "/api/timetables/stage",
        json={
            "title": "Direct Block Completion Test",
            "track_id": "coding",
            "total_days": 1,
            "blocks": [
                {
                    "day_number": 1,
                    "target_date": tomorrow.isoformat(),
                    "window_start": "14:00:00",
                    "window_deadline": "18:00:00",
                    "duration_minutes": 45,
                    "topic": "Direct Block Completion Endpoint",
                    "deliverable_spec": "Endpoint test spec",
                    "verification_rules": "Automated endpoint audit",
                }
            ],
        },
    )
    assert stage_res.status_code == 201
    block_id = stage_res.json()["blocks"][0]["id"]

    start_res = client.post(f"/api/timetables/blocks/{block_id}/start")
    session_id = start_res.json()["session_id"]

    complete_res = client.post(f"/api/timetables/blocks/{block_id}/complete", json={"passed": True})
    assert complete_res.status_code == 200
    comp_data = complete_res.json()
    assert comp_data["status"] == "COMPLETED"
    assert comp_data["verified"] is True

    # Assert scorecard reflects the completed block on date=today
    sc_res = client.get("/api/reports/daily-scorecard?date=today")
    assert sc_res.status_code == 200
    assert sc_res.json()["completed_blocks"] == 1
