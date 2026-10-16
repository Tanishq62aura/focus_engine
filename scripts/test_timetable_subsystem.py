import os
import sys
from datetime import date, time, timedelta

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import StudyTimetable, TimetableBlock
from app.routers.auth import get_current_user

# Mock authenticated user for testing
class MockUser:
    id = "usr_test_verification_01"
    email = "test_verify@focusengine.internal"

app.dependency_overrides[get_current_user] = lambda: MockUser()
client = TestClient(app)

def run_tests():
    print("[-] Running Focus Engine Subsystem Verification...")

    # 1. Verify Healthz (GET and HEAD)
    res_get = client.get("/healthz")
    assert res_get.status_code == 200, f"GET /healthz failed: {res_get.status_code}"
    assert res_get.json() == {"status": "ok", "service": "focus-engine"}
    print("  [+] GET /healthz returned 200 OK")

    res_head = client.head("/healthz")
    assert res_head.status_code == 200, f"HEAD /healthz failed: {res_head.status_code}"
    print("  [+] HEAD /healthz returned 200 OK")

    # 2. Defensive Validation Checks (Invalid deadlines & durations)
    today = date.today()
    invalid_payload = {
        "title": "Invalid Schedule",
        "track_id": "coding",
        "total_days": 1,
        "blocks": [
            {
                "day_number": 1,
                "target_date": str(today),
                "window_start": "21:00:00",
                "window_deadline": "19:00:00",  # Invalid: deadline before start
                "duration_minutes": 120,
                "topic": "Python Internals",
                "deliverable_spec": "CLI App",
                "verification_rules": "All tests pass"
            }
        ]
    }
    res_invalid = client.post("/api/timetables/stage", json=invalid_payload)
    assert res_invalid.status_code == 422, f"Expected 422 validation error, got {res_invalid.status_code}"
    print("  [+] Defensive schema check rejected invalid window (deadline <= start)")

    # 3. Stage Valid 7-Day Timetable
    valid_blocks = []
    for day in range(1, 8):
        valid_blocks.append({
            "day_number": day,
            "target_date": str(today + timedelta(days=day - 1)),
            "window_start": "19:00:00",
            "window_deadline": "21:00:00",
            "duration_minutes": 120,
            "topic": f"Python Module Day {day}",
            "deliverable_spec": f"Unit test suite {day}",
            "verification_rules": "No flake8 errors, 100% coverage"
        })

    valid_payload = {
        "title": "7-Day Python Acceleration",
        "track_id": "coding",
        "total_days": 7,
        "blocks": valid_blocks
    }

    res_stage = client.post("/api/timetables/stage", json=valid_payload)
    assert res_stage.status_code == 201, f"Stage failed: {res_stage.text}"
    staged_data = res_stage.json()
    assert staged_data["total_days"] == 7
    assert len(staged_data["blocks"]) == 7
    timetable_id = staged_data["id"]
    day1_block_id = staged_data["blocks"][0]["id"]
    print("  [+] Staged 7-day timetable committed to DB (HTTP 201)")

    # 4. Fetch Active Timetable
    res_active = client.get("/api/timetables/active")
    assert res_active.status_code == 200, f"Get active failed: {res_active.text}"
    active_data = res_active.json()
    assert active_data["id"] == timetable_id
    assert active_data["status"] == "ACTIVE"
    print("  [+] Active timetable retrieved successfully (HTTP 200)")

    # 5. Start Block Session
    res_start = client.post(f"/api/timetables/blocks/{day1_block_id}/start")
    assert res_start.status_code == 200, f"Start block failed: {res_start.text}"
    start_data = res_start.json()
    assert start_data["status"] == "started"
    assert start_data["block_id"] == day1_block_id
    assert start_data["actual_started_at"] is not None
    print("  [+] Day 1 block transitioned to IN_PROGRESS with timestamp")

    # 6. Cleanup Test Artifacts
    db = SessionLocal()
    try:
        db.query(StudyTimetable).filter(StudyTimetable.user_id == MockUser.id).delete()
        db.commit()
        print("  [+] Cleaned up test database records")
    finally:
        db.close()

    print("\n[SUCCESS] All 6 verification checks passed cleanly.")

if __name__ == "__main__":
    run_tests()
