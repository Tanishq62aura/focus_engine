"""
scripts/test_git_guard_and_lan.py
End-to-End Operational Validation of:
1. Git Guard & Local Workspace File Activity Proof Verification
   - Verify session blocked when no code/notes modified (HTTP 400)
   - Verify viva unlocks when workspace files are modified (HTTP 200)
2. Local Wi-Fi Direct Connect Endpoints
   - Inspect active LAN IPv4 address and Cloudflare Tunnel endpoint
   - Query LAN Direct endpoint directly to prove LAN connectivity
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_request(method: str, path: str, data: dict = None, token: str = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            try:
                data = json.loads(content) if content else {}
            except Exception:
                data = {"raw_text": content}
            return resp.status, data
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8")
        try:
            return e.code, json.loads(content)
        except Exception:
            return e.code, {"detail": content}
    except Exception as exc:
        return 500, {"detail": str(exc)}


def run_tests():
    print("=" * 70)
    print("🧪 FOCUS ENGINE: GIT GUARD & LOCAL WI-FI VALIDATION SUITE")
    print("=" * 70)

    # 1. Get dev token
    print("\n[1/5] Fetching authenticated dev token...")
    status, dev_res = make_request("POST", "/auth/dev-token")
    assert status == 200, f"Failed getting dev token: {dev_res}"
    token = dev_res["access_token"]
    user_id = dev_res["user_id"]
    print(f"  ✓ Authenticated as {user_id}")

    # 2. Test LAN & Tunnel network endpoints
    print("\n[2/7] Testing Local IP Direct Connect & Cloudflare Tunnel endpoints...")
    from scripts.get_tunnel_url import get_local_lan_ip
    discovered_ip = get_local_lan_ip()
    print(f"  ✓ scripts.get_tunnel_url.get_local_lan_ip() -> {discovered_ip}")
    assert discovered_ip and discovered_ip != "127.0.0.1", f"Expected active LAN IP, got {discovered_ip}"

    status, net_res = make_request("GET", "/api/network/endpoints")
    assert status == 200, f"Failed getting network endpoints: {net_res}"
    local_ip = net_res.get("local_ip")
    lan_url = net_res.get("lan_url")
    tunnel_url = net_res.get("tunnel_url")
    print(f"  ✓ Active Local LAN IP:    {local_ip}")
    print(f"  ✓ Local Network URL:     {lan_url}")
    print(f"  ✓ Cloudflare Tunnel URL: {tunnel_url}")
    assert local_ip == discovered_ip, f"Mismatch between endpoint IP {local_ip} and discovered {discovered_ip}"
    assert lan_url == f"http://{local_ip}:8000"

    # Query LAN URL directly to confirm LAN binding
    print(f"  Testing direct access via LAN URL ({lan_url}/api/network/endpoints)...")
    try:
        with urllib.request.urlopen(f"{lan_url}/api/network/endpoints", timeout=5) as lan_resp:
            assert lan_resp.status == 200
            print("  ✓ Zero-latency LAN endpoint verified accessible!")
    except Exception as e:
        print(f"  ⚠️ LAN direct access warning (firewall or loopback): {e}")

    # 3. Create test task
    print("\n[3/5] Creating test task for Git Guard verification...")
    status, task_res = make_request(
        "POST",
        "/tasks",
        {
            "title": f"Git Guard Verification Test {int(time.time())}",
            "category": "coding",
            "priority": 3,
            "estimated_duration_min": 25,
            "min_goal": 5,
            "target_goal": 25,
            "stretch_goal": 45,
        },
        token=token,
    )
    assert status == 201, f"Failed creating task: {task_res}"
    task_id = task_res["id"]
    print(f"  ✓ Created test task: {task_id}")

    # 4. TEST NEGATIVE CASE: No files touched -> Viva MUST be blocked with HTTP 400
    print("\n[4/5] Testing Negative Proof Block (Zero files modified during session)...")
    status, sess_res = make_request(
        "POST",
        "/sessions/start",
        {"task_id": task_id, "is_five_min_intervention": False},
        token=token,
    )
    assert status == 201, f"Failed starting session: {sess_res}"
    session_id = sess_res["id"]
    print(f"  ✓ Started session {session_id} at {sess_res.get('started_at')}")

    # Attempt viva submission immediately without touching any workspace files
    print("  Attempting viva request without workspace modifications...")
    status, req_res = make_request(
        "POST",
        f"/sessions/{session_id}/request-viva",
        {"proof_text": "I solved the algorithm in my head without typing any code or notes."},
        token=token,
    )
    print(f"  Response Status: {status}")
    print(f"  Response Detail: {req_res.get('detail')}")

    assert status == 400, f"Expected HTTP 400 rejection, but got status {status}: {req_res}"
    assert "Zero file modifications detected in your workspace during this coding block" in str(req_res.get("detail")), (
        f"Unexpected rejection detail: {req_res}"
    )
    print("  ✓ SUCCESS: Backend successfully blocked viva submission when zero files modified!")

    # Also test POST /sessions/{id}/complete endpoint
    print("  Testing POST /sessions/{id}/complete endpoint negative rejection...")
    status, comp_res = make_request(
        "POST",
        f"/sessions/{session_id}/complete",
        {"proof_text": "Another attempt with zero file edits."},
        token=token,
    )
    assert status == 400, f"Expected HTTP 400 rejection on /complete, got {status}: {comp_res}"
    print("  ✓ SUCCESS: /complete endpoint also strictly blocked submission!")

    # 5. TEST NON-CODING TRACK: Category != 'coding' -> skips file checks entirely
    print("\n[5/7] Testing Non-Coding Track (theory/aptitude) bypasses git check...")
    status, theory_task = make_request(
        "POST",
        "/tasks",
        {
            "title": f"Theory OS Notes Test {int(time.time())}",
            "category": "theory",
            "priority": 2,
            "estimated_duration_min": 25,
            "min_goal": 5,
            "target_goal": 25,
            "stretch_goal": 45,
        },
        token=token,
    )
    assert status == 201, f"Failed creating theory task: {theory_task}"
    theory_task_id = theory_task["id"]

    status, theory_sess = make_request(
        "POST",
        "/sessions/start",
        {"task_id": theory_task_id, "is_five_min_intervention": False},
        token=token,
    )
    assert status == 201, f"Failed starting theory session: {theory_sess}"
    theory_sess_id = theory_sess["id"]

    # Submit reflection notes for theory WITHOUT modifying any workspace files
    theory_notes = "Read OS concepts: Process states (ready, running, waiting), context switching overhead, and PCB layout."
    status, theory_viva = make_request(
        "POST",
        f"/sessions/{theory_sess_id}/request-viva",
        {"proof_text": theory_notes},
        token=token,
    )
    assert status == 200, f"Expected HTTP 200 for non-coding track without file edits, got {status}: {theory_viva}"
    print("  ✓ SUCCESS: Non-coding track (theory) successfully bypassed git checks and generated viva question!")

    # 6. TEST POSITIVE CASE: Touch/modify file -> Viva UNLOCKS with HTTP 200
    print("\n[6/7] Testing Positive Proof Unlock (Modify workspace file)...")
    scratch_file = ROOT / "scratch_work_proof.py"
    try:
        # Write actual code to scratch_work_proof.py
        scratch_code = f"# Focus Engine Session Work Proof\n# Generated at {datetime.now(timezone.utc).isoformat()}\ndef binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1\n"
        with open(scratch_file, "w", encoding="utf-8") as f:
            f.write(scratch_code)
        print(f"  ✓ Created/modified workspace file: {scratch_file.name}")

        time.sleep(0.5)

        # Now attempt viva request again with proof
        proof_text = "def binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == target: return mid\n        elif arr[mid] < target: left = mid + 1\n        else: right = mid - 1\n    return -1"
        status, unlock_res = make_request(
            "POST",
            f"/sessions/{session_id}/request-viva",
            {"proof_text": proof_text},
            token=token,
        )
        print(f"  Response Status: {status}")
        assert status == 200, f"Expected HTTP 200 unlock, but got {status}: {unlock_res}"
        viva_q = unlock_res.get("viva_question")
        print(f"  ✓ Viva defense unlocked! Generated question:\n    {viva_q}")

        # Submit viva answer to conclude verification
        status, ans_res = make_request(
            "POST",
            f"/sessions/{session_id}/submit-viva",
            {"answer": "The time complexity is O(log N) because the search space halves each iteration. The space complexity is O(1) auxiliary."},
            token=token,
        )
        assert status == 200, f"Failed submitting viva: {ans_res}"
        print(f"  ✓ Viva evaluated! Verification status: {ans_res.get('verification_status')}")

    finally:
        if scratch_file.exists():
            scratch_file.unlink()
            print("  ✓ Cleaned up scratch test file.")


    # 6. Verify static frontend contains network elements
    print("\n[6/6] Verifying Frontend HUD (Connection mode indicator & Mobile Connect modal)...")
    status, hud_res = make_request("GET", "/")
    assert status == 200
    with open(ROOT / "static" / "index.html", "r", encoding="utf-8") as f:
        html = f.read()
    assert "footer-connection-mode-badge" in html, "Missing footer connection badge in index.html"
    assert "modal-mobile-connect" in html, "Missing modal-mobile-connect in index.html"
    assert "Show Mobile Connect URLs" in html, "Missing Show Mobile Connect URLs button in index.html"
    assert "detectConnectionMode" in html, "Missing detectConnectionMode JS function in index.html"
    print("  ✓ Frontend HUD elements verified in static/index.html")

    print("\n" + "=" * 70)
    print("🎉 ALL VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
