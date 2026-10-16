import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.database import SessionLocal
from app.models import StudyTimetable, TimetableBlock
from app.routers.support import (
    STAGE_WINDOW_TIMETABLE_DECLARATION,
    _SYSTEM_PROMPT,
    _extract_timetable_proposal,
    execute_stage_window_timetable,
)


class MockUser:
    id = "usr_test_timetables_dynamic"
    email = "dynamic_tt@focusengine.internal"


@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    # Cleanup test records
    try:
        active_tt = db.query(StudyTimetable).filter(StudyTimetable.user_id == MockUser.id).all()
        for tt in active_tt:
            db.query(TimetableBlock).filter(TimetableBlock.timetable_id == tt.id).delete()
            db.delete(tt)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def test_tool_declaration_total_days():
    """Verify tool declaration specifies integer between 1 and 14 and explicit day extraction."""
    props = STAGE_WINDOW_TIMETABLE_DECLARATION["parameters"]["properties"]
    assert "total_days" in props
    total_days_desc = props["total_days"]["description"]
    assert "integer between 1 and 14" in total_days_desc
    assert "Do NOT default to 7" in total_days_desc


def test_system_prompt_dynamic_duration_guidance():
    """Verify system prompt instructs Gemini to extract target duration N (1-14)."""
    assert "target duration N (between 1 and 14)" in _SYSTEM_PROMPT
    assert "Pass total_days=N to `stage_window_timetable`" in _SYSTEM_PROMPT


def test_execute_stage_dynamic_duration_3_days(db_session):
    """Verify staging a 3-day timetable produces exactly 3 blocks and total_days=3."""
    user = MockUser()
    args = {
        "title": "3-Day Fast Sprint",
        "track_id": "coding",
        "total_days": 3,
        "blocks": [
            {
                "day_number": i,
                "days_from_today": i - 1,
                "window_start": "18:00",
                "window_deadline": "20:00",
                "duration_minutes": 90,
                "topic": f"Sprint Module {i}",
                "deliverable_spec": f"Artifact {i}",
                "verification_rules": "Test verification",
            }
            for i in range(1, 4)
        ],
    }

    result = execute_stage_window_timetable(db=db_session, current_user=user, args=args)
    assert result["type"] == "TIMETABLE_CARD"
    assert result["total_days"] == 3
    assert len(result["blocks"]) == 3

    # Verify database persistence
    tt = db_session.query(StudyTimetable).filter(StudyTimetable.id == result["timetable_id"]).first()
    assert tt is not None
    assert tt.total_days == 3
    assert len(tt.blocks) == 3


def test_execute_stage_dynamic_duration_10_days(db_session):
    """Verify staging a 10-day timetable produces exactly 10 blocks and total_days=10."""
    user = MockUser()
    args = {
        "title": "10-Day Deep Dive",
        "track_id": "theory",
        "total_days": 10,
        "blocks": [
            {
                "day_number": i,
                "days_from_today": i - 1,
                "window_start": "19:00",
                "window_deadline": "21:00",
                "duration_minutes": 120,
                "topic": f"Theory Chapter {i}",
                "deliverable_spec": f"Notes {i}",
                "verification_rules": "Viva audit",
            }
            for i in range(1, 11)
        ],
    }

    result = execute_stage_window_timetable(db=db_session, current_user=user, args=args)
    assert result["type"] == "TIMETABLE_CARD"
    assert result["total_days"] == 10
    assert len(result["blocks"]) == 10

    tt = db_session.query(StudyTimetable).filter(StudyTimetable.id == result["timetable_id"]).first()
    assert tt is not None
    assert tt.total_days == 10
    assert len(tt.blocks) == 10


def test_defensive_reconciliation_when_mismatched(db_session):
    """Verify handler enforces consistency when total_days != len(blocks)."""
    user = MockUser()
    # Mismatch: total_days says 7, but only 4 blocks are provided
    args = {
        "title": "Mismatched Schedule",
        "track_id": "coding",
        "total_days": 7,
        "blocks": [
            {
                "day_number": i,
                "days_from_today": i - 1,
                "window_start": "19:00",
                "window_deadline": "21:00",
                "duration_minutes": 100,
                "topic": f"Reconciliation Module {i}",
                "deliverable_spec": f"Artifact {i}",
                "verification_rules": "Oral defense",
            }
            for i in range(1, 5)
        ],
    }

    result = execute_stage_window_timetable(db=db_session, current_user=user, args=args)
    # The handler must reconcile total_days to match len(blocks) == 4
    assert result["total_days"] == 4
    assert len(result["blocks"]) == 4

    tt = db_session.query(StudyTimetable).filter(StudyTimetable.id == result["timetable_id"]).first()
    assert tt is not None
    assert tt.total_days == 4
    assert len(tt.blocks) == 4


