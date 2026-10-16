import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app


@pytest.fixture
def test_client():
    app.dependency_overrides[get_current_user] = lambda: "test_reports_user"
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "path",
    [
        "/api/reports/daily-scorecard",
        "/reports/daily-scorecard",
        "/api/reports/streak",
        "/reports/streak",
        "/api/reports/circadian-matrix?days=14",
        "/reports/circadian-matrix?days=14",
        "/api/reports/today",
        "/reports/today",
        "/api/reports/friction-analysis?days=7",
        "/reports/friction-analysis?days=7",
    ],
)
def test_reports_dual_routing_aliases(test_client, path):
    """Verify that reports endpoints respond with 200 OK across both /api/reports and /reports prefixes."""
    response = test_client.get(path)
    assert response.status_code == 200, f"Path {path} returned status {response.status_code}: {response.text}"
