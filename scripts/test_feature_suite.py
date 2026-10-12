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

sys.path.insert(0, ".")

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

# 8. Test Native Registration & Clean-Slate Account Scoping
print("[7] Testing POST /api/auth/signup & Clean-Slate Account Scoping...")
import uuid
from sqlalchemy import text
from app.database import engine

test_user_email = f"flight_test_{uuid.uuid4().hex[:8]}@focusengine.io"
test_user_pass = "SecurePass888!"

r_reg = requests.post(f"{BASE_URL}/api/auth/signup", json={"email": test_user_email, "password": test_user_pass})
assert r_reg.status_code == 201, f"Signup failed: {r_reg.text}"
reg_data = r_reg.json()
new_uid = reg_data["user_id"]
new_token = reg_data["access_token"]
print(f"  [OK] New user registered: {test_user_email} (UUID: {new_uid})")

# Test native login
r_log = requests.post(f"{BASE_URL}/api/auth/login", json={"email": test_user_email, "password": test_user_pass})
assert r_log.status_code == 200, f"Login failed: {r_log.text}"
print("  [OK] Native login confirmed with fresh token")

# Test clean-slate: verify 0 tasks and 0.0 focus minutes
user_headers = {"Authorization": f"Bearer {new_token}"}
r_user_tasks = requests.get(f"{BASE_URL}/tasks", headers=user_headers)
assert len(r_user_tasks.json()) == 0, f"Expected 0 tasks for new user, got: {r_user_tasks.json()}"
print("  [OK] Clean-slate verified: 0 tasks returned for new user")

r_user_rep = requests.get(f"{BASE_URL}/reports/today", headers=user_headers)
assert r_user_rep.json()["total_focused_minutes"] == 0.0, "Expected 0.0 focus min"
print("  [OK] Clean-slate verified: 0.0 focus minutes returned for new user")

# Create task under new user and verify user_id in PostgreSQL
r_new_task = requests.post(f"{BASE_URL}/tasks", headers=user_headers, json={
    "title": "Clean-Slate Account Task",
    "category": "writing",
    "priority": 1,
    "estimated_duration_min": 45,
    "min_goal": 15,
    "target_goal": 45,
    "stretch_goal": 60
})
assert r_new_task.status_code == 201
new_task_id = r_new_task.json()["id"]

# Direct PostgreSQL verification: SELECT user_id FROM tasks WHERE id = ...
with engine.connect() as conn:
    row = conn.execute(text("SELECT user_id FROM tasks WHERE id = :id"), {"id": new_task_id}).fetchone()
    db_user_id = str(row[0])
    assert db_user_id == new_uid, f"Expected user_id {new_uid}, got {db_user_id}"
    print(f"  [OK] PostgreSQL verification passed: tasks.user_id = '{db_user_id}' (matches registered UUID, NOT dev-user)")
    # Cleanup task & user from PostgreSQL
    conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": new_task_id})
    conn.execute(text("DELETE FROM auth.users WHERE id = :uid"), {"uid": new_uid})
    conn.commit()
    print("  [OK] Test user and task cleaned up from PostgreSQL")

# 9. Check Zero Emojis in Frontend
print("[8] Verifying 0 unicode emojis in static/index.html...")
html_text = Path("static/index.html").read_text(encoding="utf-8")
ep = re.compile(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]|[\u25aa-\u25fe]')
emojis = ep.findall(html_text)
assert len(emojis) == 0, f"Found {len(emojis)} emojis: {emojis}"
print(f"  [OK] 0 emojis verified in static/index.html")

print("\n==========================================")
print("ALL VERIFICATION SUITE CHECKS PASSED (100%)")
print("==========================================")
