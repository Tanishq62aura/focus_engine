#!/usr/bin/env python3
"""
scripts/get_tunnel_url.py
Extracts and prints the active *.trycloudflare.com URL from logs/tunnel.log
and optionally dispatches a clickable push alert to ntfy.sh on startup.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# UTF-8 stdout configuration for Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_FILE = ROOT / "logs" / "tunnel.log"
URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def extract_from_log() -> str | None:
    if not LOG_FILE.exists():
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            matches = URL_REGEX.findall(content)
            if matches:
                # Return the most recent match in the log file
                return matches[-1]
    except Exception:
        pass
    return None


def dispatch_to_ntfy(tunnel_url: str, custom_topic: str | None = None) -> bool:
    """
    Sends an HTTP POST to ntfy with:
      - Title: "🚀 Focus Engine Online"
      - Body: "Active URL: <url>"
      - Click header/attribute: <url> (clicking mobile alert opens app directly)
    """
    try:
        from app.mobile_bridge import dispatch_tunnel_online
        return dispatch_tunnel_online(tunnel_url, topic=custom_topic)
    except Exception:
        # Standalone fallback if app cannot be imported
        topics = []
        if custom_topic:
            topics.append(custom_topic)
        else:
            env_topic = os.getenv("NTFY_TOPIC", "focus_tanishq_engine").strip()
            if env_topic:
                topics.append(env_topic)
            if "focus_engine_ntfy" not in topics:
                topics.append("focus_engine_ntfy")

        success = False
        for t in topics:
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
                    "https://ntfy.sh",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        success = True
            except Exception as e:
                print(f"[!] Warning: failed to dispatch to ntfy topic {t}: {e}", file=sys.stderr)
        return success


import socket


def get_local_lan_ip() -> str:
    """Extract active LAN IPv4 address for local network connectivity."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


get_local_ip = get_local_lan_ip


def main():
    parser = argparse.ArgumentParser(description="Get active Cloudflare Tunnel URL, LAN URL, and dispatch alerts.")
    parser.add_argument(
        "--wait",
        type=int,
        default=10,
        help="Maximum seconds to poll/wait for tunnel URL (default: 10)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Auto-dispatch fresh tunnel URL to ntfy on discovery.",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Optional ntfy topic override.",
    )
    parser.add_argument(
        "--url-only",
        action="store_true",
        help="Print only the raw Cloudflare tunnel URL.",
    )
    args = parser.parse_args()

    local_ip = get_local_ip()
    local_url = f"http://{local_ip}:8000"

    deadline = time.time() + args.wait
    while time.time() <= deadline:
        tunnel_url = extract_from_log()
        if tunnel_url:
            if args.notify:
                notified = dispatch_to_ntfy(tunnel_url, custom_topic=args.topic)
                if notified:
                    print(f"🚀 Dispatched active tunnel URL alert to ntfy: {tunnel_url}", file=sys.stderr)

            if args.url_only:
                print(tunnel_url)
            else:
                print("=" * 60)
                print("🚀 Focus Engine Endpoints")
                print("-" * 60)
                print(f"Cloudflare Tunnel: {tunnel_url}")
                print(f"Local Network URL: {local_url}")
                print("=" * 60)
            sys.exit(0)
        time.sleep(1)

    print("❌ No active trycloudflare.com tunnel URL found in logs/tunnel.log", file=sys.stderr)
    print(f"Local Network URL: {local_url}")
    sys.exit(1)


if __name__ == "__main__":
    main()

