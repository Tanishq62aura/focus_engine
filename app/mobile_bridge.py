"""
app/mobile_bridge.py
Mobile notification & trigger dispatcher using ntfy.sh webhooks.
Integrates with Samsung Modes & Routines / Tasker webhooks for DND and grayscale triggers.
Supports user-scoped ntfy topic resolution and resilient non-blocking webhook dispatch.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models import UserSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", os.getenv("NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
DEFAULT_NTFY_TOPIC = os.getenv("NTFY_TOPIC", "focus_engine_ntfy").strip()
NTFY_SERVER = NTFY_BASE_URL
NTFY_TOPIC = DEFAULT_NTFY_TOPIC


def resolve_ntfy_topic(
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
    explicit_topic: Optional[str] = None
) -> Optional[str]:
    """
    Resolution hierarchy:
    1. Explicitly passed topic
    2. UserSettings from DB for user_id
    3. Global environment variable NTFY_TOPIC
    """
    if explicit_topic and explicit_topic.strip():
        return explicit_topic.strip()

    if db and user_id:
        try:
            settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
            if settings and getattr(settings, "ntfy_topic", None) and settings.ntfy_topic.strip():
                return settings.ntfy_topic.strip()
        except Exception as e:
            logger.warning(f"[MobileBridge] Failed to resolve user settings for topic: {e}")

    env_topic = os.getenv("NTFY_TOPIC")
    if env_topic and env_topic.strip():
        return env_topic.strip()

    return None


def send_ntfy_notification(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: Optional[list] = None
) -> bool:
    """
    Synchronous / resilient HTTP POST to ntfy.sh with strict timeout.
    """
    if not topic:
        return False

    base_url = os.getenv("NTFY_BASE_URL", os.getenv("NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
    url = f"{base_url}/{topic}"
    headers = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(url, content=message.encode("utf-8"), headers=headers)
            if resp.status_code == 200:
                logger.info(f"[MobileBridge] Notification dispatched to {topic}")
                return True
            else:
                logger.warning(f"[MobileBridge] Failed with HTTP {resp.status_code}: {resp.text}")
                return False
    except Exception as exc:
        logger.warning(f"[MobileBridge] Webhook delivery failed: {exc}")
        return False


def dispatch_session_start(
    session,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
    custom_topic: Optional[str] = None
) -> bool:
    """
    Sends a session start webhook to ntfy.sh.
    Triggers DND and grayscale automation on connected mobile devices.
    """
    if isinstance(session, str):
        uid = user_id
        duration = 25
    else:
        uid = user_id or getattr(session, "user_id", None)
        duration = getattr(session, "target_duration_min", 25)

    topic = resolve_ntfy_topic(db=db, user_id=uid, explicit_topic=custom_topic)
    if not topic:
        logger.debug("[MobileBridge] No ntfy topic configured. Skipping session start notification.")
        return False

    title = "Focus Session Locked In"
    message = f"Session started. Target: {duration}m. Phone in isolation / DND activated."
    return send_ntfy_notification(topic=topic, title=title, message=message, priority="high", tags=["lock", "timer"])


def dispatch_session_end(
    session,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
    custom_topic: Optional[str] = None
) -> bool:
    """
    Sends a session end webhook to ntfy.sh.
    Restores normal phone mode and disables DND.
    """
    if isinstance(session, str):
        uid = user_id
        status = "COMPLETED"
    else:
        uid = user_id or getattr(session, "user_id", None)
        status = getattr(session, "status", "COMPLETED")

    topic = resolve_ntfy_topic(db=db, user_id=uid, explicit_topic=custom_topic)
    if not topic:
        return False

    title = f"Focus Session: {status}"
    message = f"Session ended with status: {status}."
    return send_ntfy_notification(topic=topic, title=title, message=message, priority="default", tags=["checkered_flag"])


def dispatch_peripheral_lockdown(
    track: str = "critical",
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
    custom_topic: Optional[str] = None
) -> bool:
    """
    Dispatches immediate high-priority alert via ntfy.sh for physical phone isolation.
    """
    topic = resolve_ntfy_topic(db=db, user_id=user_id, explicit_topic=custom_topic)
    if not topic:
        return False
    title = f"Peripheral Lockdown ({track.upper()})"
    message = "CRITICAL TRACK DETECTED: Physical phone isolation required (>3m boundary)."
    return send_ntfy_notification(topic=topic, title=title, message=message, priority="urgent", tags=["warning", "no_entry"])


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
        env_top = os.getenv("NTFY_TOPIC")
        if env_top:
            target_topics.append(env_top.strip())
        if "focus_engine_ntfy" not in target_topics:
            target_topics.append("focus_engine_ntfy")

    success = False
    base_url = os.getenv("NTFY_BASE_URL", os.getenv("NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
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
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    base_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                if resp.status_code == 200:
                    logger.info("🚀 Dispatched tunnel online alert to %s/%s -> %s (status %d)", base_url, t, tunnel_url, resp.status_code)
                    success = True
        except Exception as e:
            logger.warning("⚠️ Failed to dispatch tunnel online alert to %s: %s", t, e)

    return success


def dispatch_eod_scorecard(
    topic: str,
    score: float,
    status_grade: str,
    verified_minutes: float,
    target_minutes: int,
    completed_blocks: int,
    distractions: int,
    paste_bursts: int,
    dashboard_url: Optional[str] = None,
) -> bool:
    """
    Dispatches nightly EOD scorecard push to the user's configured ntfy_topic.
    """
    env_top = os.getenv("NTFY_TOPIC")
    target_topic = (topic or env_top or "focus_engine_ntfy").strip()
    title = f"📊 EOD Scorecard: {score}% ({status_grade})"
    message = (
        f"Verified: {verified_minutes}m / {target_minutes}m | "
        f"Blocks: {completed_blocks} | "
        f"Distractions: {distractions} | "
        f"Paste Flags: {paste_bursts}"
    )
    priority = 4 if score < 60.0 else 3
    click_url = f"{dashboard_url.rstrip('/')}/#analytics" if dashboard_url else "http://127.0.0.1:8000/#analytics"

    payload = {
        "topic": target_topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": ["chart_with_upwards_trend", "bar_chart"],
        "click": click_url,
    }
    base_url = os.getenv("NTFY_BASE_URL", os.getenv("NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                base_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code == 200:
                logger.info("📊 Dispatched EOD scorecard to %s/%s (status %d)", base_url, target_topic, resp.status_code)
                return True
            else:
                logger.warning("⚠️ Failed to dispatch EOD scorecard with HTTP %s: %s", resp.status_code, resp.text)
                return False
    except Exception as e:
        logger.warning("⚠️ Failed to dispatch EOD scorecard to %s: %s", target_topic, e)
        return False
