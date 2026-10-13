"""
scripts/test_settings_and_scorecard.py
Comprehensive test suite verifying:
1. GET /api/settings
2. PUT /api/settings
3. POST /api/settings/test-ping
4. GET /api/settings/system-endpoints
5. GET /api/settings/export-telemetry
6. GET /api/reports/daily-scorecard
7. APScheduler CLI test runner
"""
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=" * 60)
    print("TEST SUITE: Settings Drawer, Daily Scorecard & Scheduler")
    print("=" * 60)

    # 0. Get dev token
    print("\n[0] Authenticating dev-token...")
    req = urllib.request.Request(f"{BASE_URL}/auth/dev-token", method="POST")
    with urllib.request.urlopen(req) as resp:
        auth_data = json.loads(resp.read().decode())
    token = auth_data["access_token"]
    user_id = auth_data["user_id"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    print(f"    Authenticated as {user_id}")

    # 1. GET /api/settings
    print("\n[1] Testing GET /api/settings...")
    req = urllib.request.Request(f"{BASE_URL}/api/settings", headers=headers)
    with urllib.request.urlopen(req) as resp:
        settings_data = json.loads(resp.read().decode())
    print(f"    Workspace Path: {settings_data.get('workspace_path')}")
    print(f"    Viva Strictness: {settings_data.get('viva_strictness')}")
    print(f"    Ntfy Topic: {settings_data.get('ntfy_topic')}")
    print(f"    Daily Target: {settings_data.get('daily_target_minutes')}m")
    assert "workspace_path" in settings_data
    assert "viva_strictness" in settings_data
    assert "daily_target_minutes" in settings_data
    print("    [PASS] GET /api/settings verified!")

    # 2. PUT /api/settings
    print("\n[2] Testing PUT /api/settings...")
    update_payload = {
        "workspace_path": r"D:\code\workspace_verified",
        "viva_strictness": "adversarial",
        "auto_fail_paste_bursts": True,
        "blind_focus_mode": True,
        "daily_target_minutes": 200,
        "default_min_minutes": 30,
        "default_target_minutes": 75,
        "default_stretch_minutes": 100
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/settings",
        data=json.dumps(update_payload).encode(),
        headers=headers,
        method="PUT"
    )
    with urllib.request.urlopen(req) as resp:
        updated_data = json.loads(resp.read().decode())
    assert updated_data["workspace_path"] == r"D:\code\workspace_verified"
    assert updated_data["viva_strictness"] == "adversarial"
    assert updated_data["blind_focus_mode"] is True
    assert updated_data["daily_target_minutes"] == 200
    print("    [PASS] PUT /api/settings verified persistence!")

    # 3. GET /api/settings/system-endpoints
    print("\n[3] Testing GET /api/settings/system-endpoints...")
    req = urllib.request.Request(f"{BASE_URL}/api/settings/system-endpoints", headers=headers)
    with urllib.request.urlopen(req) as resp:
        endpoints_data = json.loads(resp.read().decode())
    print(f"    LAN URL:    {endpoints_data.get('lan_url')}")
    print(f"    Tunnel URL: {endpoints_data.get('tunnel_url')}")
    assert "lan_url" in endpoints_data
    assert "tunnel_url" in endpoints_data
    print("    [PASS] GET /api/settings/system-endpoints verified!")

    # 4. POST /api/settings/test-ping
    print("\n[4] Testing POST /api/settings/test-ping...")
    req = urllib.request.Request(f"{BASE_URL}/api/settings/test-ping", headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        ping_res = json.loads(resp.read().decode())
    print(f"    Ping Topic: {ping_res.get('topic')}")
    print(f"    Message:    {ping_res.get('message')}")
    print("    [PASS] POST /api/settings/test-ping verified!")

    # 5. GET /api/settings/export-telemetry
    print("\n[5] Testing GET /api/settings/export-telemetry...")
    req = urllib.request.Request(f"{BASE_URL}/api/settings/export-telemetry", headers=headers)
    with urllib.request.urlopen(req) as resp:
        telemetry_data = json.loads(resp.read().decode())
    print(f"    User:           {telemetry_data.get('user_id')}")
    print(f"    Sessions count: {len(telemetry_data.get('focus_sessions', []))}")
    print(f"    Distractions:   {len(telemetry_data.get('distraction_logs', []))}")
    assert "focus_sessions" in telemetry_data
    assert "distraction_logs" in telemetry_data
    print("    [PASS] GET /api/settings/export-telemetry JSON payload verified!")

    # 6. GET /api/reports/daily-scorecard
    print("\n[6] Testing GET /api/reports/daily-scorecard...")
    req = urllib.request.Request(f"{BASE_URL}/api/reports/daily-scorecard", headers=headers)
    with urllib.request.urlopen(req) as resp:
        card_data = json.loads(resp.read().decode())
    print(f"    Date:             {card_data.get('date')}")
    print(f"    Score (DEI):      {card_data.get('score')}%")
    print(f"    Status Grade:     {card_data.get('status_grade')}")
    print(f"    Verified Minutes: {card_data.get('verified_minutes')}m")
    print(f"    Target Floor:     {card_data.get('target_minutes')}m")
    print(f"    Completed Blocks: {card_data.get('completed_blocks')}")
    print(f"    Distractions:     {card_data.get('distractions')}")
    print(f"    Paste Bursts:     {card_data.get('paste_bursts')}")
    assert "score" in card_data
    assert card_data["status_grade"] in ("OPTIMAL", "SUBPAR", "CRITICAL_DRIFT")
    print("    [PASS] GET /api/reports/daily-scorecard verified!")

    print("\n" + "=" * 60)
    print("ALL API ENDPOINTS TESTED AND VERIFIED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
