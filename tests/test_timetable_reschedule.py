import sys
from datetime import date, datetime, time, timedelta, timezone
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

TEST_USER_ID = "usr_test_reschedule"


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


def stage_3day_timetable(client: TestClient) -> dict:
    """Helper to stage a clean 3-day timetable via the API."""
    today = date.today()
    payload = {
        "title": "3-Day Reschedule Sprint",
        "track_id": "coding",
        "total_days": 3,
        "blocks": [
            {
                "day_number": i,
                "target_date": (today + timedelta(days=i - 1)).isoformat(),
                "window_start": "10:00:00",
                "window_deadline": "14:00:00",
                "duration_minutes": 60,
                "topic": f"Sprint Module {i}",
                "deliverable_spec": f"Artifact {i}",
                "verification_rules": f"Verify rules {i}",
                "resources": [f"https://docs.example.com/mod{i}"],
            }
            for i in range(1, 4)
        ],
    }
    res = client.post("/api/timetables/stage", json=payload)
    assert res.status_code == 201, f"Failed to stage timetable: {res.text}"
    return res.json()


def test_reschedule_shift_days_leaves_completed_blocks_untouched(client: TestClient, cleanup_database):
    """
    Scenario 1:
    - Stage a 3-day timetable.
    - Complete Day 1.
    - Call PATCH /api/timetables/active/reschedule with shift_days=1.
    - Assert Day 1 target_date and status == 'COMPLETED' remain unchanged.
    - Assert Day 2 and Day 3 target_date are incremented by 1 day.
    """
    staged = stage_3day_timetable(client)
    timetable_id = staged["id"]
    db = cleanup_database

    blocks = (
        db.query(TimetableBlock)
        .filter(TimetableBlock.timetable_id == timetable_id)
        .order_by(TimetableBlock.day_number)
        .all()
    )
    assert len(blocks) == 3

    orig_day1_date = blocks[0].target_date
    orig_day2_date = blocks[1].target_date
    orig_day3_date = blocks[2].target_date

    # Mark Day 1 as COMPLETED
    blocks[0].status = "COMPLETED"
    blocks[0].actual_completed_at = datetime.now(timezone.utc)
    db.commit()

    # Call reschedule with shift_days=1
    reschedule_payload = {
        "shift_days": 1,
        "reason": "Mid-sprint fatigue recovery",
        "block_adjustments": [],
    }
    res = client.patch("/api/timetables/active/reschedule", json=reschedule_payload)
    assert res.status_code == 200, f"Reschedule failed: {res.text}"
    data = res.json()

    # Verify Day 1 remains untouched
    b1 = next(b for b in data["blocks"] if b["day_number"] == 1)
    assert b1["status"] == "COMPLETED"
    assert b1["target_date"] == orig_day1_date.isoformat()

    # Verify Day 2 and Day 3 incremented by 1 day
    b2 = next(b for b in data["blocks"] if b["day_number"] == 2)
    assert b2["status"] == "PENDING"
    assert b2["target_date"] == (orig_day2_date + timedelta(days=1)).isoformat()

    b3 = next(b for b in data["blocks"] if b["day_number"] == 3)
    assert b3["status"] == "PENDING"
    assert b3["target_date"] == (orig_day3_date + timedelta(days=1)).isoformat()


def test_reschedule_block_adjustments_window_times(client: TestClient, cleanup_database):
    """
    Scenario 2:
    - Adjust window start and deadline on Day 2.
    - Verify new times persist.
    """
    staged = stage_3day_timetable(client)

    reschedule_payload = {
        "shift_days": 0,
        "reason": "Adjust evening window",
        "block_adjustments": [
            {
                "day_index": 2,
                "new_window_start": "15:00:00",
                "new_window_deadline": "18:00:00",
            }
        ],
    }
    res = client.patch("/api/timetables/active/reschedule", json=reschedule_payload)
    assert res.status_code == 200, f"Reschedule failed: {res.text}"
    data = res.json()

    b2 = next(b for b in data["blocks"] if b["day_number"] == 2)
    assert b2["window_start"].startswith("15:00")
    assert b2["window_deadline"].startswith("18:00")

    # Verify Day 1 and Day 3 remain at 10:00 - 14:00
    b1 = next(b for b in data["blocks"] if b["day_number"] == 1)
    assert b1["window_start"].startswith("10:00")
    assert b1["window_deadline"].startswith("14:00")


def test_reschedule_rejects_inverted_window(client: TestClient, cleanup_database):
    """Verify window_start >= window_deadline returns HTTP 400."""
    stage_3day_timetable(client)
    reschedule_payload = {
        "shift_days": 0,
        "block_adjustments": [
            {
                "day_index": 2,
                "new_window_start": "18:00:00",
                "new_window_deadline": "16:00:00",
            }
        ],
    }
    res = client.patch("/api/timetables/active/reschedule", json=reschedule_payload)
    assert res.status_code == 400
    assert "must be earlier than window_deadline" in res.json()["detail"]


def test_reschedule_no_active_timetable_404(client: TestClient, cleanup_database):
    """Verify 404 when user has no active timetable."""
    reschedule_payload = {"shift_days": 1}
    res = client.patch("/api/timetables/active/reschedule", json=reschedule_payload)
    assert res.status_code == 404
    assert "No active study timetable found" in res.json()["detail"]


def test_reschedule_via_support_chat_command(client: TestClient, cleanup_database):
    """Verify support chat handles timetable rescheduling intents."""
    staged = stage_3day_timetable(client)
    timetable_id = staged["id"]
    db = cleanup_database

    blocks = (
        db.query(TimetableBlock)
        .filter(TimetableBlock.timetable_id == timetable_id)
        .order_by(TimetableBlock.day_number)
        .all()
    )
    orig_day1_date = blocks[0].target_date
    orig_day2_date = blocks[1].target_date

    # Mark Day 1 COMPLETED
    blocks[0].status = "COMPLETED"
    db.commit()

    chat_payload = {"message": "Please reschedule my active timetable: delay by 1 day"}
    res = client.post("/api/support/chat", json=chat_payload)
    assert res.status_code == 200
    resp_data = res.json()
    assert resp_data["action"] == "TIMETABLE_RESCHEDULED"
    assert resp_data["data"]["is_rescheduled"] is True

    # Check updated database state
    db.expire_all()
    updated_blocks = (
        db.query(TimetableBlock)
        .filter(TimetableBlock.timetable_id == timetable_id)
        .order_by(TimetableBlock.day_number)
        .all()
    )
    assert updated_blocks[0].target_date == orig_day1_date
    assert updated_blocks[0].status == "COMPLETED"
    assert updated_blocks[1].target_date == orig_day2_date + timedelta(days=1)
