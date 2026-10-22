"""
tests/test_git_guard_batching.py
Unit and regression tests for Track 1: Batch Git Diff Execution in app/watchdogs/git_guard.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.watchdogs.git_guard import (
    DIFF_SUMMARY_REGEX,
    check_workspace_activity,
    parse_git_diff_stat,
    run_batch_git_diff,
)


SAMPLE_MULTI_DIFF = """
 app/routers/reports.py     | 24 ++++++++++--------------
 app/watchdogs/git_guard.py | 18 +++++++++---------
 2 files changed, 19 insertions(+), 23 deletions(-)
"""

SAMPLE_INSERTIONS_ONLY = """
 src/main.rs | 14 ++++++++++++++
 1 file changed, 14 insertions(+)
"""

SAMPLE_DELETIONS_ONLY = """
 README.md | 2 --
 1 file changed, 2 deletions(-)
"""

SAMPLE_FILES_ONLY = """
 doc/spec.txt | 0
 1 file changed
"""


def test_diff_summary_regex():
    """Verify regex pattern handles plural/singular and optional insertions/deletions."""
    m1 = DIFF_SUMMARY_REGEX.search(" 3 files changed, 14 insertions(+), 2 deletions(-)")
    assert m1 is not None
    assert m1.group(1) == "3"
    assert m1.group(2) == "14"
    assert m1.group(3) == "2"

    m2 = DIFF_SUMMARY_REGEX.search("1 file changed, 5 insertions(+)")
    assert m2 is not None
    assert m2.group(1) == "1"
    assert m2.group(2) == "5"
    assert m2.group(3) is None

    m3 = DIFF_SUMMARY_REGEX.search("1 file changed, 3 deletions(-)")
    assert m3 is not None
    assert m3.group(1) == "1"
    assert m3.group(2) is None
    assert m3.group(3) == "3"


def test_parse_git_diff_stat_multi_file():
    """Verify extraction of changed files, insertions, and deletions from sample git diff output."""
    files_cnt, ins, dels, files = parse_git_diff_stat(SAMPLE_MULTI_DIFF)
    assert files_cnt == 2
    assert ins == 19
    assert dels == 23
    assert files == ["app/routers/reports.py", "app/watchdogs/git_guard.py"]


def test_parse_git_diff_stat_insertions_only():
    """Verify single file with only insertions."""
    files_cnt, ins, dels, files = parse_git_diff_stat(SAMPLE_INSERTIONS_ONLY)
    assert files_cnt == 1
    assert ins == 14
    assert dels == 0
    assert files == ["src/main.rs"]


def test_parse_git_diff_stat_deletions_only():
    """Verify single file with only deletions."""
    files_cnt, ins, dels, files = parse_git_diff_stat(SAMPLE_DELETIONS_ONLY)
    assert files_cnt == 1
    assert ins == 0
    assert dels == 2
    assert files == ["README.md"]


def test_parse_git_diff_stat_empty_and_whitespace():
    """Verify empty diff output cleanly returns zeroes and empty file list."""
    assert parse_git_diff_stat("") == (0, 0, 0, [])
    assert parse_git_diff_stat("   \n\t\n  ") == (0, 0, 0, [])


def test_run_batch_git_diff_success():
    """Verify successful single-call git diff execution and parsing."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=SAMPLE_MULTI_DIFF,
            stderr="",
        )
        files_cnt, ins, dels, files, raw = run_batch_git_diff("/fake/repo", base_ref="HEAD", timeout=5.0)

        assert files_cnt == 2
        assert ins == 19
        assert dels == 23
        assert len(files) == 2
        assert "app/routers/reports.py" in files
        assert mock_run.call_count == 1
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "-C", "/fake/repo", "diff", "--stat", "HEAD"]


def test_run_batch_git_diff_fallback_new_repo_no_head():
    """
    Verify fallback logic cleanly handles new repos without commits
    ('fatal: ambiguous argument HEAD') by falling back to 'git diff --stat'.
    """
    with patch("subprocess.run") as mock_run:
        # First call (with HEAD) fails because HEAD is ambiguous
        # Second call (without HEAD) succeeds
        mock_run.side_effect = [
            MagicMock(
                returncode=128,
                stdout="",
                stderr="fatal: ambiguous argument 'HEAD': unknown revision or path not in the working tree.",
            ),
            MagicMock(
                returncode=0,
                stdout=SAMPLE_INSERTIONS_ONLY,
                stderr="",
            ),
        ]

        files_cnt, ins, dels, files, raw = run_batch_git_diff("/fresh/repo", base_ref="HEAD", timeout=5.0)

        assert files_cnt == 1
        assert ins == 14
        assert dels == 0
        assert files == ["src/main.rs"]
        assert mock_run.call_count == 2
        # Check that fallback command omitted HEAD
        fb_call = mock_run.call_args_list[1][0][0]
        assert fb_call == ["git", "-C", "/fresh/repo", "diff", "--stat"]


def test_run_batch_git_diff_timeout_handled_gracefully():
    """Verify subprocess.TimeoutExpired is caught gracefully without crashing."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git", "diff"], timeout=5.0)):
        files_cnt, ins, dels, files, raw = run_batch_git_diff("/slow/repo", base_ref="HEAD", timeout=5.0)
        assert files_cnt == 0
        assert ins == 0
        assert dels == 0
        assert files == []
        assert raw == ""


def test_run_batch_git_diff_subprocess_error_handled_gracefully():
    """Verify subprocess.SubprocessError is caught gracefully without crashing."""
    with patch("subprocess.run", side_effect=subprocess.SubprocessError("Subprocess execution terminated")):
        files_cnt, ins, dels, files, raw = run_batch_git_diff("/broken/repo", base_ref="HEAD", timeout=5.0)
        assert files_cnt == 0
        assert ins == 0
        assert dels == 0
        assert files == []
        assert raw == ""


def test_check_workspace_activity_uses_batch_diff():
    """
    Verify check_workspace_activity integrates batch diff execution and returns
    structured response with activity indicators.
    """
    with patch("subprocess.run") as mock_run:
        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="true", stderr="")
            if "diff" in cmd:
                return MagicMock(returncode=0, stdout=SAMPLE_MULTI_DIFF, stderr="")
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        activity = check_workspace_activity(target_dir=".")
        assert activity["has_activity"] is True
        assert activity["files_changed_count"] >= 2
        assert activity["insertions"] >= 19
        assert activity["deletions"] >= 23
        assert "app/routers/reports.py" in activity["files"]
