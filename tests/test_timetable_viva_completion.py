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
from app.models import FocusSession, StudyTimetable, Task, TimetableBlock
from app.routers.auth import get_current_user as router_get_current_user

TEST_USER_ID = "usr_test_viva_completion"


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
        db.commit()
    except Exception:
        db.rollback()


def stage_2day_timetable(client: TestClient) -> dict:
    """Stages a clean 2-day study timetable for testing."""
    today = date.today()
    payload = {
        "title": "2-Day Viva Defense Test Schedule",
        "track_id": "coding",
        "total_days": 2,
        "blocks": [
            {
                "day_number": 1,
                "target_date": today.isoformat(),
                "window_start": "10:00:00",
                "window_deadline": "14:00:00",
                "duration_minutes": 60,
                "topic": "Day 1 Concurrency & Channels",
                "deliverable_spec": "Working threadpool demo",
                "verification_rules": "Oral viva question on deadlock conditions",
            },
            {
                "day_number": 2,
                "target_date": (today + timedelta(days=1)).isoformat(),
                "window_start": "10:00:00",
                "window_deadline": "14:00:00",
                "duration_minutes": 60,
                "topic": "Day 2 Distributed Log Storage",
                "deliverable_spec": "Append-only segment indexer",
                "verification_rules": "Oral viva on compaction mechanics",
            },
        ],
    }
    res = client.post("/api/timetables/stage", json=payload)
    assert res.status_code == 201, f"Failed to stage timetable: {res.text}"
    return res.json()


def test_timetable_block_viva_completion_and_hud_progression(client: TestClient, cleanup_database):
    """
    Integration Test:
    1. Stage a 2-day timetable.
    2. Start block 1 (POST /api/timetables/blocks/{id}/start).
    3. Submit successful viva completion.
    4. Assert block 1 status is 'COMPLETED'.
    5. Assert next call to GET /api/timetables/active targets block 2.
    """
    # 1. Stage 2-day timetable
    staged = stage_2day_timetable(client)
    assert len(staged["blocks"]) == 2
    b1_id = staged["blocks"][0]["id"]
    b2_id = staged["blocks"][1]["id"]
    tt_id = staged["id"]

    # 2. Start block 1
    start_res = client.post(f"/api/timetables/blocks/{b1_id}/start")
    assert start_res.status_code == 200
    start_data = start_res.json()
    assert start_data["status"] == "started"
    assert start_data["block_id"] == b1_id
    session_id = start_data.get("session_id")
    assert session_id is not None

    # Assert block 1 is IN_PROGRESS
    cleanup_database.expire_all()
    b1_db = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == b1_id).first()
    assert b1_db.status == "IN_PROGRESS"

    # 3. Submit successful viva completion via /api/support/verify-viva
    viva_res = client.post(
        "/api/support/verify-viva",
        json={
            "session_id": session_id,
            "timetable_block_id": b1_id,
            "answer": "Deadlocks require mutual exclusion, hold and wait, no preemption, and circular wait.",
            "passed": True,
        },
    )
    assert viva_res.status_code == 200
    viva_data = viva_res.json()
    assert viva_data["verified"] is True
    assert viva_data["block_status"] == "COMPLETED"

    # 4. Assert block 1 status is 'COMPLETED' in DB
    cleanup_database.expire_all()
    b1_db_after = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == b1_id).first()
    assert b1_db_after.status == "COMPLETED"
    assert b1_db_after.actual_completed_at is not None

    # 5. Assert next call to GET /api/timetables/active targets block 2
    active_res = client.get("/api/timetables/active")
    assert active_res.status_code == 200
    active_data = active_res.json()

    # Verify active target block points to block 2
    assert active_data["active_target_block"] is not None
    assert active_data["active_target_block"]["id"] == b2_id
    assert active_data["active_target_block"]["day_number"] == 2
    assert active_data["active_target_block"]["status"] == "PENDING"

    # Verify first non-completed block in blocks list is block 2
    non_completed = [b for b in active_data["blocks"] if b["status"] != "COMPLETED"]
    assert len(non_completed) == 1
    assert non_completed[0]["id"] == b2_id
    assert non_completed[0]["day_number"] == 2


def test_timetable_all_blocks_completion_marks_timetable_completed(client: TestClient, cleanup_database):
    """Verify that completing all blocks in a timetable transitions StudyTimetable status to 'COMPLETED'."""
    staged = stage_2day_timetable(client)
    b1_id = staged["blocks"][0]["id"]
    b2_id = staged["blocks"][1]["id"]
    tt_id = staged["id"]

    # Start and complete block 1
    client.post(f"/api/timetables/blocks/{b1_id}/start")
    client.post("/api/support/verify-viva", json={"timetable_block_id": b1_id, "passed": True})

    # Start and complete block 2
    client.post(f"/api/timetables/blocks/{b2_id}/start")
    viva2 = client.post("/api/support/verify-viva", json={"timetable_block_id": b2_id, "passed": True})
    assert viva2.status_code == 200

    cleanup_database.expire_all()
    tt = cleanup_database.query(StudyTimetable).filter(StudyTimetable.id == tt_id).first()
    assert tt.status == "COMPLETED"


def test_timetable_failed_defense_marks_block_missed(client: TestClient, cleanup_database):
    """Verify that a rejected viva defense marks the block as MISSED."""
    staged = stage_2day_timetable(client)
    b1_id = staged["blocks"][0]["id"]

    client.post(f"/api/timetables/blocks/{b1_id}/start")
    fail_res = client.post("/api/support/verify-viva", json={"timetable_block_id": b1_id, "passed": False})
    assert fail_res.status_code == 200

    cleanup_database.expire_all()
    b1 = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == b1_id).first()
    assert b1.status == "MISSED"
