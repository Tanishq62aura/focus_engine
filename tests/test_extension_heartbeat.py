import sys
from datetime import datetime
from pathlib import Path
import uuid
import pytest
from fastapi import HTTPException

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, engine, Base, init_db
from app.models import FocusSession, SessionHeartbeat
from app.routers.telemetry import record_heartbeat, HeartbeatPayload


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    init_db()
    yield


def test_heartbeat_lifecycle_and_auto_abort(setup_db):
    db = SessionLocal()
    test_user = "dev-user"
    session_id = str(uuid.uuid4())

    try:
        # 1. Arrange: Create active FocusSession
        sess = FocusSession(
            id=session_id,
            user_id=test_user,
            status="IN_PROGRESS",
            created_at=datetime.utcnow(),
            heartbeat_violation_count=0
        )
        db.add(sess)
        db.commit()

        # 2. On-task VS Code heartbeat -> OK
        payload_ok = HeartbeatPayload(
            session_id=session_id,
            source="vscode",
            event_type="keystroke",
            active_target="src/main.rs",
            is_idle=False
        )
        res_ok = record_heartbeat(payload_ok, db=db, current_user=test_user)
        assert res_ok.status == "OK"
        assert res_ok.violation_count == 0

        # 3. First off-task Chrome heartbeat -> WARNING (violation 1)
        payload_bad_1 = HeartbeatPayload(
            session_id=session_id,
            source="chrome",
            event_type="tab_active",
            active_target="https://x.com/home",
            is_idle=False
        )
        res_bad_1 = record_heartbeat(payload_bad_1, db=db, current_user=test_user)
        assert res_bad_1.status == "WARNING"
        assert res_bad_1.violation_count == 1

        # 4. Second off-task VS Code heartbeat (blur) -> WARNING (violation 2)
        payload_bad_2 = HeartbeatPayload(
            session_id=session_id,
            source="vscode",
            event_type="focus_lost",
            is_idle=True
        )
        res_bad_2 = record_heartbeat(payload_bad_2, db=db, current_user=test_user)
        assert res_bad_2.status == "WARNING"
        assert res_bad_2.violation_count == 2

        # 5. Third off-task Chrome heartbeat -> SESSION_TERMINATED (violation 3)
        payload_bad_3 = HeartbeatPayload(
            session_id=session_id,
            source="chrome",
            event_type="tab_active",
            active_target="https://reddit.com/r/all",
            is_idle=False
        )
        res_bad_3 = record_heartbeat(payload_bad_3, db=db, current_user=test_user)
        assert res_bad_3.status == "SESSION_TERMINATED"
        assert res_bad_3.violation_count == 3

        # 6. Database Verification: Session is terminated & ended_at is set
        db.refresh(sess)
        assert sess.status == "ABORTED"
        assert sess.ended_at is not None
        assert "Auto-Aborted" in (sess.notes or "")

        # 7. Post-termination heartbeat must return 404
        with pytest.raises(HTTPException) as exc_info:
            record_heartbeat(payload_ok, db=db, current_user=test_user)
        assert exc_info.value.status_code == 404

    finally:
        # Cleanup
        try:
            db.rollback()
            db.query(SessionHeartbeat).filter(SessionHeartbeat.session_id == session_id).delete()
            db.query(FocusSession).filter(FocusSession.id == session_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
