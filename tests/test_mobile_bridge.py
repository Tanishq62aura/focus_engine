"""
tests/test_mobile_bridge.py
Unit and regression tests for Track 3: Mobile Bridge Custom Notification Routing (ntfy.sh).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mobile_bridge import (
    dispatch_session_end,
    dispatch_session_start,
    resolve_ntfy_topic,
    send_ntfy_notification,
)


def test_resolve_ntfy_topic_hierarchy():
    # 1. Explicit topic takes top priority
    res = resolve_ntfy_topic(explicit_topic="override-topic")
    assert res == "override-topic"

    # 2. UserSettings in DB takes second priority
    mock_db = MagicMock()
    mock_settings = MagicMock()
    mock_settings.ntfy_topic = "user-configured-topic"
    mock_db.query().filter().first.return_value = mock_settings

    res_db = resolve_ntfy_topic(db=mock_db, user_id="user-123")
    assert res_db == "user-configured-topic"

    # 3. Environment variable fallback
    with patch.dict("os.environ", {"NTFY_TOPIC": "env-fallback-topic"}):
        res_env = resolve_ntfy_topic(db=None, user_id=None)
        assert res_env == "env-fallback-topic"


def test_resolve_ntfy_topic_empty_fallback():
    # When no topic is supplied and env is empty
    with patch.dict("os.environ", {"NTFY_TOPIC": ""}):
        res = resolve_ntfy_topic(db=None, user_id=None, explicit_topic="")
        assert res is None


def test_send_ntfy_notification_timeout_handled_gracefully():
    # Must return False and not raise an unhandled exception when timeout occurs
    with patch("httpx.Client.post", side_effect=Exception("ReadTimeout")):
        success = send_ntfy_notification(
            topic="test-topic",
            title="Test",
            message="Test timeout",
        )
        assert success is False


def test_send_ntfy_notification_http_error_handled_gracefully():
    # Non-200 responses return False without raising exceptions
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = "Bad Gateway"
    with patch("httpx.Client.post", return_value=mock_resp):
        success = send_ntfy_notification(
            topic="test-topic",
            title="Test",
            message="Test 502",
        )
        assert success is False


def test_send_ntfy_notification_empty_topic():
    # Empty topic immediately returns False without network requests
    assert send_ntfy_notification(topic="", title="Test", message="Test") is False


def test_dispatch_session_lifecycle():
    mock_session = MagicMock()
    mock_session.user_id = "test-user"
    mock_session.target_duration_min = 45
    mock_session.status = "COMPLETED"

    with patch("app.mobile_bridge.send_ntfy_notification", return_value=True) as mock_send:
        # Start dispatch
        started = dispatch_session_start(mock_session, custom_topic="test-channel")
        assert started is True
        assert mock_send.called
        assert mock_send.call_args[1]["topic"] == "test-channel"

        # End dispatch
        ended = dispatch_session_end(mock_session, custom_topic="test-channel")
        assert ended is True
        assert mock_send.call_args[1]["topic"] == "test-channel"


def test_dispatch_session_start_no_topic_skips_cleanly():
    mock_session = MagicMock()
    mock_session.user_id = "test-user"

    with patch("app.mobile_bridge.resolve_ntfy_topic", return_value=None):
        started = dispatch_session_start(mock_session)
        assert started is False
