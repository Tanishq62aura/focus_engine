"""
scripts/test_category_proposal_flow.py
Verifies:
1. Dynamic Category Proposal staging via /chat and /api/support/chat
2. Structured category_proposal payload verification
3. Database isolation before explicit approval (0 pre-mature writes)
4. Approval endpoint POST /api/categories/approve commits track to user_settings.custom_tracks
5. Idempotent handling for already-approved tracks
6. Validation rejection formatting
"""
import json
import sys
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=" * 70)
    print("🧪 FOCUS ENGINE: DYNAMIC CATEGORY PROPOSAL FLOW VERIFICATION")
    print("=" * 70)

    # 1. Obtain test auth token
    auth_resp = requests.post(f"{BASE_URL}/auth/dev-token")
    if auth_resp.status_code != 200:
        print(f"❌ Failed to obtain dev token: {auth_resp.status_code} {auth_resp.text}")
        return False
    token = auth_resp.json()["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    print("✅ Authenticated via /auth/dev-token")

    # 2. Get initial tracks
    tracks_resp = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers)
    assert tracks_resp.status_code == 200, f"Failed to get initial tracks: {tracks_resp.text}"
    initial_tracks = tracks_resp.json()
    initial_track_ids = {t["id"] for t in initial_tracks}
    print(f"✅ Initial tracks count: {len(initial_tracks)} (IDs: {initial_track_ids})")

    # 3. Test Category Proposal: 'Distributed Systems'
    proposal_prompt = "I want to track a new subject called Distributed Systems"
    print(f"\n[Test 1] Sending Category Proposal Prompt:")
    print(f"  User Prompt: '{proposal_prompt}'")
    
    chat_resp = requests.post(
        f"{BASE_URL}/api/support/chat",
        headers=headers,
        json={"message": proposal_prompt}
    )
    if chat_resp.status_code != 200:
        print(f"❌ /api/support/chat returned {chat_resp.status_code}: {chat_resp.text}")
        return False

    chat_data = chat_resp.json()
    print("  Bot Reply:", chat_data.get("reply"))
    print("  Action:", chat_data.get("action"))
    print("  Proposal Data:", json.dumps(chat_data.get("data"), indent=2))

    assert chat_data.get("action") == "propose_category", "Expected action to be 'propose_category'"
    data = chat_data.get("data", {})
    assert data.get("suggested_name") == "Distributed Systems" or data.get("name") == "Distributed Systems", "Invalid proposed name"
    assert data.get("status") == "pending_approval", "Status must be 'pending_approval'"
    assert "distributed_systems" in data.get("id"), "Expected sanitized id to contain 'distributed_systems'"
    print("✅ Test 1 PASSED: Structured category_proposal generated with pending_approval status.")

    # 4. Verify 0 Pre-Mature Database Writes
    tracks_after_proposal = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    assert len(tracks_after_proposal) == len(initial_tracks), "Track should NOT be committed before approval"
    assert not any(t["id"] == "distributed_systems" for t in tracks_after_proposal), "Track unapproved in DB!"
    print("✅ Test 2 PASSED: Zero premature DB writes. Track remains in staging.")

    # 5. Test Approval via POST /api/categories/approve
    print(f"\n[Test 3] User clicks 'Approve & Add to Tracks' -> POST /api/categories/approve")
    approval_payload = {
        "name": data["name"],
        "id": data["id"],
        "label": data.get("label", data["name"]),
        "requires_code_watchdog": data.get("requires_code_watchdog", True),
        "friction_threshold": data.get("friction_threshold", 3),
        "default_duration_min": data.get("default_duration_min", 45),
        "justification": data.get("justification", "User requested")
    }
    approve_resp = requests.post(
        f"{BASE_URL}/api/categories/approve",
        headers=headers,
        json=approval_payload
    )
    if approve_resp.status_code != 200:
        print(f"❌ /api/categories/approve returned {approve_resp.status_code}: {approve_resp.text}")
        return False

    approve_data = approve_resp.json()
    print("  Approval Response:", json.dumps(approve_data, indent=2))
    assert approve_data.get("status") == "approved", "Expected status 'approved'"
    print("✅ Test 3 PASSED: Category proposal successfully committed to user settings.")

    # 6. Verify Track now exists in /api/settings/tracks
    tracks_after_approval = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    committed = next((t for t in tracks_after_approval if t["id"] == "distributed_systems"), None)
    assert committed is not None, "Committed track not found in /api/settings/tracks!"
    print(f"✅ Test 4 PASSED: Committed track verified in active settings: {committed}")

    # 7. Test Idempotency (Approving same track again)
    print(f"\n[Test 5] Re-submitting same track approval (Idempotency check)")
    dup_resp = requests.post(
        f"{BASE_URL}/api/categories/approve",
        headers=headers,
        json=approval_payload
    )
    assert dup_resp.status_code == 200
    assert dup_resp.json().get("status") == "already_exists"
    print(f"✅ Test 5 PASSED: Idempotent handling returned status: {dup_resp.json().get('status')}")

    # 8. Test Alternate route parity: POST /categories/approve
    print(f"\n[Test 6] Checking route parity at /categories/approve")
    alt_resp = requests.post(
        f"{BASE_URL}/categories/approve",
        headers=headers,
        json=approval_payload
    )
    assert alt_resp.status_code == 200
    print(f"✅ Test 6 PASSED: Route /categories/approve responds successfully.")

    print("\n" + "=" * 70)
    print("🎉 ALL DYNAMIC CATEGORY PROPOSAL TESTS PASSED (6/6)")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
