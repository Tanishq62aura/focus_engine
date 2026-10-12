#!/usr/bin/env python3
"""
scripts/get_tunnel_url.py
Extracts and prints the active *.trycloudflare.com URL from logs/tunnel.log
or via the local cloudflared metrics API.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def main():
    parser = argparse.ArgumentParser(description="Get active Cloudflare Tunnel URL.")
    parser.add_argument(
        "--wait",
        type=int,
        default=10,
        help="Maximum seconds to poll/wait for tunnel URL (default: 10)",
    )
    args = parser.parse_args()

    deadline = time.time() + args.wait
    while time.time() <= deadline:
        url = extract_from_log()
        if url:
            print(url)
            sys.exit(0)
        time.sleep(1)

    print("❌ No active trycloudflare.com tunnel URL found in logs/tunnel.log", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
