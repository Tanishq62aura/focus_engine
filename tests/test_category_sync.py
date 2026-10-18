import sys
from datetime import date, timedelta
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
from app.routers.support import execute_manage_task_categories, execute_stage_window_timetable

TEST_USER_ID = "usr_test_category_sync"


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


def test_manage_task_categories_create_and_archive(client: TestClient, cleanup_database):
    db = cleanup_database

    # 1. Create categories via execute_manage_task_categories
    create_args = {
        "action": "create",
        "categories": [
            {
                "name": "Dynamic Programming P1",
                "target_minutes": 50,
                "priority": "P1",
            },
            {
                "name": "System Design P2",
                "target_minutes": 45,
                "priority": "P2",
            },
        ],
    }
    create_result = execute_manage_task_categories(db, TEST_USER_ID, create_args)
    assert create_result["action"] == "create"
    assert len(create_result["categories"]) == 2

    # Verify tasks exist in DB
    dp_task = db.query(Task).filter(Task.user_id == TEST_USER_ID, Task.title == "Dynamic Programming P1").first()
    assert dp_task is not None
    assert dp_task.priority == 1
    assert dp_task.estimated_duration_min == 50
    assert dp_task.is_archived is False

    sd_task = db.query(Task).filter(Task.user_id == TEST_USER_ID, Task.title == "System Design P2").first()
    assert sd_task is not None
    assert sd_task.priority == 2
    assert sd_task.estimated_duration_min == 45
    assert sd_task.is_archived is False

    # 2. Update category
    update_args = {
        "action": "update",
        "categories": [
            {
                "name": "Dynamic Programming P1",
                "target_minutes": 60,
                "priority": "P2",
            }
        ],
    }
    update_result = execute_manage_task_categories(db, TEST_USER_ID, update_args)
    assert update_result["action"] == "update"
    db.refresh(dp_task)
    assert dp_task.estimated_duration_min == 60
    assert dp_task.priority == 2

    # 3. Archive category
    archive_args = {
        "action": "archive",
        "categories": [
            {
                "name": "Dynamic Programming P1",
            }
        ],
    }
    archive_result = execute_manage_task_categories(db, TEST_USER_ID, archive_args)
    assert archive_result["action"] == "archive"
    db.refresh(dp_task)
    assert dp_task.is_archived is True

    # 4. List categories
    list_result = execute_manage_task_categories(db, TEST_USER_ID, {"action": "list"})
    assert list_result["action"] == "list"
    # System design is active, DP is archived
    active_titles = [c["name"] for c in list_result["categories"]]
    assert "System Design P2" in active_titles
    assert "Dynamic Programming P1" not in active_titles


def test_conversational_task_management_endpoint(client: TestClient, cleanup_database):
    db = cleanup_database

    # Test conversational endpoint with natural language
    res = client.post(
        "/api/support/chat",
        json={"message": "create task category Graph Theory P1 45m"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "MANAGE_TASK_CATEGORIES"

    # Confirm task created
    task = db.query(Task).filter(Task.user_id == TEST_USER_ID, Task.title.ilike("%Graph Theory%")).first()
    assert task is not None
    assert task.is_archived is False

    # Conversational archive
    res2 = client.post(
        "/api/support/chat",
        json={"message": f"archive task category {task.title}"},
    )
    assert res2.status_code == 200
    db.refresh(task)
    assert task.is_archived is True


def test_stage_window_timetable_creates_linked_tasks(client: TestClient, cleanup_database):
    db = cleanup_database
    today = date.today()

    # Stage a 2-day timetable with distinct topics
    args = {
        "title": "Machine Learning Foundations",
        "track_id": "coding",
        "total_days": 2,
        "blocks": [
            {
                "day_number": 1,
                "target_date": today.isoformat(),
                "window_start": "09:00:00",
                "window_deadline": "12:00:00",
                "duration_minutes": 45,
                "topic": "Gradient Descent Optimization",
                "deliverable_spec": "Implement mini-batch SGD notebook",
                "verification_rules": "Derive backpropagation equations orally",
            },
            {
                "day_number": 2,
                "target_date": (today + timedelta(days=1)).isoformat(),
                "window_start": "14:00:00",
                "window_deadline": "17:00:00",
                "duration_minutes": 60,
                "topic": "Loss Functions & Regularization",
                "deliverable_spec": "Implement L1/L2 regularization module",
                "verification_rules": "Explain Frobenius norm orally",
            },
        ],
    }

    card_payload = execute_stage_window_timetable(db, TEST_USER_ID, args)
    assert card_payload["title"] == "Machine Learning Foundations"
    assert len(card_payload["blocks"]) == 2

    # Verify tasks were created for both topics
    t1 = db.query(Task).filter(Task.user_id == TEST_USER_ID, Task.title == "Gradient Descent Optimization").first()
    t2 = db.query(Task).filter(Task.user_id == TEST_USER_ID, Task.title == "Loss Functions & Regularization").first()

    assert t1 is not None, "Task for Day 1 topic should be automatically generated"
    assert t2 is not None, "Task for Day 2 topic should be automatically generated"
    assert t1.is_archived is False
    assert t2.is_archived is False

    # Verify timetable blocks have task_id linked
    blocks = card_payload["blocks"]
    b1_task_id = blocks[0].get("task_id")
    b2_task_id = blocks[1].get("task_id")

    assert str(b1_task_id) == str(t1.id), "Block 1 task_id must match Task 1 ID"
    assert str(b2_task_id) == str(t2.id), "Block 2 task_id must match Task 2 ID"

    # Verify in DB table directly
    db_b1 = db.query(TimetableBlock).filter(TimetableBlock.id == blocks[0]["id"]).first()
    assert str(db_b1.task_id) == str(t1.id)

    # Now verify starting the block session preserves and returns task_id
    res = client.post(f"/api/timetables/blocks/{blocks[0]['id']}/start")
    assert res.status_code == 200
    start_data = res.json()
    assert str(start_data.get("task_id")) == str(t1.id)
