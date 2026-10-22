"""
tests/test_browser_extension_telemetry.py
Automated test suite for Chromium Manifest V3 Dual-Browser (Chrome & Edge) telemetry subsystem.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import create_access_token
from app.database import SessionLocal
from app.main import app
from app.models import FocusSession, SessionHeartbeat, User

TEST_USER_ID = "extension-test-user"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal(expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def auth_headers():
    token = create_access_token({"sub": TEST_USER_ID, "email": f"{TEST_USER_ID}@focusengine.test"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def active_session(db):
    # Ensure clean slate for test user
    db.query(SessionHeartbeat).filter(
        SessionHeartbeat.session_id.in_(
            db.query(FocusSession.id).filter(FocusSession.user_id == TEST_USER_ID)
        )
    ).delete(synchronize_session=False)
    db.query(FocusSession).filter(FocusSession.user_id == TEST_USER_ID).delete(synchronize_session=False)
    db.commit()

    sess = FocusSession(
        id=str(uuid.uuid4()),
        user_id=TEST_USER_ID,
        status="IN_PROGRESS",
        created_at=datetime.now(timezone.utc),
        heartbeat_violation_count=0,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def test_manifest_v3_structure():
    """Verify Manifest V3 configuration complies with Chrome and Edge specifications."""
    manifest_paths = [
        Path(__file__).resolve().parent.parent / "browser_youtube_watchdog" / "manifest.json",
        Path(__file__).resolve().parent.parent / "extension" / "manifest.json",
    ]

    for p in manifest_paths:
        assert p.exists(), f"Manifest file missing at {p}"
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data.get("manifest_version") == 3
        assert data.get("name") == "Focus Engine Tracker"
        assert data.get("background", {}).get("service_worker") == "background.js"

        perms = set(data.get("permissions", []))
        required_perms = {"alarms", "storage", "tabs", "activeTab", "notifications"}
        assert required_perms.issubset(perms), f"Missing permissions in {p}: {required_perms - perms}"

        hosts = data.get("host_permissions", [])
        assert any("focus-engine.onrender.com" in h for h in hosts)
        assert any("127.0.0.1:8000" in h for h in hosts)
        assert any("localhost:8000" in h for h in hosts)

        content_scripts = data.get("content_scripts", [])
        assert len(content_scripts) >= 1
        assert "content.js" in content_scripts[0].get("js", [])


def test_heartbeat_ingestion_chrome_and_edge_sources(client, auth_headers, active_session, db):
    """Verify heartbeat ingestion with both source='chrome' and source='edge'."""
    # 1. On-task Chrome heartbeat
    payload_chrome = {
        "session_id": active_session.id,
        "source": "chrome",
        "event_type": "tab_active",
        "active_target": "https://docs.python.org/3/library/zoneinfo.html",
        "is_idle": False,
    }
    resp_chrome = client.post("/api/telemetry/heartbeat", json=payload_chrome, headers=auth_headers)
    assert resp_chrome.status_code == 200
    data_chrome = resp_chrome.json()
    assert data_chrome["status"] == "OK"
    assert data_chrome["violation_count"] == 0

    # 2. On-task Edge heartbeat
    payload_edge = {
        "session_id": active_session.id,
        "source": "edge",
        "event_type": "tab_active",
        "active_target": "https://developer.mozilla.org/en-US/docs/Web/API",
        "is_idle": False,
    }
    resp_edge = client.post("/api/telemetry/heartbeat", json=payload_edge, headers=auth_headers)
    assert resp_edge.status_code == 200
    data_edge = resp_edge.json()
    assert data_edge["status"] == "OK"
    assert data_edge["violation_count"] == 0

    # Verify audit records in DB
    records = db.query(SessionHeartbeat).filter(SessionHeartbeat.session_id == active_session.id).all()
    sources = {r.source for r in records}
    assert "chrome" in sources
    assert "edge" in sources


def test_unauthenticated_heartbeat_returns_401(client, active_session):
    """Verify that unauthenticated calls without Bearer token return HTTP 401."""
    payload = {
        "session_id": active_session.id,
        "source": "chrome",
        "event_type": "tab_active",
        "active_target": "https://google.com",
        "is_idle": False,
    }
    resp_no_auth = client.post("/api/telemetry/heartbeat", json=payload)
    assert resp_no_auth.status_code == 401

    resp_bad_auth = client.post(
        "/api/telemetry/heartbeat",
        json=payload,
        headers={"Authorization": "Bearer invalid_expired_garbage_token"},
    )
    assert resp_bad_auth.status_code == 401


def test_three_strike_escalation_youtube_and_reddit(client, auth_headers, active_session, db):
    """
    Test 3-strike escalation on distraction domains (youtube.com, reddit.com)
    transitioning from WARNING to ABORTED (or SESSION_TERMINATED).
    """
    # Strike 1: Off-task target on youtube.com with Edge
    p1 = {
        "session_id": active_session.id,
        "source": "edge",
        "event_type": "tab_active",
        "active_target": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "is_idle": False,
    }
    r1 = client.post("/api/telemetry/heartbeat", json=p1, headers=auth_headers)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["status"] == "WARNING"
    assert d1["violation_count"] == 1

    # Strike 2: Off-task target on reddit.com with Chrome
    p2 = {
        "session_id": active_session.id,
        "source": "chrome",
        "event_type": "tab_active",
        "active_target": "https://reddit.com/r/all",
        "is_idle": False,
    }
    r2 = client.post("/api/telemetry/heartbeat", json=p2, headers=auth_headers)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["status"] == "WARNING"
    assert d2["violation_count"] == 2

    # Strike 3: Off-task target on youtube.com with Edge -> Auto-abort session
    p3 = {
        "session_id": active_session.id,
        "source": "edge",
        "event_type": "tab_active",
        "active_target": "https://youtube.com/shorts/funny-clips",
        "is_idle": False,
    }
    r3 = client.post("/api/telemetry/heartbeat", json=p3, headers=auth_headers)
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["status"] in ("SESSION_TERMINATED", "ABORTED")
    assert d3["violation_count"] == 3

    # Verify session transitioned to ABORTED in database
    db.refresh(active_session)
    assert active_session.status == "ABORTED"
    assert active_session.ended_at is not None
    assert "Auto-Aborted" in (active_session.notes or "")


def test_session_not_found_returns_404(client, auth_headers):
    """Verify that a non-existent or concluded session returns HTTP 404."""
    payload = {
        "session_id": str(uuid.uuid4()),
        "source": "edge",
        "event_type": "tab_active",
        "active_target": "https://github.com",
        "is_idle": False,
    }
    resp = client.post("/api/telemetry/heartbeat", json=payload, headers=auth_headers)
    assert resp.status_code == 404


def test_content_script_no_alarms_reference_and_background_message_handling():
    """Verify content.js contains zero references to chrome.alarms and background.js handles relay messages."""
    content_paths = [
        Path(__file__).resolve().parent.parent / "browser_youtube_watchdog" / "content.js",
        Path(__file__).resolve().parent.parent / "extension" / "content.js",
    ]
    background_paths = [
        Path(__file__).resolve().parent.parent / "browser_youtube_watchdog" / "background.js",
        Path(__file__).resolve().parent.parent / "extension" / "background.js",
    ]

    for cp in content_paths:
        assert cp.exists(), f"File missing: {cp}"
        code = cp.read_text(encoding="utf-8")
        assert "chrome.alarms" not in code, f"Forbidden chrome.alarms call found in {cp}"
        assert "chrome.runtime.sendMessage" in code, f"Missing message relay bridge in {cp}"
        assert "FOCUS_ENGINE_SYNC" in code
        assert "FOCUS_ENGINE_CLEAR" in code

    for bp in background_paths:
        assert bp.exists(), f"File missing: {bp}"
        code = bp.read_text(encoding="utf-8")
        assert "chrome.runtime.onMessage.addListener" in code
        assert "FOCUS_ENGINE_SYNC" in code
        assert "FOCUS_ENGINE_CLEAR" in code
        assert "chrome.alarms.create" in code
        assert "chrome.alarms.clear" in code

