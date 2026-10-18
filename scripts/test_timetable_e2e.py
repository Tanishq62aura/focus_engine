"""
scripts/test_timetable_e2e.py
Headless end-to-end execution script verifying Day 2 timetable viva completion,
database invariants, and scorecard/streak telemetry.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.auth import get_current_user as auth_get_current_user
from app.database import SessionLocal
from app.main import app
from app.models import FocusSession, StudyTimetable, TimetableBlock
from app.routers.auth import get_current_user as router_get_current_user
from app.routers.reports import calculate_daily_scorecard, get_streak_report
from tests.test_timetable_day2_lifecycle import ensure_day2_preconditions, TARGET_USER_ID, TARGET_SESSION_ID


def run_e2e():
    print("=" * 70)
    print(">> FOCUS ENGINE: HEADLESS E2E DAY 2 LIFECYCLE REGRESSION")
    print("=" * 70)

    app.dependency_overrides[auth_get_current_user] = lambda: TARGET_USER_ID
    app.dependency_overrides[router_get_current_user] = lambda: TARGET_USER_ID

    client = TestClient(app)
    db = SessionLocal()

    try:
        # Step 1: Precondition audit
        print("\n[Step 1] Auditing preconditions...")
        tt, day2_block, session = ensure_day2_preconditions(db, client)
        print(f"  * Timetable ID: {tt.id} ('{tt.title}') - Status: {tt.status}")
        print(f"  * Day 2 Block ID: {day2_block.id} ('{day2_block.topic}') - Status: {day2_block.status}")
        print(f"  * Active Session ID: {session.id} - Status: {session.status}")

        assert day2_block.day_number == 2
        assert "Data Structures & Algorithmic Mechanics" in day2_block.topic
        assert session.user_id == TARGET_USER_ID
        assert session.status == "IN_PROGRESS"
        assert day2_block.status == "IN_PROGRESS"

        # Step 2: Submit passed viva defense
        print("\n[Step 2] Simulating passed oral viva defense...")
        viva_payload = {
            "session_id": session.id,
            "timetable_block_id": day2_block.id,
            "answer": "Cache line false sharing occurs when distinct CPU cores modify independent variables on the same 64-byte boundary.",
            "passed": True,
        }
        res = client.post("/api/support/verify-viva", json=viva_payload)
        assert res.status_code == 200, f"Viva submission failed: {res.text}"
        data = res.json()
        print(f"  * Response: verified={data.get('verified')}, block_status={data.get('block_status')}")

        # Step 3: Database Commit & Lifecycle Invariants
        print("\n[Step 3] Verifying database commit & lifecycle invariants...")
        db.expire_all()
        updated_block = db.query(TimetableBlock).filter(TimetableBlock.id == day2_block.id).first()
        updated_tt = db.query(StudyTimetable).filter(StudyTimetable.id == tt.id).first()
        updated_session = db.query(FocusSession).filter(FocusSession.id == session.id).first()

        print(f"  * StudyTimetableBlock.status: {updated_block.status}")
        assert updated_block.status == "COMPLETED"

        print(f"  * StudyTimetableBlock.actual_completed_at: {updated_block.actual_completed_at}")
        assert updated_block.actual_completed_at is not None

        print(f"  * StudyTimetable.status (all blocks done): {updated_tt.status}")
        assert updated_tt.status == "COMPLETED"

        print(f"  * StudySession.status: {updated_session.status}, is_verified: {updated_session.is_verified}, verified_minutes: {updated_session.verified_minutes}")
        assert updated_session.status == "COMPLETED"
        assert updated_session.is_verified is True
        assert updated_session.verified_minutes > 0

        orphan_active = (
            db.query(FocusSession)
            .filter(FocusSession.user_id == TARGET_USER_ID, FocusSession.ended_at.is_(None))
            .all()
        )
        print(f"  * Orphan active sessions for {TARGET_USER_ID}: {len(orphan_active)}")
        assert len(orphan_active) == 0

        # Step 4: Scorecard & Streak Telemetry
        print("\n[Step 4] Validating scorecard & streak telemetry...")
        sc_res = client.get("/api/reports/daily-scorecard?date=today")
        assert sc_res.status_code == 200
        scorecard = sc_res.json()
        print(f"  * Daily Scorecard: completed_blocks={scorecard['completed_blocks']}, verified_minutes={scorecard['verified_minutes']}, score={scorecard['score']}")
        assert scorecard["completed_blocks"] >= 2
        assert scorecard["verified_minutes"] >= 60.0

        today_date = datetime.utcnow().date()
        direct_sc = calculate_daily_scorecard(db, TARGET_USER_ID, today_date)
        assert direct_sc["completed_blocks"] >= 2
        assert direct_sc["verified_minutes"] >= 60.0

        st_res = client.get("/api/reports/streak")
        assert st_res.status_code == 200
        streak = st_res.json()
        print(f"  * Streak Report: today_verified_blocks={streak['today_verified_blocks']}, floor_met_today={streak['floor_met_today']}, streak_days={streak['current_streak_days']}")
        assert streak["today_verified_blocks"] >= 2
        assert streak["floor_met_today"] is True
        assert streak["current_streak_days"] >= 1

        direct_streak = get_streak_report(db, TARGET_USER_ID)
        assert direct_streak.today_verified_blocks >= 2
        assert direct_streak.floor_met_today is True

        print("\n" + "=" * 70)
        print(">>> ALL INVARIANTS AND TELEMETRY VERIFIED SUCCESSFULLY!")
        print("=" * 70)
        return True

    finally:
        app.dependency_overrides.pop(auth_get_current_user, None)
        app.dependency_overrides.pop(router_get_current_user, None)
        db.close()


if __name__ == "__main__":
    success = run_e2e()
    sys.exit(0 if success else 1)
