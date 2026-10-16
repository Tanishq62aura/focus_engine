import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models import StudyTimetable, TimetableBlock
from app.routers.support import execute_stage_window_timetable

# Mock authenticated user for testing
class MockUser:
    id = "usr_tool_verification_gemini_01"
    email = "gemini_tool_test@focusengine.internal"

def run_test():
    print("[-] Verifying Gemini stage_window_timetable tool execution handler...")
    db = SessionLocal()
    mock_user = MockUser()

    try:
        # 1. Supply tool-call argument dictionary matching Gemini output format
        tool_args = {
            "title": "7-Day Distributed Systems Acceleration",
            "track_id": "coding",
            "total_days": 7,
            "blocks": [
                {
                    "day_number": i,
                    "days_from_today": i - 1,
                    "window_start": "19:00",
                    "window_deadline": "21:00",
                    "duration_minutes": 120,
                    "topic": f"Distributed Consensus & Raft Day {i}",
                    "deliverable_spec": f"Raft state machine unit tests and node logs {i}",
                    "verification_rules": "Passes all partition simulation tests, oral defense viva"
                }
                for i in range(1, 8)
            ]
        }

        # 2. Call execute_stage_window_timetable directly
        result = execute_stage_window_timetable(db=db, current_user=mock_user, args=tool_args)

        # Assert 1: Return dictionary format and card metadata
        assert result["type"] == "TIMETABLE_CARD", f"Expected type TIMETABLE_CARD, got {result.get('type')}"
        assert result["title"] == "7-Day Distributed Systems Acceleration"
        assert result["total_days"] == 7
        assert len(result["blocks"]) == 7, f"Expected 7 blocks in card payload, got {len(result['blocks'])}"
        print("  [+] Handler returned valid TIMETABLE_CARD dictionary")

        # Assert 2: Database creates a StudyTimetable row with status ACTIVE
        timetable_id = result["timetable_id"]
        timetable = db.query(StudyTimetable).filter(StudyTimetable.id == timetable_id).first()
        assert timetable is not None, "StudyTimetable row not found in database"
        assert timetable.status == "ACTIVE", f"Expected status ACTIVE, got {timetable.status}"
        assert timetable.user_id == mock_user.id
        print(f"  [+] Active StudyTimetable record verified in database (ID: {timetable_id})")

        # Assert 3: Exactly 7 TimetableBlock rows are created with matching foreign keys
        blocks = db.query(TimetableBlock).filter(TimetableBlock.timetable_id == timetable_id).order_by(TimetableBlock.day_number).all()
        assert len(blocks) == 7, f"Expected 7 blocks in database, got {len(blocks)}"
        for b in blocks:
            assert b.timetable_id == timetable_id
            assert b.status == "PENDING"
            assert b.duration_minutes == 120
            assert b.window_start.strftime("%H:%M") == "19:00"
            assert b.window_deadline.strftime("%H:%M") == "21:00"
        print("  [+] Exactly 7 TimetableBlock rows created with matching foreign keys")

        # Assert 4: Archive-on-replace behavior by staging a second timetable
        second_args = {
            "title": "Replacement 3-Day Plan",
            "track_id": "theory",
            "total_days": 3,
            "blocks": [
                {
                    "day_number": i,
                    "days_from_today": i - 1,
                    "window_start": "20:00",
                    "window_deadline": "22:00",
                    "duration_minutes": 90,
                    "topic": f"Theory Topic {i}",
                    "deliverable_spec": f"Notes {i}",
                    "verification_rules": "Viva oral defense"
                }
                for i in range(1, 4)
            ]
        }
        res2 = execute_stage_window_timetable(db=db, current_user=mock_user, args=second_args)
        db.refresh(timetable)
        assert timetable.status == "ARCHIVED", f"Expected previous timetable to be ARCHIVED, got {timetable.status}"
        t2_id = res2["timetable_id"]
        t2 = db.query(StudyTimetable).filter(StudyTimetable.id == t2_id).first()
        assert t2.status == "ACTIVE"
        print("  [+] Prior active timetable successfully archived upon replacement")

    finally:
        # Clean up test records
        try:
            db.query(StudyTimetable).filter(StudyTimetable.user_id == mock_user.id).delete()
            db.commit()
            print("  [+] Cleaned up all test database records")
        except Exception as e:
            print(f"  [!] Cleanup warning: {e}")
        finally:
            db.close()

    print("\n[SUCCESS] Gemini stage_window_timetable tool handler verification passed completely.")

if __name__ == "__main__":
    run_test()
