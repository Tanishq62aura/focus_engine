"""
app/mobile_bridge.py
Mobile notification & trigger dispatcher using ntfy.sh webhooks.
Integrates with Samsung Modes & Routines / tasker webhooks for DND and grayscale triggers.
"""
from __future__ import annotations

import json
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


def dispatch_peripheral_lockdown(track: str = "critical") -> bool:
    """
    Dispatches immediate high-priority alert via ntfy.sh for physical phone isolation.
    Tags: warning,no_entry
    Message: "CRITICAL TRACK DETECTED: Physical phone isolation required (>3m boundary)."
    """
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    message = "CRITICAL TRACK DETECTED: Physical phone isolation required (>3m boundary)."
    headers = {
        "Title": f"Peripheral Lockdown ({track.upper()})",
        "Tags": "warning,no_entry",
        "Priority": "urgent",
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
                "🚨 Dispatched peripheral lockdown to %s (track: %r, status: %d)",
                url,
                track,
                response.status,
            )
            return True
    except Exception as e:
        logger.warning(
            "⚠️ Failed to dispatch peripheral lockdown to %s: %s",
            url,
            e,
        )
        return False


def dispatch_tunnel_online(tunnel_url: str, topic: str | None = None) -> bool:
    """
    Sends an HTTP POST to ntfy.sh with the fresh Cloudflare Tunnel URL.
    Title: "🚀 Focus Engine Online"
    Body: "Active URL: <url>"
    Header/JSON Click: <url> so clicking on phone notification opens the live app immediately.
    """
    target_topics = []
    if topic:
        target_topics.append(topic.strip())
    else:
        if NTFY_TOPIC:
            target_topics.append(NTFY_TOPIC)
        if "focus_engine_ntfy" not in target_topics:
            target_topics.append("focus_engine_ntfy")

    success = False
    for t in target_topics:
        payload = {
            "topic": t,
            "title": "🚀 Focus Engine Online",
            "message": f"Active URL: {tunnel_url}",
            "click": tunnel_url,
            "tags": ["rocket", "link"],
            "priority": 4,
        }
        try:
            req = urllib.request.Request(
                f"{NTFY_SERVER}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                logger.info("🚀 Dispatched tunnel online alert to %s/%s -> %s (status %d)", NTFY_SERVER, t, tunnel_url, response.status)
                success = True
        except Exception as e:
            logger.warning("⚠️ Failed to dispatch tunnel online alert to %s: %s", t, e)

    return success
