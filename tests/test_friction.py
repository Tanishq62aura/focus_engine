import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.crud import calculate_session_friction, get_weekly_friction_report
from app.models import FocusSession, Schedule, DistractionLog, Task
from app.main import app

def test_friction_formula_zero():
    # Session with 0 latency, 0 snoozes, 0 distractions, verified, no intervention
    session = FocusSession(
        id="s1",
        start_latency_min=0,
        verification_status="verified",
        is_five_min_intervention=False,
    )
    session.schedule = None
    session.distractions = []
    score = calculate_session_friction(session)
    assert score == 0.0

def test_friction_formula_components():
    # w1 = 20: 15 min latency -> 20.0
    s_latency = FocusSession(id="s2", start_latency_min=15, verification_status="verified", is_five_min_intervention=False)
    s_latency.schedule = None
    s_latency.distractions = []
    assert calculate_session_friction(s_latency) == 20.0

    # w2 = 15: 1 snooze
    sched = Schedule(id="sch1", snooze_count=1)
    s_snooze = FocusSession(id="s3", start_latency_min=0, verification_status="verified", is_five_min_intervention=False, schedule=sched)
    s_snooze.distractions = []
    assert calculate_session_friction(s_snooze) == 15.0

    # w3 = 10: 2 distractions -> 20.0
    s_dist = FocusSession(id="s4", start_latency_min=0, verification_status="verified", is_five_min_intervention=False)
    s_dist.schedule = None
    s_dist.distractions = [DistractionLog(id="d1"), DistractionLog(id="d2")]
    assert calculate_session_friction(s_dist) == 20.0

    # w4 = 25: unverified (C_ratio = 0.5) -> 25 * 0.5 * 10 = 125?
    # w4 * (1 - C_ratio) * 10:
    # if C_ratio = 0.5 -> 25 * (1 - 0.5) * 10 = 25 * 5 = 125, bounded by 100
    # if C_ratio = 1.0 -> 0.0
    # w5 = 30: intervention -> 30.0
    s_interv = FocusSession(id="s5", start_latency_min=0, verification_status="verified", is_five_min_intervention=True)
    s_interv.schedule = None
    s_interv.distractions = []
    assert calculate_session_friction(s_interv) == 30.0

def test_friction_formula_upper_bound():
    s_huge = FocusSession(
        id="s6",
        start_latency_min=120,
        verification_status="rejected",
        is_five_min_intervention=True,
    )
    s_huge.schedule = Schedule(id="sch2", snooze_count=5)
    s_huge.distractions = [DistractionLog(id="d1")] * 10
    score = calculate_session_friction(s_huge)
    assert score == 100.0

def test_friction_api_endpoint():
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: "dev-user"
    try:
        client = TestClient(app)
        res = client.get("/reports/friction-analysis?days=7")
        assert res.status_code == 200
        data = res.json()
        assert "system_friction_index" in data
        assert "categories" in data
        assert "high_friction_culprit" in data
        cat_keys = [c["category"] for c in data["categories"]]
        assert "coding" in cat_keys
        assert "tnp" in cat_keys
        assert "theory" in cat_keys
    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_get_weekly_friction_report_aggregation():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        report = get_weekly_friction_report(db, user_id="dev-user", days=7)
        assert isinstance(report, dict)
        assert report["period_days"] == 7
        assert "system_friction_index" in report
        assert "friction_status" in report
        assert report["friction_status"] in ("optimal", "moderate", "severe")
        assert len(report["categories"]) == 3
        assert "culprit" in report
        assert report["culprit"]["category"] in ("coding", "tnp", "theory", "none")
    finally:
        db.close()
