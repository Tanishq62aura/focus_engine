"""
scripts/verify_latest_session.py
Dynamic database verification audit against the live hosted Supabase PostgreSQL database.
Inspects schemas dynamically without assuming hardcoded column names.
"""
import json
import os
import sys
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

# Reconfigure stdout for UTF-8 in Windows environments
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print(json.dumps({"error": "DATABASE_URL not found in .env"}))
    sys.exit(1)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)

def run_audit():
    engine = create_engine(db_url, pool_pre_ping=True)
    inspector = inspect(engine)

    audit_result = {
        "database_target": db_url.split("@")[-1] if "@" in db_url else "configured",
        "schema_introspection": {},
        "latest_task": None,
        "latest_focus_session": None,
        "distraction_logs": {},
        "verification_analysis": {},
    }

    # 1. Dynamic Schema Introspection
    tables = ["tasks", "focus_sessions", "distraction_logs"]
    cols_by_table = {}
    for table_name in tables:
        columns = inspector.get_columns(table_name)
        col_names = [c["name"] for c in columns]
        cols_by_table[table_name] = col_names
        audit_result["schema_introspection"][table_name] = {
            "columns": col_names,
            "column_count": len(col_names),
        }

    with engine.connect() as conn:
        # 2. Query Latest Task
        task_cols = cols_by_table["tasks"]
        task_order_col = "created_at" if "created_at" in task_cols else task_cols[0]
        task_row = conn.execute(
            text(f"SELECT * FROM tasks ORDER BY {task_order_col} DESC LIMIT 1")
        ).mappings().fetchone()

        if task_row:
            audit_result["latest_task"] = dict(task_row)

        # 3. Query Latest Focus Session
        session_cols = cols_by_table["focus_sessions"]
        # Find best timestamp ordering column dynamically
        candidates = ["started_at", "start_time", "created_at", "timestamp"]
        session_order_col = next((c for c in candidates if c in session_cols), session_cols[0])
        
        session_row = conn.execute(
            text(f"SELECT * FROM focus_sessions ORDER BY {session_order_col} DESC LIMIT 1")
        ).mappings().fetchone()

        if session_row:
            s_data = dict(session_row)
            # Omit large raw code proof text for clean output
            if "proof_text" in s_data and s_data["proof_text"] and len(str(s_data["proof_text"])) > 100:
                s_data["proof_text"] = str(s_data["proof_text"])[:80] + "... [truncated]"
            audit_result["latest_focus_session"] = s_data

        # 4. Query Distraction Logs
        dist_cols = cols_by_table["distraction_logs"]
        dist_order_col = next((c for c in ["timestamp", "created_at", "time"] if c in dist_cols), dist_cols[0])
        
        total_distractions = conn.execute(text("SELECT count(*) FROM distraction_logs")).scalar()
        latest_dist_row = conn.execute(
            text(f"SELECT * FROM distraction_logs ORDER BY {dist_order_col} DESC LIMIT 1")
        ).mappings().fetchone()

        audit_result["distraction_logs"] = {
            "total_count": total_distractions,
            "latest_event": dict(latest_dist_row) if latest_dist_row else None,
        }

    # 5. Evaluate Telemetry & Viva Defense State
    session = audit_result["latest_focus_session"]
    if session:
        # Check completion state
        is_completed = (
            session.get("ended_at") is not None
            or session.get("status") in ("completed", "ended")
            or session.get("is_completed") is True
        )
        
        # Check verification state
        verification_status = session.get("verification_status")
        is_verified = (
            verification_status == "verified"
            or session.get("verified") is True
            or session.get("is_verified") is True
        )

        viva_feedback = session.get("viva_feedback", "")
        viva_verdict_pass = (
            "VERDICT: PASS" in (viva_feedback or "").upper()
            or session.get("viva_verdict") == "PASS"
            or is_verified
        )

        audit_result["verification_analysis"] = {
            "session_id": session.get("id"),
            "task_id": session.get("task_id"),
            "session_completed": is_completed,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "actual_focused_seconds": session.get("actual_focused_seconds"),
            "actual_focused_minutes": round((session.get("actual_focused_seconds") or 0) / 60, 2),
            "pasted_burst_detected": session.get("pasted_burst_detected"),
            "verification_status": verification_status,
            "verified": is_verified,
            "viva_verdict_pass": viva_verdict_pass,
            "viva_question": session.get("viva_question"),
            "viva_score": session.get("viva_score", "N/A (Heuristic / Binary Verdict)"),
            "flight_test_confirmed": bool(is_completed and is_verified and viva_verdict_pass),
        }

    # Print Clean Formatted JSON Report
    print(json.dumps(audit_result, indent=2, default=json_serial))

if __name__ == "__main__":
    run_audit()
