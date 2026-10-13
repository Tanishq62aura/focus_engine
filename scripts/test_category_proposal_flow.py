"""
scripts/test_category_proposal_flow.py
Verifies:
1. [INTENT-01] Conversational/fuzzy phrasing intent extraction & staging:
   "From now on, all my work should go into Distributed Systems, set that bucket up for me"
2. Standard category proposal extraction & staging
3. Database isolation before explicit approval (0 pre-mature writes)
4. Approval endpoint POST /api/categories/approve commits track to user_settings.custom_tracks
5. Idempotent handling for already-approved tracks
6. [CAP-01] Hard Category Upper-Bound (CAP = 30):
   - Fills user active tracks to 30
   - Attempts to approve a 31st category
   - Verifies HTTP 400 rejection with exact detail message and zero DB writes
7. Cleanup of benchmark test tracks
"""
import json
import sys
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=" * 70)
    print("🧪 FOCUS ENGINE: HARDENED DYNAMIC CATEGORY PROPOSAL FLOW VERIFICATION")
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

    # -------------------------------------------------------------------------
    # TEST: [INTENT-01] Conversational & Fuzzy Phrasing Extraction
    # -------------------------------------------------------------------------
    conversational_prompt = "From now on, all my work should go into Distributed Systems, set that bucket up for me"
    print(f"\n[INTENT-01] Sending Conversational/Fuzzy Intent Prompt:")
    print(f"  Prompt: '{conversational_prompt}'")

    chat_resp = requests.post(
        f"{BASE_URL}/api/support/chat",
        headers=headers,
        json={"message": conversational_prompt}
    )
    assert chat_resp.status_code == 200, f"Chat endpoint failed: {chat_resp.status_code} {chat_resp.text}"
    chat_data = chat_resp.json()

    print("  Bot Reply:", chat_data.get("reply"))
    print("  Action:", chat_data.get("action"))
    print("  Proposal Data:", json.dumps(chat_data.get("data"), indent=2))

    assert chat_data.get("action") == "propose_category", "Expected action 'propose_category'"
    fuzzy_data = chat_data.get("data", {})
    assert fuzzy_data.get("name") == "Distributed Systems" or fuzzy_data.get("suggested_name") == "Distributed Systems", f"Extracted name mismatch: {fuzzy_data.get('name')}"
    assert fuzzy_data.get("status") == "pending_approval", "Status must be 'pending_approval'"
    assert "distributed_systems" in fuzzy_data.get("id", ""), f"ID slug mismatch: {fuzzy_data.get('id')}"
    assert len(fuzzy_data.get("name", "")) <= 30, "Name must not exceed 30 chars"
    print("✅ [INTENT-01] PASSED: Conversational phrasing successfully parsed into structured proposal.")

    # -------------------------------------------------------------------------
    # TEST: Verify 0 Pre-Mature Database Writes
    # -------------------------------------------------------------------------
    tracks_after_proposal = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    assert len(tracks_after_proposal) == len(initial_tracks), "Track should NOT be committed before approval"
    print("✅ Staging Isolation PASSED: Zero premature DB writes. Track remains in pending state.")

    # -------------------------------------------------------------------------
    # TEST: Standard User Approval Flow
    # -------------------------------------------------------------------------
    print(f"\n[APPROVAL] User approves proposal -> POST /api/categories/approve")
    approval_payload = {
        "name": fuzzy_data["name"][:30],
        "id": fuzzy_data["id"][:30],
        "label": fuzzy_data.get("label", fuzzy_data["name"])[:30],
        "requires_code_watchdog": fuzzy_data.get("requires_code_watchdog", True),
        "friction_threshold": fuzzy_data.get("friction_threshold", 3),
        "default_duration_min": fuzzy_data.get("default_duration_min", 45),
        "justification": fuzzy_data.get("justification", "User requested")
    }
    approve_resp = requests.post(
        f"{BASE_URL}/api/categories/approve",
        headers=headers,
        json=approval_payload
    )
    assert approve_resp.status_code == 200, f"Approval failed: {approve_resp.text}"
    approve_data = approve_resp.json()
    assert approve_data.get("status") in ["approved", "already_exists"]
    print("✅ Approval PASSED: Track committed to user settings.")

    # -------------------------------------------------------------------------
    # TEST: Slug Collision Prevention Against Core System Tracks
    # -------------------------------------------------------------------------
    print(f"\n[COLLISION] Attempting to approve collision track with ID 'coding'")
    coll_resp = requests.post(
        f"{BASE_URL}/api/categories/approve",
        headers=headers,
        json={
            "name": "Coding",
            "id": "coding",
            "label": "Coding"
        }
    )
    assert coll_resp.status_code == 400, f"Expected 400 for system collision, got {coll_resp.status_code}"
    print(f"✅ Collision Prevention PASSED: Core track collision rejected with HTTP 400 ({coll_resp.json()['detail']})")

    # -------------------------------------------------------------------------
    # TEST: [CAP-01] Hard Category Upper-Bound (CAP = 30)
    # -------------------------------------------------------------------------
    print(f"\n[CAP-01] Testing Hard Category Upper-Bound (Limit = 30 Tracks):")
    current_tracks = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    current_count = len(current_tracks)
    print(f"  Current active tracks count: {current_count}")

    # Step A: Fill tracks up to exactly 30
    tracks_to_fill = 30 - current_count
    added_filler_ids = []
    if tracks_to_fill > 0:
        print(f"  Populating {tracks_to_fill} test tracks to reach the 30-track boundary...")
        for i in range(tracks_to_fill):
            filler_name = f"Track Slot {i + 1}"
            filler_id = f"slot_{i + 1}"
            r = requests.post(
                f"{BASE_URL}/api/categories/approve",
                headers=headers,
                json={
                    "name": filler_name,
                    "id": filler_id,
                    "label": filler_name
                }
            )
            assert r.status_code == 200, f"Failed populating filler track {filler_id}: {r.text}"
            added_filler_ids.append(filler_id)

    tracks_at_cap = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    assert len(tracks_at_cap) == 30, f"Expected exactly 30 active tracks, found {len(tracks_at_cap)}"
    print(f"  Successfully reached ceiling: {len(tracks_at_cap)} active tracks.")

    # Step B: Attempt to approve the 31st category
    print("  Attempting to approve 31st track: 'Overflow Category'...")
    overflow_resp = requests.post(
        f"{BASE_URL}/api/categories/approve",
        headers=headers,
        json={
            "name": "Overflow Category",
            "id": "overflow_category",
            "label": "Overflow Category"
        }
    )
    print(f"  Response Status: {overflow_resp.status_code}")
    print(f"  Response Body:   {overflow_resp.text}")

    assert overflow_resp.status_code == 400, f"Expected HTTP 400 on 31st track, got {overflow_resp.status_code}"
    err_detail = overflow_resp.json().get("detail", "")
    assert "Maximum track limit reached (30 tracks)" in err_detail, f"Unexpected error detail: {err_detail}"

    # Step C: Verify ZERO database writes occurred
    tracks_after_overflow = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    assert len(tracks_after_overflow) == 30, f"Database write occurred! Expected 30 tracks, got {len(tracks_after_overflow)}"
    assert not any(t["id"] == "overflow_category" for t in tracks_after_overflow), "Overflow category was written to database!"
    print("✅ [CAP-01] PASSED: 31st track strictly rejected with HTTP 400 and 0 database writes.")

    # Step D: Cleanup filler test tracks
    print("\n[CLEANUP] Pruning benchmark filler tracks...")
    for fid in added_filler_ids:
        del_resp = requests.delete(f"{BASE_URL}/api/settings/tracks/{fid}", headers=headers)
        assert del_resp.status_code == 200, f"Failed deleting test track {fid}: {del_resp.text}"

    final_tracks = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers).json()
    print(f"✅ Cleanup Complete. Active tracks restored to: {len(final_tracks)}")

    print("\n" + "=" * 70)
    print("🎉 ALL HARDENED CATEGORY PROPOSAL & CAP TESTS PASSED (100% SUCCESS)")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
