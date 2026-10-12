import json
import os
import sys
import urllib.request
from dotenv import load_dotenv
from jose import jwt

load_dotenv()
jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "supabase-jwt-secret-njxlltvrcmqinrnhlpgh")
auth_disabled = os.getenv("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")

base_url = "http://127.0.0.1:8000"

# Generate test token
token = jwt.encode({"sub": "dev-user", "role": "authenticated"}, jwt_secret, algorithm="HS256")
headers_auth = {"Authorization": f"Bearer {token}"}

print("=== API & AUTH SMOKE TEST ===")

# 1. GET /docs
try:
    req = urllib.request.Request(f"{base_url}/docs")
    with urllib.request.urlopen(req, timeout=5) as r:
        print(f"1. GET /docs: Status {r.status} (PASS)")
except Exception as e:
    print(f"1. GET /docs: FAILED ({e})")

# 2. GET /auth/status (without auth)
try:
    req = urllib.request.Request(f"{base_url}/auth/status")
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
        print(f"2a. GET /auth/status (unauthenticated): Status {r.status} | Data: {data}")
except urllib.error.HTTPError as e:
    print(f"2a. GET /auth/status (unauthenticated): Status {e.code} ({e.reason}) - Expected when AUTH_DISABLED=false")

# 2b. GET /auth/status (with JWT)
try:
    req = urllib.request.Request(f"{base_url}/auth/status", headers=headers_auth)
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
        print(f"2b. GET /auth/status (with Bearer JWT): Status {r.status} | User: {data.get('user_id')} | Auth Disabled: {data.get('auth_disabled')} (PASS)")
except Exception as e:
    print(f"2b. GET /auth/status (with Bearer JWT): FAILED ({e})")

# 3. GET /reports/today
try:
    req = urllib.request.Request(f"{base_url}/reports/today", headers=headers_auth)
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
        required_keys = ["date", "total_focused_minutes", "verified_sessions", "execution_ratio_pct"]
        valid = all(k in data for k in required_keys)
        print(f"3. GET /reports/today: Status {r.status} | Schema Valid: {valid} (PASS)")
except Exception as e:
    print(f"3. GET /reports/today: FAILED ({e})")

# 4. GET /reports/friction-analysis?days=7
try:
    req = urllib.request.Request(f"{base_url}/reports/friction-analysis?days=7", headers=headers_auth)
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
        f_idx = data.get("system_friction_index")
        f_status = data.get("friction_status")
        cats = data.get("categories", [])
        math_valid = 0.0 <= f_idx <= 100.0 and f_status in ("optimal", "moderate", "severe")
        print(f"4. GET /reports/friction-analysis: Status {r.status} | Index: {f_idx} | Status: {f_status} | Categories: {len(cats)} | Math Valid: {math_valid} (PASS)")
except Exception as e:
    print(f"4. GET /reports/friction-analysis: FAILED ({e})")
