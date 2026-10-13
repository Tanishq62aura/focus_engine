import json
import os
import time
import urllib.request

BASE = os.getenv("FOCUS_API_BASE", "http://127.0.0.1:8000")
AUTH_TOKEN = os.getenv("FOCUS_AUTH_TOKEN", "").strip()
SEP  = "-" * 62

def post(path, body):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    req  = urllib.request.Request(
        BASE + path, data=data,
        headers=headers,
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())

def get(path):
    headers = {}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    req = urllib.request.Request(BASE + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())

def banner(step, title):
    print(f"\n{SEP}")
    print(f"  STEP {step} – {title}")
    print(SEP)

def main():
    global AUTH_TOKEN
    if not AUTH_TOKEN:
        try:
            req = urllib.request.Request(f"{BASE}/auth/dev-token", data=b"", headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                AUTH_TOKEN = data.get("access_token", "")
        except Exception as e:
            pass

    # ── STEP 1: Create task ────────────────────────────────────────────
    banner(1, "Create Task")
    task = post("/tasks", {
        "title": "Binary Search Trees",
        "category": "coding",
        "priority": 1,
        "estimated_duration_min": 60,
        "min_goal": 25, "target_goal": 50, "stretch_goal": 90,
    })
    tid = task["id"]
    print(f"  [PASS] Task created")
    print(f"         id       = {tid}")
    print(f"         title    = {task['title']}")
    print(f"         goals    = {task['min_goal']} / {task['target_goal']} / {task['stretch_goal']} min")

    # ── STEP 2: Start session ──────────────────────────────────────────
    banner(2, "Start Focus Session")
    session = post("/sessions/start", {"task_id": tid})
    sid = session["id"]
    print(f"  [PASS] Session started")
    print(f"         id             = {sid}")
    print(f"         start_latency  = {session['start_latency_min']} min")
    print(f"         status         = {session['verification_status']}")

    # ── STEP 3: Log distractions ───────────────────────────────────────
    banner(3, "Log Distractions (1x phone, 2x youtube, 1x overthinking)")
    for dtype, count in [("phone", 1), ("youtube", 2), ("overthinking", 1)]:
        for _ in range(count):
            post("/distractions", {"session_id": sid, "interruption_type": dtype})
        print(f"  [LOG]  {count}x {dtype}")

    # ── STEP 4: YouTube audit – OFF-TASK ──────────────────────────────
    banner(4, "YouTube Audit – OFF-TASK video")
    r = post("/sessions/active/youtube-audit", {
        "video_title": "Drake - Gods Plan Official Music Video",
        "channel_name": "Drake",
        "url": "https://youtube.com/watch?v=xyz",
    })
    flag = "PASS" if r["is_distraction"] and r["status"] == "flagged" else "FAIL"
    print(f"  [{flag}] status={r['status']}  is_distraction={r['is_distraction']}")
    print(f"         msg: {r.get('message', '')}")

    # ── STEP 5: YouTube audit – ON-TASK ───────────────────────────────
    banner(5, "YouTube Audit – ON-TASK video")
    r2 = post("/sessions/active/youtube-audit", {
        "video_title": "Binary Search Tree Full DSA Tutorial - NeetCode",
        "channel_name": "NeetCode",
        "url": "https://youtube.com/watch?v=abc",
    })
    flag2 = "PASS" if not r2["is_distraction"] and r2["status"] == "approved" else "FAIL"
    print(f"  [{flag2}] status={r2['status']}  is_distraction={r2['is_distraction']}")
    print(f"         msg: {r2.get('message', '')}")

    # ── STEP 6: End session ────────────────────────────────────────────
    banner(6, "End Session")
    time.sleep(3)
    ended = post(f"/sessions/{sid}/end", {"notes": "Practised iterative BST traversal"})
    print(f"  [PASS] Session ended")
    print(f"         focused        = {ended['actual_focused_seconds']}s")
    print(f"         status         = {ended['verification_status']}")

    # ── STEP 7: Request viva ───────────────────────────────────────────
    banner(7, "Request Viva Question")
    proof = (
        "def binary_search(arr, target):\n"
        "    left, right = 0, len(arr) - 1\n"
        "    while left <= right:\n"
        "        mid = (left + right) // 2\n"
        "        if arr[mid] == target:\n"
        "            return mid\n"
        "        elif arr[mid] < target:\n"
        "            left = mid + 1\n"
        "        else:\n"
        "            right = mid - 1\n"
        "    return -1\n"
    )
    viva = post(f"/sessions/{sid}/request-viva", {"proof_text": proof})
    print(f"  [PASS] Viva question generated:")
    print(f"\n  >>> {viva['viva_question']}\n")

    # ── STEP 8: Submit viva answer ─────────────────────────────────────
    banner(8, "Submit Viva Answer")
    answer = (
        "Time complexity is O(log n) because we halve the search space each iteration. "
        "Space complexity is O(1) since we use iterative approach with only pointer variables. "
        "Edge cases: empty array returns -1, single element works correctly, "
        "duplicates return any valid matching index. "
        "Overflow risk on mid: use mid = left + (right - left) // 2 to be safe."
    )
    result = post(f"/sessions/{sid}/submit-viva", {"answer": answer})
    verdict  = result["verification_status"]
    emoji    = "PASS" if verdict == "verified" else "FAIL"
    print(f"  [{emoji}] Verdict: {verdict.upper()}")
    print(f"\n  Feedback:\n  {result['viva_feedback']}\n")

    # ── STEP 9: Today report ───────────────────────────────────────────
    banner(9, "Today Report")
    report = get("/reports/today")
    print(f"  Date                 : {report['date']}")
    print(f"  Total focused        : {report['total_focused_minutes']} min")
    print(f"  Verified sessions    : {report['verified_sessions']}")
    print(f"  Rejected sessions    : {report['rejected_sessions']}")
    print(f"  Avg start latency    : {report['average_start_latency_min']} min")
    print(f"  Total distractions   : {report['total_distractions']}")
    print(f"  Breakdown            : {report['distraction_breakdown']}")
    print(f"  Execution ratio      : {report['execution_ratio_pct']}%")

    print(f"\n{SEP}")
    print("  ALL 9 STEPS COMPLETE")
    print(SEP)


if __name__ == "__main__":
    main()
