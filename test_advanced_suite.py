"""
test_advanced_suite.py
Verification test suite for Focus Engine Advanced Workflow Suite:
1. Circadian Resistance & Excuse Heatmap API (GET /api/reports/circadian-matrix)
2. Streak Counter & Strict Stakes Engine (GET /api/reports/streak)
3. Parked Thoughts Distraction Scratchpad (POST & GET /api/sessions/{id}/park-thought)
4. Zen Kiosk Emergency Abort & Streak Break (POST /api/sessions/{id}/emergency-abort)
5. Static HUD DOM elements verification
"""
import sys
import time
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("==================================================")
    print("[*] FOCUS ENGINE ADVANCED WORKFLOW SUITE TEST RUNNER")
    print("==================================================")

    # 1. Obtain Dev Token
    res = requests.post(f"{BASE_URL}/auth/dev-token")
    assert res.status_code == 200, f"Dev token failed: {res.text}"
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("✅ 1. Auth & Dev Token initialized successfully")

    # 2. Test Circadian Resistance Heatmap Matrix
    res = requests.get(f"{BASE_URL}/api/reports/circadian-matrix", headers=headers)
    assert res.status_code == 200, f"Circadian matrix failed: {res.text}"
    data = res.json()
    assert "matrix" in data, "Matrix missing in response"
    assert len(data["matrix"]) == 7, f"Expected 7 days, got {len(data['matrix'])}"
    assert len(data["matrix"][0]) == 24, f"Expected 24 hours, got {len(data['matrix'][0])}"
    print(f"✅ 2. Circadian Resistance Matrix API: 7x24 grid verified (Analyzed: {data['total_sessions_analyzed']} sessions, {data['total_interruptions_analyzed']} distractions)")

    # 3. Test Streak Counter & Strict Stakes Engine
    res = requests.get(f"{BASE_URL}/api/reports/streak", headers=headers)
    assert res.status_code == 200, f"Streak report failed: {res.text}"
    streak_data = res.json()
    assert "current_streak_days" in streak_data
    assert "daily_floor_target" in streak_data
    print(f"✅ 3. Streak Counter API: Streak={streak_data['current_streak_days']} days, Daily Floor={streak_data['daily_floor_target']} blocks, Status={streak_data['status']}")

    # 4. Create Task & Start Session
    task_res = requests.post(
        f"{BASE_URL}/tasks",
        headers=headers,
        json={
            "title": f"Advanced Suite Verification Task {int(time.time())}",
            "category": "theory",
            "priority": 1,
            "estimated_duration_min": 45,
            "min_goal": 15,
            "target_goal": 30,
            "stretch_goal": 45,
        },
    )
    assert task_res.status_code == 201, f"Task create failed: {task_res.text}"
    task_id = task_res.json()["id"]

    sess_res = requests.post(
        f"{BASE_URL}/sessions/start",
        headers=headers,
        json={"task_id": task_id, "is_five_min_intervention": False},
    )
    assert sess_res.status_code == 201, f"Session start failed: {sess_res.text}"
    session_id = sess_res.json()["id"]
    print(f"✅ 4. Active Session started: {session_id}")

    # 5. Test Parked Thoughts Ingress
    thought_res = requests.post(
        f"{BASE_URL}/api/sessions/{session_id}/park-thought",
        headers=headers,
        json={"content": "Check dynamic study tracks documentation", "urgency": "medium"},
    )
    assert thought_res.status_code == 200, f"Park thought failed: {thought_res.text}"
    thought_data = thought_res.json()
    assert thought_data["content"] == "Check dynamic study tracks documentation"
    assert thought_data["urgency"] == "medium"
    assert thought_data["session_id"] == session_id
    print(f"✅ 5. Parked Thought saved: id={thought_data['id']}, content='{thought_data['content']}'")

    # 6. Test Get Parked Thoughts for Session
    get_thoughts_res = requests.get(
        f"{BASE_URL}/api/sessions/{session_id}/parked-thoughts",
        headers=headers,
    )
    assert get_thoughts_res.status_code == 200, f"Get parked thoughts failed: {get_thoughts_res.text}"
    thoughts_list = get_thoughts_res.json()
    assert len(thoughts_list) >= 1, "Expected at least 1 parked thought"
    print(f"✅ 6. Retrieved {len(thoughts_list)} parked thought(s) for session review drawer")

    # 7. Test Emergency Abort from Zen Kiosk Mode (< 10m floor triggers streak break & distraction audit)
    abort_res = requests.post(
        f"{BASE_URL}/api/sessions/{session_id}/emergency-abort",
        headers=headers,
    )
    assert abort_res.status_code == 200, f"Emergency abort failed: {abort_res.text}"
    abort_data = abort_res.json()
    assert abort_data["success"] is True
    assert "streak_broken" in abort_data
    print(f"✅ 7. Zen Kiosk Emergency Abort: Duration={abort_data['duration_seconds']}s, Streak broken={abort_data['streak_broken']}")

    # 8. Test Frontend Static Assets & HUD Elements
    html_res = requests.get(f"{BASE_URL}/")
    assert html_res.status_code == 200, "Frontend index.html failed to serve"
    html = html_res.text
    required_dom_ids = [
        "audio-soundscape-bar",
        "audio-channel-select",
        "audio-volume-slider",
        "btn-audio-mute",
        "streak-badge",
        "streak-count-label",
        "btn-zen-mode",
        "btn-park-thought-hud",
        "zen-kiosk-view",
        "zen-timer-display",
        "zen-exit-btn",
        "zen-exit-fill",
        "circadian-heatmap-rows",
        "modal-parked-thoughts",
        "parked-thought-input",
        "viva-parked-thoughts-container",
    ]
    for dom_id in required_dom_ids:
        assert f'id="{dom_id}"' in html, f"Missing required DOM element: id='{dom_id}'"
    print(f"✅ 8. Verified all {len(required_dom_ids)} HUD DOM elements present in index.html")

    print("==================================================")
    print("🎉 ALL 5 ADVANCED WORKFLOW SUITE MODULES VERIFIED!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
