"""
scripts/cluster_excuses.py
Standalone CLI runner and verification tool for Focus Engine's
Excuse Clustering & Behavioral Resistance engine.

Queries live Supabase PostgreSQL (excuse_logs, distraction_logs, focus_sessions, tasks),
clusters user rationalizations, calculates track-specific starting resistance,
and prints a structured terminal diagnostic report.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal, engine
from app.analytics.friction_clustering import ExcuseClusterEngine, TRACK_DISPLAY_NAMES
from app import models

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("cluster_excuses")


def seed_sample_behavioral_telemetry(db, user_id: str = "dev-user") -> int:
    """
    Seeds a representative corpus of multi-track focus sessions, schedules,
    excuses, and distraction logs into Supabase for demonstration and clustering verification.
    """
    logger.info("Seeding realistic multi-track behavioral telemetry into Supabase...")
    now = datetime.now(timezone.utc)

    # 1. Ensure Tasks exist across tracks
    tasks_spec = [
        ("DSA: Graphs & Dynamic Programming", "coding", 1, 60),
        ("T&P: Quantitative Aptitude & Speed Math", "tnp", 2, 45),
        ("OS & Computer Networks: Memory Paging", "theory", 2, 50),
    ]
    task_objs = {}
    for title, cat, pri, dur in tasks_spec:
        t = db.query(models.Task).filter(models.Task.title == title, models.Task.user_id == user_id).first()
        if not t:
            t = models.Task(
                user_id=user_id,
                title=title,
                category=cat,
                priority=pri,
                estimated_duration_min=dur,
                min_goal=25,
                target_goal=40,
                stretch_goal=55,
            )
            db.add(t)
            db.flush()
        task_objs[cat] = t

    # 2. Seed realistic excuses across schedules
    sample_excuses = [
        ("tnp", "Too tired and drained after 6 hours of college lectures, need to rest", False, 1),
        ("tnp", "Got intimidated by permutation and combination probability formulas, felt overwhelmed", False, 2),
        ("tnp", "Hungry and need to grab an evening snack before studying", False, 1),
        ("theory", "Roommate is talking loudly on speakerphone, impossible to read memory management theory", False, 1),
        ("theory", "Feeling exhausted and sleepy, heavy eyes reading OS slides", False, 2),
        ("theory", "Lost in YouTube algorithm watching random tech documentary videos instead of opening syllabus", False, 3),
        ("coding", "Stuck on graph cycle detection, overthinking recursion base case and got anxious", False, 2),
        ("coding", "Setting up Neovim configuration plugins instead of writing code", False, 1),
        ("coding", "Family members asked me to run an urgent household errand", True, 1),
    ]

    excuses_added = 0
    for cat, reason, is_blocker, snooze_cnt in sample_excuses:
        task = task_objs.get(cat)
        if not task:
            continue
        start_t = now - timedelta(days=2, hours=snooze_cnt * 2)
        sched = models.Schedule(
            user_id=user_id,
            task_id=task.id,
            scheduled_start=start_t,
            scheduled_end=start_t + timedelta(minutes=45),
            status="snoozed",
            snooze_count=snooze_cnt,
        )
        db.add(sched)
        db.flush()

        excuse = models.ExcuseLog(
            user_id=user_id,
            schedule_id=sched.id,
            timestamp=start_t + timedelta(minutes=5),
            stated_reason=reason,
            is_valid_blocker=is_blocker,
            ai_audit_response="Audit recorded for friction clustering demonstration.",
        )
        db.add(excuse)
        excuses_added += 1

    # 3. Seed additional distraction sessions
    sample_sessions = [
        ("tnp", 18, 1200, [("phone", 180), ("youtube", 420), ("phone", 800)]), # high early friction
        ("theory", 14, 1800, [("overthinking", 240), ("phone", 540)]),        # early friction
        ("coding", 6, 2700, [("phone", 1500)]),                                # mid session
    ]

    sessions_added = 0
    for cat, lat_min, dur_sec, dist_list in sample_sessions:
        task = task_objs.get(cat)
        if not task:
            continue
        sess_start = now - timedelta(days=1, hours=sessions_added * 3)
        sess = models.FocusSession(
            user_id=user_id,
            task_id=task.id,
            started_at=sess_start,
            ended_at=sess_start + timedelta(seconds=dur_sec),
            start_latency_min=lat_min,
            actual_focused_seconds=dur_sec,
            verification_status="verified",
            notes=f"Telemetry test session for {cat} friction tracking",
        )
        db.add(sess)
        db.flush()
        sessions_added += 1

        for dtype, offset_sec in dist_list:
            dist = models.DistractionLog(
                user_id=user_id,
                session_id=sess.id,
                timestamp=sess_start + timedelta(seconds=offset_sec),
                interruption_type=dtype,
            )
            db.add(dist)

    db.commit()
    logger.info("Successfully seeded %d excuses and %d sessions into Supabase.", excuses_added, sessions_added)
    return excuses_added


def print_terminal_report(report: dict):
    """Renders a structured, clean terminal report for behavioral friction analysis."""
    sep = "=" * 80
    subsep = "-" * 80

    print("\n" + sep)
    print(" 🚀 FOCUS ENGINE: EXCUSE CLUSTERING & BEHAVIORAL RESISTANCE REPORT")
    print(sep)
    print(f" Analysis Scope        : Past {report['period_days']} Days")
    print(f" Total Interruptions   : {report['total_interruptions_analyzed']} events (Distractions + Logged Excuses)")
    print(f" Primary Friction Trap : {report['primary_friction_cluster']}")
    print(f" Fleet Resistance Score: {report['average_resistance_score']}/100")
    print(f" Target User           : {report['meta']['user_id']}")
    print(sep)

    # Table 1: Track-Level Interruption Breakdown
    print("\n📊 1. INTERRUPTION BREAKDOWN BY TRACK / CATEGORY")
    print(subsep)
    header1 = f"{'Track':<14} | {'Sessions':<8} | {'Hours':<6} | {'Distr':<5} | {'Excus':<5} | {'Rate/Hr':<7} | {'Early (≤10m)':<12} | {'Avg Lat':<7}"
    print(header1)
    print(subsep)

    for k, t in report["tracks"].items():
        print(
            f"{t['track']:<14} | "
            f"{t['total_sessions']:<8} | "
            f"{t['total_focused_hours']:<6.1f} | "
            f"{t['distraction_count']:<5} | "
            f"{t['excuse_count']:<5} | "
            f"{t['interruption_rate_per_hour']:<7.2f} | "
            f"{t['early_distractions_first_10m']:<12} | "
            f"{t['avg_start_latency_min']:<6.1f}m"
        )
    print(subsep)

    # Table 2: Top Identified Friction Clusters
    print("\n🧩 2. TOP IDENTIFIED FRICTION TRIGGERS & RATIONALIZATION CLUSTERS")
    print(subsep)
    if not report["clusters"]:
        print("  (No interruptions logged during this period)")
    else:
        for idx, c in enumerate(report["clusters"], 1):
            impact_str = ", ".join(f"{k}:{v}" for k, v in c["tracks_impacted"].items())
            print(f" #{idx} {c['icon']} {c['name']} (Severity: {c['severity']:.2f})")
            print(f"     Share    : {c['count']} occurrences ({c['percentage']}%) | Impacted Tracks: [{impact_str}]")
            if c["sample_quotes"]:
                quotes_disp = " | ".join(f'"{q}"' for q in c["sample_quotes"][:2])
                print(f"     Examples : {quotes_disp}")
            print(f"     Protocol : {c['remediation']}")
            print()
    print(subsep)

    # Table 3: Track-Level Resistance Scores & Threshold Recommendations
    print("\n⚡ 3. TRACK-LEVEL STARTING RESISTANCE & THRESHOLD CALIBRATION")
    print(subsep)
    header3 = f"{'Track':<14} | {'Resistance':<10} | {'High Friction?':<14} | {'Baseline Penalty':<16} | {'Status'}"
    print(header3)
    print(subsep)

    for k, t in report["tracks"].items():
        high_flag = "⚠️ YES (HIGH)" if t["high_initiation_resistance"] else "✅ NO (STABLE)"
        score_bar = f"{t['resistance_score']:.1f}/100"
        status = "CRITICAL" if t["resistance_score"] >= 60 else ("MODERATE" if t["resistance_score"] >= 35 else "HEALTHY")

        print(
            f"{t['track']:<14} | "
            f"{score_bar:<10} | "
            f"{high_flag:<14} | "
            f"+{t['adjusted_baseline_latency_penalty_min']:<15.1f}m | "
            f"{status}"
        )
        print(f"  👉 Recommendation: {t['recommendation']}")
        print()
    print(sep + "\n")


def main():
    parser = argparse.ArgumentParser(description="Focus Engine Excuse Clustering & Behavioral Resistance Engine")
    parser.add_argument("--days", type=int, default=14, help="Analysis lookback window in days (default: 14)")
    parser.add_argument("--user-id", type=str, default="dev-user", help="User ID to analyze (default: dev-user)")
    parser.add_argument("--seed", action="store_true", help="Seed sample behavioral excuses and distractions if empty")
    parser.add_argument("--json", action="store_true", help="Print report in raw JSON format")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Check current data count
        excuse_count = db.query(models.ExcuseLog).filter(models.ExcuseLog.user_id == args.user_id).count()
        distraction_count = db.query(models.DistractionLog).filter(models.DistractionLog.user_id == args.user_id).count()

        if (excuse_count == 0 or args.seed):
            logger.info("Found %d excuses and %d distractions in live Supabase.", excuse_count, distraction_count)
            if args.seed or excuse_count == 0:
                seed_sample_behavioral_telemetry(db, user_id=args.user_id)

        # Run clustering engine
        engine_inst = ExcuseClusterEngine(db, user_id=args.user_id, days=args.days)
        report = engine_inst.run_analysis()

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_terminal_report(report)

    finally:
        db.close()


if __name__ == "__main__":
    main()
