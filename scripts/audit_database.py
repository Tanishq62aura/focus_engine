import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

load_dotenv()
db_url = os.getenv("DATABASE_URL")
print(f"DATABASE_URL target: {db_url.split('@')[-1] if '@' in db_url else db_url}")

try:
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT 1;")).scalar()
        print(f"Connectivity test (SELECT 1): {'PASS' if res == 1 else 'FAIL'}")

    inspector = inspect(engine)
    tables = ["tasks", "schedules", "focus_sessions", "distraction_logs", "excuse_logs"]
    print("\n--- Schema & Table Integrity Audit ---")
    with engine.connect() as conn:
        for t in tables:
            exists = inspector.has_table(t)
            if exists:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {t};")).scalar()
                cols = inspector.get_columns(t)
                tz_cols = [c["name"] for c in cols if "TIME" in str(c["type"]).upper()]
                fks = inspector.get_foreign_keys(t)
                fk_info = [f"{fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}" for fk in fks]
                print(f"  [{t}] Status: OK | Rows: {cnt} | Timestamp Cols: {tz_cols} | Foreign Keys: {len(fks)}")
            else:
                print(f"  [{t}] Status: MISSING")
except Exception as e:
    print(f"Database audit error: {e}")
    sys.exit(1)