def test_semantic_proposal_dynamic_duration():
    """Verify semantic proposal extractor parses dynamic duration correctly."""
    prop_3 = _extract_timetable_proposal("Please set up a 3-day study schedule for distributed systems")
    assert prop_3 is not None
    assert prop_3["total_days"] == 3
    assert len(prop_3["blocks"]) == 3

    prop_5 = _extract_timetable_proposal("Create a 5-day plan for OS concepts")
    assert prop_5 is not None
    assert prop_5["total_days"] == 5
    assert len(prop_5["blocks"]) == 5


def test_start_block_in_progress_with_active_session_idempotent(db_session):
    """Verify starting an IN_PROGRESS block reconnects to existing active session idempotently."""
    from datetime import date, datetime, time, timezone
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import get_current_user
    from app.models import Task, FocusSession

    app.dependency_overrides[get_current_user] = lambda: MockUser.id
    client = TestClient(app)

    try:
        tt = StudyTimetable(
            user_id=MockUser.id,
            title="Idempotent Test",
            track_id="coding",
            total_days=1,
            status="ACTIVE",
        )
        db_session.add(tt)
        db_session.flush()

        block = TimetableBlock(
            timetable_id=tt.id,
            day_number=1,
            target_date=date.today(),
            window_start=time(0, 0),
            window_deadline=time(23, 59),
            duration_minutes=60,
            topic="Idempotency Topic",
            deliverable_spec="Spec",
            verification_rules="Rules",
            status="IN_PROGRESS",
        )
        db_session.add(block)

        task = Task(
            user_id=MockUser.id,
            title="Idempotency Task",
            category="coding",
        )
        db_session.add(task)
        db_session.flush()

        session = FocusSession(
            user_id=MockUser.id,
            task_id=task.id,
            started_at=datetime.now(timezone.utc),
            ended_at=None,
        )
        db_session.add(session)
        db_session.commit()

        res = client.post(f"/api/timetables/blocks/{block.id}/start")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert data.get("session_id") == session.id
        assert data.get("reconnected") is True

    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_start_block_in_progress_without_active_session_recovers(db_session):
    """Verify starting an IN_PROGRESS block without active session allows restart recovery."""
    from datetime import date, time
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: MockUser.id
    client = TestClient(app)

    try:
        tt = StudyTimetable(
            user_id=MockUser.id,
            title="Recovery Test",
            track_id="coding",
            total_days=1,
            status="ACTIVE",
        )
        db_session.add(tt)
        db_session.flush()

        block = TimetableBlock(
            timetable_id=tt.id,
            day_number=1,
            target_date=date.today(),
            window_start=time(0, 0),
            window_deadline=time(23, 59),
            duration_minutes=60,
            topic="Recovery Topic",
            deliverable_spec="Spec",
            verification_rules="Rules",
            status="IN_PROGRESS",
        )
        db_session.add(block)
        db_session.commit()

        res = client.post(f"/api/timetables/blocks/{block.id}/start")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert data["block_id"] == block.id

    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_start_block_completed_fails_400(db_session):
    """Verify starting a COMPLETED block is rejected with 400."""
    from datetime import date, time
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: MockUser.id
    client = TestClient(app)

    try:
        tt = StudyTimetable(
            user_id=MockUser.id,
            title="Completed Test",
            track_id="coding",
            total_days=1,
            status="ACTIVE",
        )
        db_session.add(tt)
        db_session.flush()

        block = TimetableBlock(
            timetable_id=tt.id,
            day_number=1,
            target_date=date.today(),
            window_start=time(0, 0),
            window_deadline=time(23, 59),
            duration_minutes=60,
            topic="Completed Topic",
            deliverable_spec="Spec",
            verification_rules="Rules",
            status="COMPLETED",
        )
        db_session.add(block)
        db_session.commit()

        res = client.post(f"/api/timetables/blocks/{block.id}/start")
        assert res.status_code == 400
        assert "COMPLETED" in res.text

    finally:
        app.dependency_overrides.pop(get_current_user, None)
