"""
scripts/run_operational_flight_test.py
End-to-End Operational Flight Test for Focus Engine.
Simulates a live 60-minute study block against the hosted Supabase PostgreSQL backend.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from dotenv import load_dotenv
from jose import jwt
from sqlalchemy import create_engine, text

# Set UTF-8 encoding for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 1. Environment & Auth Setup
load_dotenv()
jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "supabase-jwt-secret-njxlltvrcmqinrnhlpgh")
db_url = os.getenv("DATABASE_URL")
base_url = "http://127.0.0.1:8000"

user_id = "dev-user"
token = jwt.encode({"sub": user_id, "role": "authenticated"}, jwt_secret, algorithm="HS256")
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

def api_call(method: str, path: str, payload: dict = None) -> tuple[int, dict]:
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"[API ERROR] {method} {path} -> {e.code}: {err_body}")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"error": err_body}

def main():
    print("=" * 75)
    print("     FOCUS ENGINE - OPERATIONAL FLIGHT TEST (LIVE SUPABASE)")
    print("=" * 75)

    # Clean up lingering active sessions from previous interrupted runs
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE focus_sessions SET ended_at = NOW() WHERE user_id = :uid AND ended_at IS NULL"),
            {"uid": user_id}
        )
        conn.commit()

    # Pre-flight Baseline
    status_code, baseline_report = api_call("GET", "/reports/today")
    assert status_code == 200, f"Failed to get baseline today report: {status_code}"
    baseline_verified = baseline_report.get("verified_sessions", 0)
    baseline_focused_min = baseline_report.get("total_focused_minutes", 0.0)
    print(f"[*] Baseline Metrics (Today): Verified Sessions = {baseline_verified}, Focused = {baseline_focused_min}m\n")

    # -------------------------------------------------------------------------
    # Step 1: Task Creation
    # -------------------------------------------------------------------------
    print(">>> STEP 1: Creating Task (POST /tasks)")
    task_payload = {
        "title": "DSA: Interval Scheduling & Graph Traversal",
        "category": "coding",
        "priority": 1,
        "estimated_duration_min": 60,
        "min_goal": 30,
        "target_goal": 50,
        "stretch_goal": 60,
    }
    code, task_data = api_call("POST", "/tasks", task_payload)
    assert code == 201, f"Task creation failed ({code}): {task_data}"
    task_id = task_data["id"]
    print(f"  [OK] Task Created | ID: {task_id}")
    print(f"       Title: {task_data['title']} | Category: {task_data['category']} | Goals: {task_data['min_goal']}/{task_data['target_goal']}/{task_data['stretch_goal']}m\n")

    # -------------------------------------------------------------------------
    # Step 2: Session Lifecycle
    # -------------------------------------------------------------------------
    print(">>> STEP 2: Starting Session (POST /sessions/start)")
    session_payload = {
        "task_id": task_id,
        "is_five_min_intervention": False,
    }
    code, session_data = api_call("POST", "/sessions/start", session_payload)
    assert code == 201, f"Session start failed ({code}): {session_data}"
    session_id = session_data["id"]
    print(f"  [OK] Session Started | ID: {session_id} | Task: {session_data['task_id']}")

    # 2a. Verify Database State in Supabase directly
    print("\n>>> STEP 2a: Verifying Supabase Database State directly via SQLAlchemy")
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, user_id, task_id, started_at, ended_at, verification_status, pasted_burst_detected FROM focus_sessions WHERE id = :id"),
            {"id": session_id}
        ).mappings().first()

    assert row is not None, "Session row not found in Supabase database!"
    assert row["started_at"] is not None, "started_at is null!"
    assert row["ended_at"] is None, "ended_at is not null (session should be active)!"
    print(f"  [OK] Supabase Row Verified: id={row['id']}")
    print(f"       User ID: {row['user_id']}")
    print(f"       Status: ACTIVE (ended_at is NULL)")
    print(f"       Started At (UTC): {row['started_at']}")
    print(f"       Verification Status: {row['verification_status']}")

    # 2b. Telemetric Noise: Distraction Injection
    print("\n>>> STEP 2b: Injecting Telemetric Noise (POST /distractions)")
    distraction_payload = {
        "session_id": session_id,
        "interruption_type": "phone",
        "note": "Checked notification",
    }
    code, dist_data = api_call("POST", "/distractions", distraction_payload)
    assert code == 201, f"Distraction injection failed ({code}): {dist_data}"
    print(f"  [OK] Distraction Logged | ID: {dist_data['id']} | Type: {dist_data['interruption_type']}")

    # 2c. Telemetric Noise: VS Code Watchdog Paste Burst Check
    print("\n>>> STEP 2c: Triggering VS Code Paste Burst Watchdog (POST /sessions/active/paste-event)")
    paste_payload = {
        "file_name": "interval_scheduler.py",
        "char_count": 320,
        "line_number": 15,
    }
    code, paste_data = api_call("POST", "/sessions/active/paste-event", paste_payload)
    assert code == 200, f"Paste event failed ({code}): {paste_data}"
    assert paste_data.get("flagged") is True, "Paste burst should be flagged!"
    print(f"  [OK] Watchdog Burst Flagged: {paste_data.get('flagged')} | Session: {paste_data.get('session_id')}")
    print(f"       Message: {paste_data.get('message')}")

    # -------------------------------------------------------------------------
    # Step 3: Viva Defense & Submission
    # -------------------------------------------------------------------------
    print("\n>>> STEP 3: Submitting Code Proof (POST /sessions/active/complete)")
    code_proof = (
        "def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:\n"
        "    if not intervals:\n"
        "        return []\n"
        "    intervals.sort(key=lambda x: x[0])\n"
        "    merged = [intervals[0]]\n"
        "    for curr in intervals[1:]:\n"
        "        prev = merged[-1]\n"
        "        if curr[0] <= prev[1]:\n"
        "            prev[1] = max(prev[1], curr[1])\n"
        "        else:\n"
        "            merged.append(curr)\n"
        "    return merged\n"
    )
    complete_payload = {
        "code_snippet": code_proof,
        "duration_minutes": 60,
        "notes": "Simulated live 60-minute study block execution.",
    }
    code, complete_data = api_call("POST", "/sessions/active/complete", complete_payload)
    assert code == 200, f"Complete session failed ({code}): {complete_data}"
    viva_question = complete_data.get("viva_question")
    print(f"  [OK] Session Marked Completed | Focused Seconds: {complete_data.get('actual_focused_seconds')}s (60 min)")
    print(f"       Verification Status: {complete_data.get('verification_status')}")
    print(f"       Generated Viva Question:\n       \"{viva_question}\"")

    # 3b. Submit Viva Defense Answer
    print("\n>>> STEP 3b: Submitting Viva Defense Answer (POST /sessions/{session_id}/submit-viva)")
    viva_answer = (
        "Sort intervals by start time. Iterate and merge overlapping intervals when "
        "curr.start <= prev.end; otherwise append to output. "
        "Time complexity O(N log N), space complexity O(N)."
    )
    submit_payload = {"answer": viva_answer}
    code, viva_result = api_call("POST", f"/sessions/{session_id}/submit-viva", submit_payload)
    assert code == 200, f"Viva submission failed ({code}): {viva_result}"

    feedback = viva_result.get("viva_feedback", "")
    verification_status = viva_result.get("verification_status")
    is_verified = viva_result.get("verified", False) or verification_status == "verified"

    print(f"  [OK] Defense Evaluation Complete:")
    print(f"       Verification Status: {verification_status}")
    print(f"       Verdict Verified: {is_verified}")
    print(f"       Examiner Feedback:\n       {feedback}")

    assert is_verified, f"Viva evaluation expected PASS, got {verification_status}: {feedback}"

    # Verify directly in Supabase
    with engine.connect() as conn:
        db_session = conn.execute(
            text("SELECT verification_status, actual_focused_seconds, viva_question, viva_answer, viva_feedback FROM focus_sessions WHERE id = :id"),
            {"id": session_id}
        ).mappings().first()
    assert db_session["verification_status"] == "verified", f"Supabase status not verified: {db_session['verification_status']}"
    print(f"\n  [OK] Direct Supabase Assertion: verification_status = '{db_session['verification_status']}' (PASS)")

    # -------------------------------------------------------------------------
    # Step 4: Telemetry & Analytics Validation
    # -------------------------------------------------------------------------
    print("\n>>> STEP 4: Validating Telemetry & Reporting (GET /reports/today & GET /reports/friction-analysis)")
    code, today_report = api_call("GET", "/reports/today")
    assert code == 200, f"GET /reports/today failed ({code}): {today_report}"

    new_verified = today_report.get("verified_sessions", 0)
    new_focused_min = today_report.get("total_focused_minutes", 0.0)
    print(f"  [OK] /reports/today updated:")
    print(f"       - Total Focused Minutes: {new_focused_min}m (delta: +{round(new_focused_min - baseline_focused_min, 1)}m)")
    print(f"       - Verified Sessions:     {new_verified} (incremented from {baseline_verified} -> {new_verified})")
    print(f"       - Execution Ratio:       {today_report.get('execution_ratio_pct')}%")
    print(f"       - Distractions Breakdown: {today_report.get('distraction_breakdown')}")

    assert new_verified == baseline_verified + 1, f"Verified sessions did not increment by 1: {baseline_verified} -> {new_verified}"
    assert new_focused_min >= baseline_focused_min + 60.0, f"Focused minutes did not increase by 60: {baseline_focused_min} -> {new_focused_min}"

    # Friction report check
    code, friction_report = api_call("GET", "/reports/friction-analysis?days=7")
    assert code == 200, f"GET /reports/friction-analysis failed ({code}): {friction_report}"
    category_names = [c["category"] for c in friction_report.get("categories", [])]
    print(f"  [OK] /reports/friction-analysis returned successfully (Categories: {category_names})")


    # -------------------------------------------------------------------------
    # Step 5: Final Structured Terminal Deliverable
    # -------------------------------------------------------------------------
    print("\n" + "=" * 75)
    print("             OPERATIONAL FLIGHT TEST EXECUTION REPORT")
    print("=" * 75)
    print(f"Database Target:       Supabase Hosted PostgreSQL ({db_url.split('@')[-1] if '@' in db_url else 'configured'})")
    print(f"Authenticated User:    {user_id}")
    print(f"Task ID:               {task_id}")
    print(f"Session ID:            {session_id}")
    print(f"Watchdog Status:       PASTE_BURST_FLAGGED = True (320 chars, line 15)")
    print(f"Distraction Noise:     interruption_type='phone' registered")
    print("-" * 75)
    print("AI VIVA EXAMINATION BREAKDOWN:")
    print(f"Question:              {viva_question}")
    print(f"Answer Submitted:      {viva_answer}")
    print(f"Verdict:               PASS (verified: true, status: '{verification_status}')")
    print(f"Feedback:              {feedback.strip()}")
    print("-" * 75)
    print("FINAL /reports/today JSON METRICS:")
    print(json.dumps(today_report, indent=2))
    print("=" * 75)
    print(">>> FLIGHT TEST STATUS: ALL SYSTEM CHECKS PASSED (100% OPERATIONAL) <<<")
    print("=" * 75)

if __name__ == "__main__":
    main()
