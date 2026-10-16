"""
scripts/test_timetable_subsystem.py
Automated End-to-End Verification Suite for Focus Engine
Moderate Window-Anchored Study Timetable & Scheduling Subsystem.

Tests:
1. Authenticate via dev token.
2. Post a 7-day timetable payload to /api/timetables/stage and assert HTTP 200 with 7 blocks.
3. Fetch /api/timetables/active and verify schema structure and date/time field integrity.
4. Call /start on Day 1 block and verify transition to IN_PROGRESS with non-null actual_started_at.
5. Verify defensive validation (rejecting negative duration or invalid deadline).
6. Verify chat assistant TIMETABLE_CARD payload trigger.
7. Clean up test timetable data from database.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app import models

client = TestClient(app)


def run_tests():
    print("=" * 65)
    print("[*] FOCUS ENGINE: TIMETABLE & SCHEDULING SUBSYSTEM VERIFICATION")
    print("=" * 65)

    init_db()

    # Step 1: Authenticate via dev token
    print("\n[Step 1] Requesting dev authentication token...")
    auth_resp = client.post("/auth/dev-token")
    assert auth_resp.status_code == 200, f"Auth failed: {auth_resp.text}"
    token_data = auth_resp.json()
    token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[OK] Authenticated as dev-user. Token prefix: {token[:16]}...")

    db = SessionLocal()
    staged_timetable_id = None

    try:
        # Step 2: Post 7-day timetable payload to /api/timetables/stage
        print("\n[Step 2] Staging 7-day moderate window timetable payload...")
        today = date.today()
        sample_blocks = []
        for i in range(1, 8):
            sample_blocks.append({
                "day_number": i,
                "target_date": (today + timedelta(days=i - 1)).isoformat(),
                "window_start": "19:00:00",
                "window_deadline": "21:00:00",
                "duration_minutes": 120,
                "topic": f"Day {i}: Distributed Consensus & Raft Protocol",
                "deliverable_spec": "Raft leader election and log replication unit test suite.",
                "verification_rules": "Git diff with 90%+ branch coverage and oral defense viva.",
                "status": "PENDING",
            })

        payload = {
            "title": "Mastering Distributed Systems (7-Day Sprint)",
            "track_id": "coding",
            "total_days": 7,
            "blocks": sample_blocks,
        }

        stage_resp = client.post("/api/timetables/stage?commit=true", json=payload, headers=headers)
        assert stage_resp.status_code in (200, 201), f"Stage failed: {stage_resp.text}"
        data = stage_resp.json()
        staged_timetable_id = data["id"]
        assert len(data["blocks"]) == 7, f"Expected 7 blocks, got {len(data['blocks'])}"
        assert data["status"] == "ACTIVE", f"Expected ACTIVE status, got {data['status']}"
        assert data["title"] == payload["title"]
        print(f"[OK] Staged timetable ID {staged_timetable_id} with 7 blocks successfully.")

        # Step 3: Fetch /api/timetables/active
        print("\n[Step 3] Querying /api/timetables/active...")
        active_resp = client.get("/api/timetables/active", headers=headers)
        assert active_resp.status_code == 200, f"Active fetch failed: {active_resp.text}"
        active_data = active_resp.json()
        assert active_data["id"] == staged_timetable_id, f"Expected active ID {staged_timetable_id}"
        assert len(active_data["blocks"]) == 7, f"Expected 7 blocks, got {len(active_data['blocks'])}"
        day1_block_id = active_data["blocks"][0]["id"]
        print(f"[OK] Active timetable confirmed. Timetable ID: {staged_timetable_id}, Day 1 block ID: {day1_block_id}")

        # Step 4: Call /start on Day 1 block
        print(f"\n[Step 4] Starting execution block {day1_block_id} via /api/timetables/blocks/{day1_block_id}/start...")
        start_resp = client.post(f"/api/timetables/blocks/{day1_block_id}/start", headers=headers)
        assert start_resp.status_code == 200, f"Start block failed: {start_resp.text}"
        start_data = start_resp.json()
        assert start_data["status"] == "started", f"Expected 'started', got {start_data.get('status')}"
        assert start_data["block_id"] == day1_block_id
        assert start_data["actual_started_at"] is not None
        print(f"[OK] Block transitioned to IN_PROGRESS. Started at: {start_data['actual_started_at']}")
        print(f"     Timing diagnosis: early_start={start_data['early_start']}, late_start={start_data['late_start']}")

        # Step 4b: Verify duplicate start rejection
        print("\n[Step 4b] Testing defensive rejection of re-starting already IN_PROGRESS block...")
        re_start = client.post(f"/api/timetables/blocks/{day1_block_id}/start", headers=headers)
        assert re_start.status_code == 400, f"Expected 400 on duplicate start, got {re_start.status_code}"
        print("[OK] Duplicate block start rejected with HTTP 400.")

        # Step 5: Verify defensive schema validation
        print("\n[Step 5] Testing Pydantic validation (negative duration & invalid deadline)...")
        # 5a: Negative duration
        invalid_payload_duration = {
            "title": "Invalid Timetable",
            "track_id": "coding",
            "total_days": 1,
            "blocks": [{
                "day_number": 1,
                "target_date": today.isoformat(),
                "window_start": "19:00:00",
                "window_deadline": "21:00:00",
                "duration_minutes": -30,
                "topic": "Negative time",
                "deliverable_spec": "Invalid",
                "verification_rules": "None",
            }]
        }
        resp_dur = client.post("/api/timetables/stage", json=invalid_payload_duration, headers=headers)
        assert resp_dur.status_code == 422, f"Expected 422 for negative duration, got {resp_dur.status_code}"
        print("[OK] Correctly rejected negative duration with 422 Unprocessable Entity.")

        # 5b: Deadline before start
        invalid_payload_window = {
            "title": "Invalid Window",
            "track_id": "coding",
            "total_days": 1,
            "blocks": [{
                "day_number": 1,
                "target_date": today.isoformat(),
                "window_start": "22:00:00",
                "window_deadline": "18:00:00",  # Deadline earlier than start
                "duration_minutes": 60,
                "topic": "Inverted window",
                "deliverable_spec": "Invalid",
                "verification_rules": "None",
            }]
        }
        resp_win = client.post("/api/timetables/stage", json=invalid_payload_window, headers=headers)
        assert resp_win.status_code == 422, f"Expected 422 for inverted window, got {resp_win.status_code}"
        print("[OK] Correctly rejected invalid window_deadline <= window_start with 422.")

        # Step 6: Verify Chat Assistant TIMETABLE_CARD Trigger
        print("\n[Step 6] Testing Chat Assistant stage_window_timetable intent & TIMETABLE_CARD...")
        chat_resp = client.post(
            "/api/support/chat",
            json={"message": "Can you plan my 7 day study timetable for Operating Systems?"},
            headers=headers,
        )
        assert chat_resp.status_code == 200, f"Chat failed: {chat_resp.text}"
        chat_data = chat_resp.json()
        assert chat_data.get("action") == "TIMETABLE_CARD", f"Expected TIMETABLE_CARD, got {chat_data.get('action')}"
        assert "data" in chat_data and chat_data["data"]["total_days"] == 7
        assert len(chat_data["data"]["blocks"]) == 7
        print("[OK] Support Chat assistant successfully triggered stage_window_timetable and returned TIMETABLE_CARD.")

    finally:
        # Step 7: Cleanup test data
        print("\n[Step 7] Cleaning up test timetable database records...")
        if staged_timetable_id:
            db.query(models.StudyTimetable).filter(models.StudyTimetable.id == staged_timetable_id).delete()
            db.commit()
            print(f"[CLEANUP] Successfully cleaned up timetable ID {staged_timetable_id}.")
        db.close()

    print("\n" + "=" * 65)
    print("[SUCCESS] ALL TIMETABLE & SCHEDULING SUBSYSTEM TESTS PASSED!")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
