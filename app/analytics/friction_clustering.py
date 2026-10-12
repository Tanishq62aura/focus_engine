"""
app/analytics/friction_clustering.py
Behavioral Excuse Clustering & Initiation Resistance Calibration Engine.

Analyzes telemetry from Supabase PostgreSQL (excuse_logs, distraction_logs, focus_sessions, tasks)
to map recurring friction patterns, cluster user rationalizations, and dynamically calculate
track-specific starting resistance penalties.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from app import models

logger = logging.getLogger("focus_engine.analytics")

# Canonical Track Mapping
TRACK_ALIASES = {
    "coding": "coding",
    "tnp": "tp_aptitude",
    "tp_aptitude": "tp_aptitude",
    "theory": "theory",
}

TRACK_DISPLAY_NAMES = {
    "coding": "Coding (Algorithms & Systems)",
    "tp_aptitude": "T&P Aptitude (Quantitative & Analytical)",
    "theory": "Theory (Core CS & Architecture)",
}

# Archetypal Semantic Friction Taxonomy
FRICTION_TAXONOMY = [
    {
        "id": "fatigue",
        "name": "Fatigue & Circadian Depletion",
        "icon": "⚡",
        "severity": 0.85,
        "keywords": [
            r"\btired\b", r"\bexhaust(ed|ion)\b", r"\bsleep(y)?\b", r"\bheadache\b",
            r"\bdrained\b", r"\blow energy\b", r"\bburnout\b", r"\bnap\b", r"\bheavy eyes\b",
            r"\bletharg(ic|y)\b", r"\bfatigue\b"
        ],
        "default_remediation": "Schedule 15m NSDR (Non-Sleep Deep Rest) or shift intensive blocks to morning."
    },
    {
        "id": "phone_pickup",
        "name": "Mobile Peripheral Notification Trap",
        "icon": "📱",
        "severity": 0.90,
        "keywords": [
            r"\bphone\b", r"\bnotification\b", r"\bcall\b", r"\btext\b", r"\bwhatsapp\b",
            r"\btelegram\b", r"\binstagram\b", r"\bmessage\b", r"\bping\b", r"\bscreen glance\b"
        ],
        "default_remediation": "Engage Faraday box or place mobile device outside physical arm reach (>3m)."
    },
    {
        "id": "digital_drift",
        "name": "Digital Context Drift & Tab Hopping",
        "icon": "🌐",
        "severity": 0.75,
        "keywords": [
            r"\byoutube\b", r"\bvideo\b", r"\bbrows(e|ing)\b", r"\breddit\b", r"\bx\.com\b",
            r"\btwitter\b", r"\btab\b", r"\brabbit hole\b", r"\bnews\b", r"\bdiscord\b",
            r"\bfeed\b", r"\bscroll(ing)?\b"
        ],
        "default_remediation": "Deploy single-window desktop lockdown; enforce URL blacklisting during active timers."
    },
    {
        "id": "cognitive_overwhelm",
        "name": "Cognitive Overwhelm & Problem Intimidation",
        "icon": "🧠",
        "severity": 0.95,
        "keywords": [
            r"\bhard\b", r"\bdifficult\b", r"\bstuck\b", r"\bconfus(ed|ing)\b", r"\bintimidat(ed|ing)\b",
            r"\bcomplex\b", r"\boverthink(ing)?\b", r"\banxiety\b", r"\bdoubt\b", r"\bno clue\b",
            r"\btoo long\b", r"\bimpasse\b", r"\blost\b"
        ],
        "default_remediation": "Decompose task into atomic 5-minute pseudocode / test case generation sub-goals."
    },
    {
        "id": "setup_perfectionism",
        "name": "Context Preparation & Productive Procrastination",
        "icon": "🛠️",
        "severity": 0.65,
        "keywords": [
            r"\bsetup\b", r"\benvironment\b", r"\binstall\b", r"\bconfig\b", r"\bclean desk\b",
            r"\bwait(ing)?\b", r"\btomorrow\b", r"\blater\b", r"\bperfect time\b", r"\bnot ready\b",
            r"\bprep(aration)?\b"
        ],
        "default_remediation": "Enforce 'Dirty Start' rule: write raw code within 120s without pre-cleanup."
    },
    {
        "id": "environmental_interruption",
        "name": "Environmental & Social Friction",
        "icon": "🚪",
        "severity": 0.70,
        "keywords": [
            r"\broommate\b", r"\bfamily\b", r"\bnoise\b", r"\bloud\b", r"\bchore(s)?\b",
            r"\berrand\b", r"\bvisitor\b", r"\bdoor\b", r"\bguest\b", r"\bdisturb(ed|ance)\b",
            r"\bambient\b"
        ],
        "default_remediation": "Wear ANC headphones and display physical 'Do Not Disturb' execution sign."
    },
    {
        "id": "biological_break",
        "name": "Metabolic & Physiological Interruption",
        "icon": "☕",
        "severity": 0.50,
        "keywords": [
            r"\bhungr(y)?\b", r"\bfood\b", r"\bcoffee\b", r"\btea\b", r"\bsnack\b",
            r"\bwater\b", r"\bwashroom\b", r"\brestroom\b"
        ],
        "default_remediation": "Stage water bottle and light fuel 10 minutes prior to session ignition."
    },
]


def classify_rationalization(text: str) -> dict:
    """
    Matches text against archetypal behavioral taxonomy using regex heuristics.
    Falls back to 'unclassified' if no patterns hit.
    """
    clean_text = text.lower().strip()
    matched_clusters = []

    for taxon in FRICTION_TAXONOMY:
        for pattern in taxon["keywords"]:
            if re.search(pattern, clean_text):
                matched_clusters.append(taxon)
                break

    if matched_clusters:
        # Pick highest severity match if multiple apply
        matched_clusters.sort(key=lambda t: t["severity"], reverse=True)
        top = matched_clusters[0]
        return {
            "cluster_id": top["id"],
            "cluster_name": top["name"],
            "icon": top["icon"],
            "severity": top["severity"],
            "remediation": top["default_remediation"],
            "is_classified": True,
        }

    return {
        "cluster_id": "unclassified_noise",
        "cluster_name": "Unclassified Friction & Cognitive Restlessness",
        "icon": "❓",
        "severity": 0.50,
        "remediation": "Audit session notes manually to discover novel distraction taxonomy.",
        "is_classified": False,
    }


def normalize_track(cat: Optional[str]) -> str:
    """Normalizes raw database task category to standardized track name."""
    if not cat:
        return "coding"
    raw = cat.lower().strip()
    return TRACK_ALIASES.get(raw, "coding")


class ExcuseClusterEngine:
    """
    Aggregates logged rationalizations, groups interruptions across tracks,
    and calculates initiation resistance and latency penalties.
    """

    def __init__(self, db: Session, user_id: str = "dev-user", days: int = 14):
        self.db = db
        self.user_id = user_id
        self.days = max(1, days)
        self.cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)

    def fetch_data(self) -> dict:
        """Queries telemetry across core database models within the time window."""
        # Focus sessions with eager task loading
        sessions = (
            self.db.query(models.FocusSession)
            .options(
                joinedload(models.FocusSession.task),
                joinedload(models.FocusSession.schedule),
                joinedload(models.FocusSession.distraction_logs),
            )
            .filter(
                models.FocusSession.user_id == self.user_id,
                models.FocusSession.started_at >= self.cutoff,
            )
            .all()
        )

        # Distraction logs
        distractions = (
            self.db.query(models.DistractionLog)
            .options(
                joinedload(models.DistractionLog.session).joinedload(models.FocusSession.task)
            )
            .filter(
                models.DistractionLog.user_id == self.user_id,
                models.DistractionLog.timestamp >= self.cutoff,
            )
            .all()
        )

        # Excuse logs
        excuses = (
            self.db.query(models.ExcuseLog)
            .options(
                joinedload(models.ExcuseLog.schedule).joinedload(models.Schedule.task)
            )
            .filter(
                models.ExcuseLog.user_id == self.user_id,
                models.ExcuseLog.timestamp >= self.cutoff,
            )
            .all()
        )

        return {
            "sessions": sessions,
            "distractions": distractions,
            "excuses": excuses,
        }

    def run_analysis(self) -> dict:
        """Executes full clustering, track breakdown, and resistance calibration."""
        data = self.fetch_data()
        sessions = data["sessions"]
        distractions = data["distractions"]
        excuses = data["excuses"]

        # 1. Track-level Interruption & Session Accounting
        track_stats = {
            "coding": {
                "track": "coding",
                "display_name": TRACK_DISPLAY_NAMES["coding"],
                "total_sessions": 0,
                "total_focused_seconds": 0,
                "distraction_count": 0,
                "excuse_count": 0,
                "early_distractions": 0,  # <= 10 min into session
                "start_latencies_min": [],
            },
            "tp_aptitude": {
                "track": "tp_aptitude",
                "display_name": TRACK_DISPLAY_NAMES["tp_aptitude"],
                "total_sessions": 0,
                "total_focused_seconds": 0,
                "distraction_count": 0,
                "excuse_count": 0,
                "early_distractions": 0,
                "start_latencies_min": [],
            },
            "theory": {
                "track": "theory",
                "display_name": TRACK_DISPLAY_NAMES["theory"],
                "total_sessions": 0,
                "total_focused_seconds": 0,
                "distraction_count": 0,
                "excuse_count": 0,
                "early_distractions": 0,
                "start_latencies_min": [],
            },
        }

        # Track session hours and start latency
        session_track_map = {}
        for s in sessions:
            track = normalize_track(s.task.category if s.task else None)
            session_track_map[s.id] = (track, s.started_at)
            track_stats[track]["total_sessions"] += 1
            track_stats[track]["total_focused_seconds"] += max(0, s.actual_focused_seconds or 0)
            if s.start_latency_min is not None:
                track_stats[track]["start_latencies_min"].append(max(0, s.start_latency_min))

        # Track distractions
        interruption_events = []
        for d in distractions:
            sess_info = session_track_map.get(d.session_id)
            track = "coding"
            session_start = None
            if sess_info:
                track, session_start = sess_info
            elif d.session and d.session.task:
                track = normalize_track(d.session.task.category)
                session_start = d.session.started_at

            track_stats[track]["distraction_count"] += 1

            # Check if distraction occurred within the first 10 minutes (600s)
            is_early = False
            if session_start and d.timestamp:
                delta_sec = (d.timestamp - session_start).total_seconds()
                if 0 <= delta_sec <= 600:
                    is_early = True
                    track_stats[track]["early_distractions"] += 1

            interruption_events.append({
                "type": "distraction",
                "raw_text": f"{d.interruption_type} distraction",
                "interruption_type": d.interruption_type,
                "track": track,
                "is_early": is_early,
                "timestamp": d.timestamp,
            })

        # Track excuses
        for e in excuses:
            track = "coding"
            if e.schedule and e.schedule.task:
                track = normalize_track(e.schedule.task.category)
            track_stats[track]["excuse_count"] += 1

            interruption_events.append({
                "type": "excuse",
                "raw_text": e.stated_reason,
                "interruption_type": "excuse_snooze",
                "track": track,
                "is_early": True,  # excuses by definition occur before/at scheduled initiation
                "timestamp": e.timestamp,
                "is_valid_blocker": e.is_valid_blocker,
            })

        # 2. Cluster Rationalizations & Telemetry Triggers
        cluster_accumulator = defaultdict(lambda: {
            "count": 0,
            "track_distribution": defaultdict(int),
            "samples": set(),
            "meta": None,
        })

        for event in interruption_events:
            text = event["raw_text"]
            cls_meta = classify_rationalization(text)
            cid = cls_meta["cluster_id"]
            cluster_accumulator[cid]["count"] += 1
            cluster_accumulator[cid]["track_distribution"][event["track"]] += 1
            cluster_accumulator[cid]["samples"].add(text)
            cluster_accumulator[cid]["meta"] = cls_meta

        total_interruptions = len(interruption_events)
        cluster_results = []
        for cid, cdata in cluster_accumulator.items():
            meta = cdata["meta"]
            count = cdata["count"]
            pct = round((count / total_interruptions * 100.0), 1) if total_interruptions > 0 else 0.0
            cluster_results.append({
                "cluster_id": cid,
                "name": meta["cluster_name"],
                "icon": meta["icon"],
                "count": count,
                "percentage": pct,
                "severity": meta["severity"],
                "remediation": meta["remediation"],
                "tracks_impacted": dict(cdata["track_distribution"]),
                "sample_quotes": list(cdata["samples"])[:4],
            })

        # Sort clusters by frequency descending
        cluster_results.sort(key=lambda c: (c["count"], c["severity"]), reverse=True)

        # 3. Track-level Resistance Calibration
        tracks_output = {}
        for track_key, stats in track_stats.items():
            hours = stats["total_focused_seconds"] / 3600.0
            total_track_interruptions = stats["distraction_count"] + stats["excuse_count"]
            interruption_rate_per_hr = (
                round(total_track_interruptions / hours, 2) if hours > 0 else float(total_track_interruptions)
            )

            latencies = stats["start_latencies_min"]
            avg_start_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

            early_distractions = stats["early_distractions"]
            early_ratio = (
                round(early_distractions / stats["distraction_count"], 2)
                if stats["distraction_count"] > 0
                else 0.0
            )

            # High initiation resistance flag:
            # Repeat distractions within first 10m (>= 2) OR high early ratio (>= 0.5) OR avg start latency >= 12m
            high_initiation_resistance = bool(
                early_distractions >= 2
                or (early_distractions >= 1 and early_ratio >= 0.5)
                or avg_start_latency >= 12.0
            )

            # Adjusted Baseline Latency Penalty (Minutes)
            # Base penalty (5.0 min) + initiation friction weight + latency drag
            penalty_bonus = (early_ratio * 8.0) + min(12.0, avg_start_latency * 0.4)
            if high_initiation_resistance:
                penalty_bonus += 5.0
            adjusted_latency_penalty_min = round(min(30.0, 5.0 + penalty_bonus), 1)

            # Resistance Score (0 - 100 Composite Index)
            # 40% early distraction density + 30% start latency + 30% hourly interruption rate
            component_early = min(1.0, early_distractions / 3.0) * 40.0
            component_lat = min(1.0, avg_start_latency / 20.0) * 30.0
            component_rate = min(1.0, interruption_rate_per_hr / 5.0) * 30.0
            resistance_score = round(min(100.0, component_early + component_lat + component_rate), 1)

            # Actionable Tuning Recommendations
            if resistance_score >= 60.0:
                recommendation = (
                    f"CRITICAL RESISTANCE: Enforce mandatory 5-minute micro-start intervention at T=0. "
                    f"Tighten phone proximity boundary. Cap block duration to 25m Pomodoro."
                )
            elif resistance_score >= 35.0:
                recommendation = (
                    f"MODERATE RESISTANCE: Frontload 3 minutes of friction-free setup. "
                    f"Schedule proactive notification shutdown before commencing session."
                )
            else:
                recommendation = (
                    f"LOW RESISTANCE: Track operates with optimal startup velocity. "
                    f"Eligible for extended 60-90 minute deep flow blocks."
                )

            tracks_output[track_key] = {
                "track": track_key,
                "display_name": stats["display_name"],
                "total_sessions": stats["total_sessions"],
                "total_focused_hours": round(hours, 2),
                "total_interruptions": total_track_interruptions,
                "distraction_count": stats["distraction_count"],
                "excuse_count": stats["excuse_count"],
                "early_distractions_first_10m": early_distractions,
                "early_distraction_ratio": early_ratio,
                "interruption_rate_per_hour": interruption_rate_per_hr,
                "avg_start_latency_min": avg_start_latency,
                "high_initiation_resistance": high_initiation_resistance,
                "adjusted_baseline_latency_penalty_min": adjusted_latency_penalty_min,
                "resistance_score": resistance_score,
                "recommendation": recommendation,
            }

        # 4. Global Synthesis
        primary_culprit_cluster = cluster_results[0]["name"] if cluster_results else "None Identified"
        avg_fleet_resistance = (
            round(sum(t["resistance_score"] for t in tracks_output.values()) / len(tracks_output), 1)
            if tracks_output
            else 0.0
        )

        return {
            "period_days": self.days,
            "cutoff_timestamp": self.cutoff.isoformat(),
            "total_interruptions_analyzed": total_interruptions,
            "primary_friction_cluster": primary_culprit_cluster,
            "average_resistance_score": avg_fleet_resistance,
            "clusters": cluster_results,
            "tracks": tracks_output,
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "user_id": self.user_id,
            },
        }
