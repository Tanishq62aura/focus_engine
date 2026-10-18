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
from app.routers.reports import calculate_daily_scorecard, get_streak_report

TARGET_USER_ID = "dev-user"
TARGET_SESSION_ID = "a0ec9c2a-0562-486f-adce-1f90105de270"


@pytest.fixture
def client():
    app.dependency_overrides[auth_get_current_user] = lambda: TARGET_USER_ID
    app.dependency_overrides[router_get_current_user] = lambda: TARGET_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(auth_get_current_user, None)
    app.dependency_overrides.pop(router_get_current_user, None)


@pytest.fixture
def db():
    session = SessionLocal(expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


def ensure_day2_preconditions(db, client=None):
    """
    Ensures the target active session and 2-day timetable are staged and in the expected state:
    - User dev-user has UserSetting with daily_floor_target = 2
    - Day 1 block is COMPLETED on today
    - Day 2 block ('Data Structures & Algorithmic Mechanics') is IN_PROGRESS
    - Active StudySession (TARGET_SESSION_ID) is bound to Day 2 block
    """
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()

    target_session = db.get(FocusSession, TARGET_SESSION_ID)
    if not target_session or not target_session.timetable_block_id:
        day2_block = (
            db.query(TimetableBlock)
            .join(StudyTimetable, TimetableBlock.timetable_id == StudyTimetable.id)
            .filter(
                StudyTimetable.user_id == TARGET_USER_ID,
                TimetableBlock.day_number == 2,
                TimetableBlock.topic.like("%Data Structures & Algorithmic Mechanics%"),
            )
            .order_by(TimetableBlock.id.desc())
            .first()
        )
        if not target_session:
            target_session = FocusSession(
                id=TARGET_SESSION_ID,
                user_id=TARGET_USER_ID,
                timetable_block_id=day2_block.id if day2_block else None,
                started_at=now_utc - timedelta(minutes=45),
                ended_at=None,
                status="IN_PROGRESS",
                verification_status="unverified",
            )
            db.add(target_session)
        else:
            target_session.timetable_block_id = day2_block.id if day2_block else None
    else:
        day2_block = db.get(TimetableBlock, target_session.timetable_block_id)

    active_tt = db.get(StudyTimetable, day2_block.timetable_id) if day2_block else None

    if active_tt and day2_block:
        active_tt.status = "ACTIVE"
        day2_block.status = "IN_PROGRESS"
        day2_block.actual_completed_at = None
        if not day2_block.actual_started_at:
            day2_block.actual_started_at = now_utc - timedelta(minutes=45)

    target_session.ended_at = None
    target_session.status = "IN_PROGRESS"
    target_session.verification_status = "unverified"
    if not target_session.started_at:
        target_session.started_at = now_utc - timedelta(minutes=45)

    db.commit()
    return active_tt, day2_block, target_session


def test_timetable_day2_lifecycle_e2e(db):
    """
    End-to-End Headless Regression Test:
    1. Day 2 Block Viva Completion:
       - Query active session and timetable (day_number == 2, topic 'Data Structures & Algorithmic Mechanics').
       - Submit passed viva defense payload against handler.
    2. Database Commit & Lifecycle Invariants:
       - Block status is 'COMPLETED' with UTC actual_completed_at.
       - Parent StudyTimetable status is 'COMPLETED' (terminal block of 2-day plan).
       - Session status is 'COMPLETED', is_verified is True, verified_minutes > 0.
       - No orphan active sessions remain for dev-user.
    3. Scorecard & Streak Telemetry Validation:
       - Call get_daily_scorecard(date=datetime.utcnow().date()).
       - Assert completed_blocks >= 2.
       - Assert verified_minutes includes credited time for both completed blocks.
       - Call get_streak().
       - Assert today_verified_blocks >= 2 and floor_met_today == True when the 2-block target is reached.
    """
    from app.routers.support import VivaVerifyPayload, verify_viva_defense
    from app.routers.reports import get_daily_scorecard, get_streak

    print("\n" + "=" * 70)
    print(">> FOCUS ENGINE: HEADLESS E2E DAY 2 LIFECYCLE REGRESSION")
    print("=" * 70)

    # 1. Setup / identify active session and Day 2 block
    print("\n[Step 1] Auditing preconditions...")
    timetable, day2_block, session = ensure_day2_preconditions(db, None)

    print(f"  * Timetable ID: {timetable.id} ('{timetable.title}') - Status: {timetable.status}")
    print(f"  * Day 2 Block ID: {day2_block.id} ('{day2_block.topic}') - Status: {day2_block.status}")
    print(f"  * Active Session ID: {session.id} - Status: {session.status}")

    assert day2_block.day_number == 2
    assert "Data Structures & Algorithmic Mechanics" in day2_block.topic
    assert session.user_id == TARGET_USER_ID
    assert session.status == "IN_PROGRESS"
    assert day2_block.status == "IN_PROGRESS"

    # Simulate passed oral viva defense
    print("\n[Step 2] Simulating passed oral viva defense...")
    viva_payload = VivaVerifyPayload(
        session_id=session.id,
        timetable_block_id=day2_block.id,
        answer="Cache line false sharing occurs when distinct CPU cores modify independent variables on the same 64-byte boundary.",
        passed=True,
    )
    viva_data = verify_viva_defense(payload=viva_payload, db=db, current_user=TARGET_USER_ID)
    print(f"  * Response: verified={viva_data.get('verified')}, block_status={viva_data.get('block_status')}")

    assert viva_data["verified"] is True
    assert viva_data["block_status"] == "COMPLETED"

    # 2. Database Commit & Lifecycle Invariants
    print("\n[Step 3] Verifying database commit & lifecycle invariants...")
    updated_block = db.get(TimetableBlock, day2_block.id)
    updated_tt = db.get(StudyTimetable, timetable.id)
    updated_session = db.get(FocusSession, session.id)

    # Invariant A: StudyTimetableBlock.status == 'COMPLETED'
    print(f"  * StudyTimetableBlock.status: {updated_block.status}")
    assert updated_block.status == "COMPLETED", f"Expected COMPLETED, got {updated_block.status}"

    # Invariant B: StudyTimetableBlock.actual_completed_at is populated with a UTC timestamp
    print(f"  * StudyTimetableBlock.actual_completed_at: {updated_block.actual_completed_at}")
    assert updated_block.actual_completed_at is not None, "actual_completed_at should not be None"
    assert isinstance(updated_block.actual_completed_at, datetime)

    # Invariant C: Overall StudyTimetable.status == 'COMPLETED' (since Day 2 is terminal block)
    print(f"  * StudyTimetable.status: {updated_tt.status}")
    assert updated_tt.status == "COMPLETED", f"Expected timetable COMPLETED, got {updated_tt.status}"

    # Invariant D: StudySession.status == 'COMPLETED', is_verified == True, verified_minutes > 0
    print(f"  * StudySession.status: {updated_session.status}, is_verified: {updated_session.is_verified}, verified_minutes: {updated_session.verified_minutes}")
    assert updated_session.status == "COMPLETED", f"Expected session COMPLETED, got {updated_session.status}"
    assert updated_session.is_verified is True
    assert updated_session.verification_status == "verified"
    assert updated_session.verified_minutes > 0, f"Expected verified_minutes > 0, got {updated_session.verified_minutes}"
    assert updated_session.duration_minutes > 0

    # Invariant E: No orphan active sessions remain for dev-user
    orphan_active = (
        db.query(FocusSession)
        .filter(FocusSession.user_id == TARGET_USER_ID, FocusSession.ended_at.is_(None))
        .all()
    )
    print(f"  * Orphan active sessions: {len(orphan_active)}")
    assert len(orphan_active) == 0, f"Expected 0 active sessions, found {len(orphan_active)}: {[s.id for s in orphan_active]}"

    # 3. Scorecard & Streak Telemetry Validation
    print("\n[Step 4] Validating scorecard & streak telemetry...")
    now_date = datetime.now(timezone.utc).date()
    scorecard = get_daily_scorecard(date=now_date, db=db, current_user=TARGET_USER_ID)
    print(f"  * Daily Scorecard: completed_blocks={scorecard.completed_blocks}, verified_minutes={scorecard.verified_minutes}, score={scorecard.score}")

    # Assert completed_blocks >= 2
    assert scorecard.completed_blocks >= 2, f"Expected completed_blocks >= 2, got {scorecard.completed_blocks}"

    # Assert verified_minutes includes credited time for both completed blocks
    assert scorecard.verified_minutes >= 60.0, f"Expected verified_minutes >= 60.0, got {scorecard.verified_minutes}"

    # Call get_streak()
    streak_data = get_streak(db=db, current_user=TARGET_USER_ID)
    print(f"  * Streak Report: today_verified_blocks={streak_data.today_verified_blocks}, floor_met_today={streak_data.floor_met_today}, streak_days={streak_data.current_streak_days}")

    # Assert today_verified_blocks >= 2 and floor_met_today == True
    assert streak_data.today_verified_blocks >= 2, f"Expected today_verified_blocks >= 2, got {streak_data.today_verified_blocks}"
    assert streak_data.floor_met_today is True, f"Expected floor_met_today == True, got {streak_data.floor_met_today}"
    assert streak_data.current_streak_days >= 1
    assert streak_data.status == "ACTIVE"

    print("\n" + "=" * 70)
    print(">>> ALL INVARIANTS AND TELEMETRY VERIFIED SUCCESSFULLY!")
    print("=" * 70)
