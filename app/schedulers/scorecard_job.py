"""
app/schedulers/scorecard_job.py
Nightly EOD execution scorecard scheduler and push dispatcher.
Uses AsyncIOScheduler to trigger daily at 22:00 local time and dispatch
Daily Execution Index summaries to user-configured ntfy topics.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app import models
from app.database import SessionLocal
from app.mobile_bridge import dispatch_eod_scorecard
from app.routers.reports import calculate_daily_scorecard
from app.routers.settings import _detect_tunnel_url

logger = logging.getLogger("focus_engine.scheduler")

scheduler: Optional[AsyncIOScheduler] = None


async def trigger_nightly_scorecards():
    """
    Core job function: iterates over all distinct users in user_settings or focus_sessions,
    computes today's execution scorecard, and pushes an EOD summary via ntfy.
    """
    logger.info("⏰ Running scheduled Nightly EOD Scorecard job...")
    db = SessionLocal()
    try:
        # Resolve all users
        setting_users = [row[0] for row in db.query(models.UserSetting.user_id).distinct().all()]
        session_users = [row[0] for row in db.query(models.FocusSession.user_id).distinct().all()]
        all_users = list(set(setting_users + session_users))
        if not all_users:
            all_users = ["dev-user"]

        tunnel_url = _detect_tunnel_url()
        base_url = tunnel_url or "http://127.0.0.1:8000"
        today = datetime.now().date()

        for user_id in all_users:
            try:
                setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == user_id).first()
                ntfy_topic = setting.ntfy_topic if (setting and setting.ntfy_topic) else "focus_engine_ntfy"

                card = calculate_daily_scorecard(db, user_id, today)
                logger.info(
                    "📊 Scorecard calculated for %s: %s%% (%s), verified=%sm",
                    user_id,
                    card["score"],
                    card["status_grade"],
                    card["verified_minutes"],
                )

                dispatch_eod_scorecard(
                    topic=ntfy_topic,
                    score=card["score"],
                    status_grade=card["status_grade"],
                    verified_minutes=card["verified_minutes"],
                    target_minutes=card["target_minutes"],
                    completed_blocks=card["completed_blocks"],
                    distractions=card["distractions"],
                    paste_bursts=card["paste_bursts"],
                    dashboard_url=base_url,
                )
            except Exception as e:
                logger.error("Failed to dispatch scorecard for user %s: %s", user_id, e)
    finally:
        db.close()


def start_scheduler():
    """Starts the AsyncIOScheduler and schedules the nightly 22:00 job."""
    global scheduler
    if scheduler is not None and scheduler.running:
        return scheduler

    try:
        scheduler = AsyncIOScheduler()
        # Schedule daily at 22:00 (10:00 PM) local time
        trigger = CronTrigger(hour=22, minute=0)
        scheduler.add_job(
            trigger_nightly_scorecards,
            trigger=trigger,
            id="nightly_eod_scorecard",
            name="Nightly EOD Execution Scorecard",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("🚀 Nightly EOD Scorecard scheduler started (configured for daily at 22:00 local time).")
    except Exception as e:
        logger.warning("Could not start AsyncIOScheduler: %s", e)
    return scheduler


def stop_scheduler():
    """Shuts down the scheduler gracefully."""
    global scheduler
    if scheduler is not None and scheduler.running:
        try:
            scheduler.shutdown(wait=False)
            logger.info("🛑 Nightly EOD Scorecard scheduler stopped.")
        except Exception as e:
            logger.warning("Error stopping scheduler: %s", e)
        scheduler = None


def run_scorecard_test_dispatch():
    """
    CLI test runner entrypoint: runs a synchronous test dispatch of the EOD scorecard.
    """
    print("==================================================")
    print("[*] EOD SCORECARD TEST DISPATCH RUNNER")
    print("==================================================")
    db = SessionLocal()
    try:
        user_id = "dev-user"
        setting = db.query(models.UserSetting).filter(models.UserSetting.user_id == user_id).first()
        topic = setting.ntfy_topic if (setting and setting.ntfy_topic) else "focus_engine_ntfy"
        today = datetime.now().date()

        card = calculate_daily_scorecard(db, user_id, today)
        print(f"[*] Target User: {user_id}")
        print(f"[*] Ntfy Topic:  {topic}")
        print(f"[*] Date:        {card['date']}")
        print(f"[*] Score:       {card['score']}% ({card['status_grade']})")
        print(f"[*] Verified:    {card['verified_minutes']}m / {card['target_minutes']}m")
        print(f"[*] Blocks:      {card['completed_blocks']}")
        print(f"[*] Distractions:{card['distractions']}")
        print(f"[*] Paste Flags: {card['paste_bursts']}")

        tunnel_url = _detect_tunnel_url()
        base_url = tunnel_url or "http://127.0.0.1:8000"

        ok = dispatch_eod_scorecard(
            topic=topic,
            score=card["score"],
            status_grade=card["status_grade"],
            verified_minutes=card["verified_minutes"],
            target_minutes=card["target_minutes"],
            completed_blocks=card["completed_blocks"],
            distractions=card["distractions"],
            paste_bursts=card["paste_bursts"],
            dashboard_url=base_url,
        )
        if ok:
            print("[+] Push notification delivered successfully!")
        else:
            print("[!] Push dispatch encountered an error (check topic/network).")
        return ok
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Focus Engine EOD Scorecard Scheduler CLI")
    parser.add_argument("--test-run", action="store_true", help="Run a test scorecard calculation and push dispatch")
    args = parser.parse_args()

    if args.test_run or len(sys.argv) == 1:
        success = run_scorecard_test_dispatch()
        sys.exit(0 if success else 1)
