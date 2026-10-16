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

TEST_USER_ID = "usr_test_idempotency"


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
        "title": "3-Day Idempotency Sprint",
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
            }
            for i in range(1, 4)
        ],
    }
    res = client.post("/api/timetables/stage", json=payload)
    assert res.status_code == 201, f"Failed to stage timetable: {res.text}"
    return res.json()


def test_block_start_when_pending(client: TestClient, cleanup_database):
    """Scenario 1: Starting a PENDING block transitions status to IN_PROGRESS with HTTP 200."""
    staged = stage_3day_timetable(client)
    assert len(staged["blocks"]) == 3
    first_block = staged["blocks"][0]
    block_id = first_block["id"]

    # Verify initial block status is PENDING
    block_in_db = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == block_id).first()
    assert block_in_db is not None
    assert block_in_db.status == "PENDING"

    # Start block
    res = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"

    data = res.json()
    assert data["status"] == "started"
    assert data["block_id"] == block_id

    # Verify transition in DB
    cleanup_database.refresh(block_in_db)
    assert block_in_db.status == "IN_PROGRESS"
    assert block_in_db.actual_started_at is not None


def test_block_start_when_already_in_progress(client: TestClient, cleanup_database):
    """Scenario 2: Starting an IN_PROGRESS block is idempotent, returning 200 OK without crashing."""
    staged = stage_3day_timetable(client)
    block_id = staged["blocks"][0]["id"]

    # First start: transitions block to IN_PROGRESS
    res1 = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert res1.status_code == 200

    # Second immediate start: should be idempotent and return 200 OK (NOT 400 Bad Request)
    res2 = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert res2.status_code == 200, f"Expected 200 OK on duplicate start, got {res2.status_code}: {res2.text}"

    data2 = res2.json()
    assert data2["status"] == "started"
    assert data2["block_id"] == block_id

    # Also verify idempotent reconnection when an active FocusSession exists
    task = Task(user_id=TEST_USER_ID, title="Idempotency Task", category="coding")
    cleanup_database.add(task)
    cleanup_database.flush()

    active_session = FocusSession(
        user_id=TEST_USER_ID,
        task_id=task.id,
        started_at=datetime.now(timezone.utc),
        ended_at=None,
    )
    cleanup_database.add(active_session)
    cleanup_database.commit()

    res3 = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["status"] == "started"
    assert data3.get("session_id") == active_session.id
    assert data3.get("reconnected") is True


def test_block_start_fails_only_when_completed(client: TestClient, cleanup_database):
    """Scenario 3: Starting a COMPLETED block must be rejected with HTTP 400 Bad Request."""
    staged = stage_3day_timetable(client)
    block_id = staged["blocks"][0]["id"]

    # Mark block as COMPLETED in DB
    block_in_db = cleanup_database.query(TimetableBlock).filter(TimetableBlock.id == block_id).first()
    assert block_in_db is not None
    block_in_db.status = "COMPLETED"
    block_in_db.actual_completed_at = datetime.now(timezone.utc)
    cleanup_database.commit()

    # Attempt to start the COMPLETED block
    res = client.post(f"/api/timetables/blocks/{block_id}/start")
    assert res.status_code == 400, f"Expected 400 Bad Request, got {res.status_code}: {res.text}"
    assert "COMPLETED" in res.text
