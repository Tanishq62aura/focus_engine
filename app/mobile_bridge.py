"""
app/mobile_bridge.py
Mobile notification & trigger dispatcher using ntfy.sh webhooks.
Integrates with Samsung Modes & Routines / tasker webhooks for DND and grayscale triggers.
"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "focus_engine_alerts").strip()
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def dispatch_session_start(task_title: str, duration_min: int) -> bool:
    """
    Sends a session start webhook to ntfy.sh.
    Triggers DND and grayscale automation on connected mobile devices.
    """
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    message = f"FOCUS_START: Locked in for {duration_min}m on {task_title}"
    headers = {
        "Title": "Focus Block Active",
        "Tags": "lock,rotating_light",
        "Priority": "default",
    }
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            logger.info(
                "📱 Dispatched session start trigger to %s (task: %r, duration: %dm, status: %d)",
                url,
                task_title,
                duration_min,
                response.status,
            )
            return True
    except Exception as e:
        logger.warning(
            "⚠️ Failed to dispatch session start to %s (network drop / offline): %s",
            url,
            e,
        )
        return False


def dispatch_session_end(task_title: str) -> bool:
    """
    Sends a session end webhook to ntfy.sh.
    Restores normal phone mode and disables DND.
    """
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    message = f"FOCUS_END: Block completed for {task_title}"
    headers = {
        "Title": "Focus Block Completed",
        "Tags": "white_check_mark",
        "Priority": "default",
    }
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            logger.info(
                "📱 Dispatched session end trigger to %s (task: %r, status: %d)",
                url,
                task_title,
                response.status,
            )
            return True
    except Exception as e:
        logger.warning(
            "⚠️ Failed to dispatch session end to %s (network drop / offline): %s",
            url,
            e,
        )
        return False
