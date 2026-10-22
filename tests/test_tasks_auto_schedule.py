import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user as auth_get_current_user
from app.database import SessionLocal
from app.main import app
from app.models import Task

TEST_USER_ID = "usr_test_auto_schedule"


@pytest.fixture
def client():
    app.dependency_overrides[auth_get_current_user] = lambda: CurrentUser(TEST_USER_ID)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(auth_get_current_user, None)


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
        db.query(Task).filter(Task.user_id == TEST_USER_ID).delete()
        db.commit()
    except Exception:
        db.rollback()


def test_auto_schedule_no_tasks(client):
    response = client.post("/api/tasks/auto-schedule", json={"available_minutes": 120})
    assert response.status_code == 404
    assert response.json()["detail"] == "No pending tasks available for scheduling."


def test_auto_schedule_greedy_priority_and_duration(client):
    db = SessionLocal()
    try:
        # Create tasks with varying priorities and durations
        t1 = Task(user_id=TEST_USER_ID, title="P2 - 30m", priority=2, estimated_duration_min=30)
        t2 = Task(user_id=TEST_USER_ID, title="P1 - 45m", priority=1, estimated_duration_min=45)
        t3 = Task(user_id=TEST_USER_ID, title="P1 - 15m", priority=1, estimated_duration_min=15)
        t4 = Task(user_id=TEST_USER_ID, title="P3 - 20m", priority=3, estimated_duration_min=20)
        t_completed = Task(user_id=TEST_USER_ID, title="P1 - Completed", priority=1, estimated_duration_min=10, is_completed=True)
        t_archived = Task(user_id=TEST_USER_ID, title="P1 - Archived", priority=1, estimated_duration_min=10, is_archived=True)
        db.add_all([t1, t2, t3, t4, t_completed, t_archived])
        db.commit()
    finally:
        db.close()

    # Available window: 65 minutes.
    # Sorted order of pending tasks:
    # 1. P1 - 15m (consumed 15, remaining 50)
    # 2. P1 - 45m (consumed 60, remaining 5)
    # 3. P2 - 30m (doesn't fit in 5 min)
    # Remaining time is 5 (< 5 triggers break? 5 - 0 < 5 is False, so evaluated. P2 doesn't fit.)
    # Result should include: t3, t2
    response = client.post("/api/tasks/auto-schedule", json={"available_minutes": 65})
    assert response.status_code == 200
    scheduled = response.json()
    assert len(scheduled) == 2
    assert scheduled[0]["title"] == "P1 - 15m"
    assert scheduled[0]["estimated_duration"] == 15
    assert scheduled[1]["title"] == "P1 - 45m"
    assert scheduled[1]["estimated_duration"] == 45


def test_auto_schedule_stop_when_less_than_five_minutes_remaining(client):
    db = SessionLocal()
    try:
        # t1 (P1, 57 min) is evaluated before t2 (P2, 2 min) due to priority
        t1 = Task(user_id=TEST_USER_ID, title="Task 1", priority=1, estimated_duration_min=57)
        t2 = Task(user_id=TEST_USER_ID, title="Task 2", priority=2, estimated_duration_min=2)
        db.add_all([t1, t2])
        db.commit()
    finally:
        db.close()

    # 60 available minutes. t1 takes 57 min. Remaining = 3 (< 5).
    # Loop breaks immediately before evaluating t2.
    response = client.post("/api/tasks/auto-schedule", json={"available_minutes": 60})
    assert response.status_code == 200
    scheduled = response.json()
    assert len(scheduled) == 1
    assert scheduled[0]["title"] == "Task 1"



def test_auto_schedule_validation_bounds(client):
    res_zero = client.post("/api/tasks/auto-schedule", json={"available_minutes": 0})
    assert res_zero.status_code == 422

    res_too_large = client.post("/api/tasks/auto-schedule", json={"available_minutes": 1441})
    assert res_too_large.status_code == 422
