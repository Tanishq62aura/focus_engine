"""
tests/test_offline_idempotency.py
Integration & Regression Tests for the Local-First Offline Synchronization
and Mutation Idempotency Subsystem.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app
from app.database import Base, SessionLocal, engine, init_db
from app.models import FocusSession, IdempotencyRecord, Task
from app.auth import get_current_user as auth_get_current_user
from app.routers.sessions import get_current_user as router_get_current_user

TEST_USER_ID = "test-offline-sync-user"


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    init_db()
    yield


@pytest.fixture
def db():
    session = SessionLocal(expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    app.dependency_overrides[auth_get_current_user] = lambda: TEST_USER_ID
    app.dependency_overrides[router_get_current_user] = lambda: TEST_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(auth_get_current_user, None)
    app.dependency_overrides.pop(router_get_current_user, None)


def create_test_session(db, user_id: str = TEST_USER_ID) -> FocusSession:
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        user_id=user_id,
        title="Offline Sync Task",
        category="coding",
        priority=3,
        estimated_duration_min=60,
        is_completed=False,
    )
    db.add(task)
    db.commit()

    sess_id = str(uuid.uuid4())
    session = FocusSession(
        id=sess_id,
        user_id=user_id,
        task_id=task_id,
        status="IN_PROGRESS",
        started_at=datetime.now(timezone.utc),
        ended_at=None,
        start_latency_min=0,
        actual_focused_seconds=0,
        verification_status="unverified",
        pasted_burst_detected=False,
        is_five_min_intervention=False,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# ===========================================================================
# 1. Replay Invariant Test
# ===========================================================================
def test_mutation_replay_invariant(client: TestClient, db):
    """
    Asserts that duplicate mutating requests bearing the same X-Idempotency-Key
    return cached responses with X-Cache-Lookup: HIT-IDEMPOTENT and do not
    re-execute business logic or cause double-closure errors.
    """
    session = create_test_session(db)
    idempotency_key = f"idemp-test-{uuid.uuid4()}"

    payload = {"notes": "Autonomous session end verified"}

    # 1. First execution (Cache Miss)
    resp1 = client.post(
        f"/sessions/{session.id}/end",
        json=payload,
        headers={"X-Idempotency-Key": idempotency_key},
    )
    assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
    assert "HIT-IDEMPOTENT" not in resp1.headers.get("X-Cache-Lookup", "")
    data1 = resp1.json()
    assert data1["id"] == session.id
    assert data1["ended_at"] is not None

    # Verify database persistence of the idempotency record
    record = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == idempotency_key).first()
    assert record is not None
    assert record.status_code == 200
    assert record.user_id == TEST_USER_ID

    # 2. Second execution with identical key (Cache Hit Replay)
    resp2 = client.post(
        f"/sessions/{session.id}/end",
        json=payload,
        headers={"X-Idempotency-Key": idempotency_key},
    )
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Cache-Lookup") == "HIT-IDEMPOTENT"
    data2 = resp2.json()

    # Exact payload equality (id, ended_at timestamp preserved without re-termination)
    assert data2 == data1
    assert data2["ended_at"] == data1["ended_at"]

    # Exactly one record must remain in the idempotency table
    count = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == idempotency_key).count()
    assert count == 1


# ===========================================================================
# 2. Missing Key Passthrough Test
# ===========================================================================
def test_missing_key_passthrough(client: TestClient, db):
    """
    Asserts that requests without X-Idempotency-Key proceed normally through
    the execution pipeline without creating any idempotency records.
    """
    session = create_test_session(db)

    initial_record_count = db.query(IdempotencyRecord).count()

    resp = client.post(
        f"/sessions/{session.id}/end",
        json={"notes": "Normal standard end without key"},
    )
    assert resp.status_code == 200
    assert "X-Cache-Lookup" not in resp.headers

    # No new record created
    new_record_count = db.query(IdempotencyRecord).count()
    assert new_record_count == initial_record_count


# ===========================================================================
# 3. Non-Mutating GET Passthrough Test
# ===========================================================================
def test_non_mutating_get_ignores_idempotency_key(client: TestClient, db):
    """
    Asserts that GET requests bearing X-Idempotency-Key are ignored by the
    middleware and are not recorded in the idempotency table.
    """
    key = f"get-idemp-{uuid.uuid4()}"

    resp = client.get("/auth/status", headers={"X-Idempotency-Key": key})
    assert resp.status_code == 200
    assert "X-Cache-Lookup" not in resp.headers

    record = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == key).first()
    assert record is None


# ===========================================================================
# 4. Error Status Caching (400 Bad Request Cached and Replayed)
# ===========================================================================
def test_client_error_400_cached_and_replayed(client: TestClient, db):
    """
    Asserts that client errors in (200, 201, 400, 409) such as deprecated direct
    block completions (400) are cached and replayed idempotently.
    """
    key = f"err-400-{uuid.uuid4()}"

    # POST to deprecated /blocks/{block_id}/complete returns 400 Bad Request
    resp1 = client.post(
        "/api/timetables/blocks/99999/complete",
        headers={"X-Idempotency-Key": key},
    )
    assert resp1.status_code == 400
    assert "HIT-IDEMPOTENT" not in resp1.headers.get("X-Cache-Lookup", "")

    # Second invocation should hit cache
    resp2 = client.post(
        "/api/timetables/blocks/99999/complete",
        headers={"X-Idempotency-Key": key},
    )
    assert resp2.status_code == 400
    assert resp2.headers.get("X-Cache-Lookup") == "HIT-IDEMPOTENT"
    assert resp2.json() == resp1.json()

    record = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == key).first()
    assert record is not None
    assert record.status_code == 400
