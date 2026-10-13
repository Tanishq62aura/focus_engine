"""
scripts/test_mobile_manifest.py
Automated verification for Capacitor Mobile Shell & OTA Manifest Pipeline:
1. Validates GET /api/mobile/manifest schema adherence and SHA-256 integrity.
2. Validates delta comparison with current_build query parameter:
   - current_build < latest -> update_available == True
   - current_build == latest -> update_available == False
   - current_build > latest -> update_available == False
3. Validates route parity on /mobile/manifest.
"""
import hashlib
import json
import sys
from pathlib import Path
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=" * 70)
    print("📱 FOCUS ENGINE: MOBILE OTA UPDATE MANIFEST VERIFICATION")
    print("=" * 70)

    # 1. Test Base Manifest Endpoint
    print("\n[Test 1] Querying GET /api/mobile/manifest without parameters...")
    resp = requests.get(f"{BASE_URL}/api/mobile/manifest")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    manifest = resp.json()
    print("  Response Body:", json.dumps(manifest, indent=2))

    # Schema adherence
    assert "latest_version" in manifest, "Missing latest_version"
    assert "build_number" in manifest, "Missing build_number"
    assert "checksum_sha256" in manifest, "Missing checksum_sha256"
    assert "bundle_url" in manifest, "Missing bundle_url"
    assert "mandatory_update" in manifest, "Missing mandatory_update"
    assert "release_notes" in manifest, "Missing release_notes"
    assert "update_available" in manifest, "Missing update_available"

    assert isinstance(manifest["latest_version"], str) and len(manifest["latest_version"]) > 0
    assert isinstance(manifest["build_number"], int) and manifest["build_number"] > 0
    assert isinstance(manifest["checksum_sha256"], str) and len(manifest["checksum_sha256"]) == 64
    assert isinstance(manifest["update_available"], bool)
    print("✅ [Test 1] PASSED: Manifest structure strictly adheres to MobileManifestResponse schema.")

    # 2. Checksum Verification against physical assets
    print("\n[Test 2] Verifying SHA-256 Checksum against static/index.html...")
    index_file = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if index_file.exists():
        hasher = hashlib.sha256()
        with open(index_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        expected_sha = hasher.hexdigest()
        assert manifest["checksum_sha256"] == expected_sha, (
            f"Checksum mismatch! Server: {manifest['checksum_sha256']}, Local: {expected_sha}"
        )
        print(f"✅ [Test 2] PASSED: Checksum verified: {expected_sha[:16]}...")
    else:
        print("⚠️ static/index.html not found relative to script, skipping file hash comparison.")

    # 3. Delta Comparison Tests with current_build
    latest_build = manifest["build_number"]

    # Older build -> update must be available
    older_build = latest_build - 10
    print(f"\n[Test 3A] Testing Delta: Older client (current_build={older_build} < {latest_build})...")
    resp_older = requests.get(f"{BASE_URL}/api/mobile/manifest?current_build={older_build}")
    assert resp_older.status_code == 200
    older_manifest = resp_older.json()
    assert older_manifest["update_available"] is True, f"Expected update_available=True for build {older_build}"
    print("✅ [Test 3A] PASSED: Outdated client correctly triggered update_available=True.")

    # Up-to-date build -> update must not be available
    print(f"\n[Test 3B] Testing Delta: Up-to-date client (current_build={latest_build})...")
    resp_current = requests.get(f"{BASE_URL}/api/mobile/manifest?current_build={latest_build}")
    assert resp_current.status_code == 200
    current_manifest = resp_current.json()
    assert current_manifest["update_available"] is False, f"Expected update_available=False for build {latest_build}"
    print("✅ [Test 3B] PASSED: Up-to-date client received update_available=False.")

    # Future / Canary build -> update must not be available
    future_build = latest_build + 10
    print(f"\n[Test 3C] Testing Delta: Future/canary client (current_build={future_build})...")
    resp_future = requests.get(f"{BASE_URL}/api/mobile/manifest?current_build={future_build}")
    assert resp_future.status_code == 200
    future_manifest = resp_future.json()
    assert future_manifest["update_available"] is False, f"Expected update_available=False for build {future_build}"
    print("✅ [Test 3C] PASSED: Canary build received update_available=False.")

    # 4. Route Parity Check on /mobile/manifest
    print("\n[Test 4] Testing Route Parity on /mobile/manifest...")
    alt_resp = requests.get(f"{BASE_URL}/mobile/manifest?current_build={older_build}")
    assert alt_resp.status_code == 200, f"Expected 200 on /mobile/manifest, got {alt_resp.status_code}"
    assert alt_resp.json()["update_available"] is True
    print("✅ [Test 4] PASSED: Route /mobile/manifest operates with complete parity.")

    print("\n" + "=" * 70)
    print("🎉 ALL MOBILE OTA MANIFEST TESTS PASSED (100% SUCCESS)")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
