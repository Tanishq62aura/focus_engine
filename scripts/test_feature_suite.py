"""
scripts/test_feature_suite.py
Automated end-to-end verification for:
1. Dynamic Study Tracks API (GET, POST, DELETE)
2. Task Creation with Custom Track
3. Embedded AI Support Chat
4. Zero Emojis in Frontend
"""
import requests
import re
import sys
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

# 1. Dev Auth
print("[1] Obtaining dev auth token...")
auth_res = requests.post(f"{BASE_URL}/auth/dev-token")
assert auth_res.status_code == 200, f"Failed dev token: {auth_res.text}"
token = auth_res.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("  [OK] Dev auth token obtained")

# 2. Get Tracks
print("[2] Testing GET /api/settings/tracks...")
r_tracks = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers)
assert r_tracks.status_code == 200, f"Failed GET tracks: {r_tracks.text}"
tracks = r_tracks.json()
print(f"  [OK] Found {len(tracks)} tracks: {[t['id'] for t in tracks]}")
assert any(t["id"] == "coding" for t in tracks), "Default coding track missing"

# 3. Add Custom Non-Coding Track
print("[3] Testing POST /api/settings/tracks with custom track 'design'...")
custom_track = {
    "id": "design",
    "label": "UI & Systems Design",
    "default_duration_min": 45,
    "requires_code_watchdog": False,
    "friction_threshold": 55,
    "verification_prompt_override": "Evaluate visual hierarchy, typography, and contrast fidelity."
}
r_add = requests.post(f"{BASE_URL}/api/settings/tracks", headers=headers, json=custom_track)
assert r_add.status_code == 201, f"Failed POST track: {r_add.text}"
print("  [OK] Track 'design' created:", r_add.json()["label"])

# 4. Verify Track shows up in GET
r_tracks2 = requests.get(f"{BASE_URL}/api/settings/tracks", headers=headers)
tracks2 = r_tracks2.json()
assert any(t["id"] == "design" for t in tracks2), "Custom track 'design' not returned in tracks list"
print("  [OK] Custom track verified in active tracks list")

# 5. Create Task with Custom Track
print("[4] Creating task with custom category 'design'...")
task_payload = {
    "title": "Design Obsidian HUD Layout",
    "category": "design",
    "priority": 2,
    "estimated_duration_min": 45,
    "min_goal": 20,
    "target_goal": 45,
    "stretch_goal": 60
}
r_task = requests.post(f"{BASE_URL}/tasks", headers=headers, json=task_payload)
assert r_task.status_code in (200, 201), f"Failed task creation with custom category: {r_task.text}"
task_data = r_task.json()
print(f"  [OK] Task created successfully with id {task_data['id']} and category '{task_data['category']}'")

# Clean up created task
requests.delete(f"{BASE_URL}/tasks/{task_data['id']}", headers=headers)

# 6. Delete Custom Track
print("[5] Testing DELETE /api/settings/tracks/design...")
r_del = requests.delete(f"{BASE_URL}/api/settings/tracks/design", headers=headers)
assert r_del.status_code == 200, f"Failed DELETE track: {r_del.text}"
print("  [OK] Track 'design' deleted cleanly")

# 7. Test AI Support Chat
print("[6] Testing POST /api/support/chat...")
support_queries = [
    "How does viva defense judge my research notes?",
    "Help me break down this reading goal",
    "How do I beat initiation resistance right now?"
]
for q in support_queries:
    r_chat = requests.post(f"{BASE_URL}/api/support/chat", headers=headers, json={"message": q})
    assert r_chat.status_code == 200, f"Failed support chat: {r_chat.text}"
    reply = r_chat.json()["reply"]
    print(f"  [OK] Query: '{q}'\n    Reply: {reply[:120]}...\n")

# 8. Check Zero Emojis in Frontend
print("[7] Verifying 0 unicode emojis in static/index.html...")
html_text = Path("static/index.html").read_text(encoding="utf-8")
ep = re.compile(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]|[\u25aa-\u25fe]')
emojis = ep.findall(html_text)
assert len(emojis) == 0, f"Found {len(emojis)} emojis: {emojis}"
print(f"  [OK] 0 emojis verified in static/index.html")

print("\n==========================================")
print("ALL VERIFICATION SUITE CHECKS PASSED (100%)")
print("==========================================")
