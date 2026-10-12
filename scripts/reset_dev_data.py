"""
scripts/reset_dev_data.py
Safely resets all test data created for dev-user across all tables.
Preserves table schemas and structure intact.
"""
import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("Error: DATABASE_URL not found in .env")
    sys.exit(1)

target_user = "dev-user"

try:
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        print(f"[*] Resetting test data for user '{target_user}'...")

        # 1. Distraction logs (linked via session_id)
        d_res = conn.execute(
            text("""
                DELETE FROM distraction_logs 
                WHERE session_id IN (
                    SELECT id FROM focus_sessions WHERE user_id = :uid
                );
            """),
            {"uid": target_user}
        )
        print(f"  [OK] Deleted distraction_logs: {d_res.rowcount} rows")

        # 2. Excuse logs
        e_res = conn.execute(
            text("DELETE FROM excuse_logs WHERE user_id = :uid;"),
            {"uid": target_user}
        )
        print(f"  [OK] Deleted excuse_logs: {e_res.rowcount} rows")

        # 3. Focus sessions
        fs_res = conn.execute(
            text("DELETE FROM focus_sessions WHERE user_id = :uid;"),
            {"uid": target_user}
        )
        print(f"  [OK] Deleted focus_sessions: {fs_res.rowcount} rows")

        # 4. Schedules
        s_res = conn.execute(
            text("DELETE FROM schedules WHERE user_id = :uid;"),
            {"uid": target_user}
        )
        print(f"  [OK] Deleted schedules: {s_res.rowcount} rows")

        # 5. Tasks
        t_res = conn.execute(
            text("DELETE FROM tasks WHERE user_id = :uid;"),
            {"uid": target_user}
        )
        print(f"  [OK] Deleted tasks: {t_res.rowcount} rows")

    print("\n[SUCCESS] Test data reset complete for dev-user. Database is clean.")

except Exception as err:
    print(f"[ERROR] Database reset failed: {err}")
    sys.exit(1)
