"""
app/watchdogs/git_guard.py
Local File/Git Proof Validation Watchdog for Focus Engine.

Inspects workspace activity via Git diff, git log, and recursive filesystem scanning
to verify that genuine coding or technical documentation occurred during a focus session
before unlocking AI-powered Viva defense.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger("focus_engine.watchdogs.git_guard")

TARGET_EXTENSIONS: Set[str] = {
    ".py", ".ts", ".js", ".cpp", ".c", ".h", ".hpp",
    ".md", ".txt", ".json", ".sql", ".html", ".css",
    ".jsx", ".tsx", ".rs", ".go", ".java"
}

IGNORED_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".gemini", "logs", "dist", "build", ".idea", ".vscode", ".pytest_cache"
}

DIFF_SUMMARY_REGEX = re.compile(
    r"(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
    re.IGNORECASE,
)


def _normalize_timestamp(ts: Optional[Union[datetime, str, float]]) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return None


def _format_git_since(dt: datetime) -> str:
    """Format datetime for git --since parameter."""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _scan_untracked_files(root_path: Path, threshold: float) -> tuple[int, int, List[str]]:
    """Scan untracked files from git status --porcelain created or modified >= threshold."""
    files_added = 0
    lines_added = 0
    untracked_paths: List[str] = []

    try:
        proc = subprocess.run(
            ["git", "-C", str(root_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.startswith("??"):
                    rel_file = line[2:].strip().strip('"')
                    ext = Path(rel_file).suffix.lower()
                    if ext in TARGET_EXTENSIONS:
                        full_p = root_path / rel_file
                        if full_p.is_file():
                            try:
                                mtime = full_p.stat().st_mtime
                                if mtime >= threshold:
                                    files_added += 1
                                    untracked_paths.append(rel_file)
                                    try:
                                        with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                                            lines_added += sum(1 for _ in f)
                                    except Exception:
                                        lines_added += 10
                            except Exception:
                                pass
    except Exception as e:
        logger.debug("Failed scanning untracked files via git: %s", e)

    return files_added, lines_added, untracked_paths


def _scan_filesystem_fallback(target_dir: Path, since_ts: Optional[datetime]) -> Dict[str, Any]:
    """
    Fallback recursive directory scanning using os.scandir when git is unavailable.
    Checks st_mtime >= since_timestamp for target file extensions.
    """
    files_modified = 0
    insertions = 0
    deletions = 0
    modified_files: List[str] = []
    threshold = since_ts.timestamp() if since_ts else 0.0

    def _walk_dir(current_dir: Path):
        nonlocal files_modified, insertions
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in IGNORED_DIRS:
                            _walk_dir(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(entry.name).suffix.lower()
                        if ext in TARGET_EXTENSIONS:
                            try:
                                st = entry.stat()
                                if st.st_mtime >= threshold:
                                    files_modified += 1
                                    rel = str(Path(entry.path).relative_to(target_dir))
                                    modified_files.append(rel)
                                    try:
                                        with open(entry.path, "r", encoding="utf-8", errors="ignore") as f:
                                            insertions += sum(1 for _ in f)
                                    except Exception:
                                        insertions += 10
                            except Exception:
                                pass
        except Exception:
            pass

    _walk_dir(target_dir)

    has_activity = files_modified > 0
    summary = (
        f"{files_modified} files modified (+{insertions} lines) in workspace: "
        f"{', '.join(modified_files[:4])}"
        if has_activity
        else "No file modifications detected via filesystem scan"
    )

    return {
        "files_modified": files_modified,
        "insertions": insertions,
        "deletions": deletions,
        "has_activity": has_activity,
        "summary": summary,
        "files": modified_files,
    }


def verify_workspace_activity(
    target_dir: str = ".",
    since_timestamp: Optional[Union[datetime, str, float]] = None,
) -> Dict[str, Any]:
    """
    Inspects git and file activity since the given timestamp.
    Returns:
      {
        "files_modified": int,
        "insertions": int,
        "deletions": int,
        "has_activity": bool,
        "summary": str,
        "files": list[str]
      }
    """
    root_path = Path(target_dir).resolve()
    since_ts = _normalize_timestamp(since_timestamp)
    threshold = since_ts.timestamp() if since_ts else 0.0

    # 1. Check if git repo
    is_git_repo = False
    try:
        proc = subprocess.run(
            ["git", "-C", str(root_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_git_repo = (proc.returncode == 0 and proc.stdout.strip() == "true")
    except Exception as e:
        logger.debug("Git rev-parse check failed: %s", e)

    if not is_git_repo:
        logger.info("Target directory %s is not a git repo; using filesystem fallback scanner.", root_path)
        return _scan_filesystem_fallback(root_path, since_ts)

    # 2. Query git activity
    files_modified = 0
    insertions = 0
    deletions = 0
    modified_files: List[str] = []

    try:
        # A. Check if commits were made since session start
        has_recent_commits = False
        base_commit: Optional[str] = None
        if since_ts:
            since_arg = _format_git_since(since_ts)
            log_proc = subprocess.run(
                ["git", "-C", str(root_path), "log", f"--since={since_arg}", "--oneline"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if log_proc.returncode == 0 and log_proc.stdout.strip():
                has_recent_commits = True
                # Find base commit before this session start
                rev_proc = subprocess.run(
                    ["git", "-C", str(root_path), "rev-list", "-n", "1", f"--before={since_arg}", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if rev_proc.returncode == 0 and rev_proc.stdout.strip():
                    base_commit = rev_proc.stdout.strip()

        # If commits occurred during session, diff from base_commit captures those commits
        if has_recent_commits:
            diff_cmd = ["git", "-C", str(root_path), "diff", "--stat"]
            diff_cmd.append(f"{base_commit}..HEAD" if base_commit else "HEAD")
            c_diff = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=5)
            if c_diff.returncode == 0 and c_diff.stdout.strip():
                lines = c_diff.stdout.strip().splitlines()
                for line in lines[:-1]:
                    if "|" in line:
                        fname = line.split("|")[0].strip()
                        if fname and fname not in modified_files:
                            modified_files.append(fname)
                summary_line = lines[-1]
                match = DIFF_SUMMARY_REGEX.search(summary_line)
                if match:
                    files_modified += int(match.group(1) or 0)
                    insertions += int(match.group(2) or 0)
                    deletions += int(match.group(3) or 0)
                else:
                    files_modified += len(modified_files)

        # B. Check uncommitted modified/staged files in working directory
        # Inspect git status porcelain for modified (M) or staged (A/M/D) files
        st_proc = subprocess.run(
            ["git", "-C", str(root_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if st_proc.returncode == 0 and st_proc.stdout.strip():
            for line in st_proc.stdout.splitlines():
                if len(line) < 3:
                    continue
                code = line[:2]
                rel_file = line[3:].strip().strip('"')
                # Ignore untracked here, handled in _scan_untracked_files
                if code.startswith("??"):
                    continue
                ext = Path(rel_file).suffix.lower()
                if ext in TARGET_EXTENSIONS:
                    full_p = root_path / rel_file
                    if full_p.is_file():
                        try:
                            mtime = full_p.stat().st_mtime
                            # CRITICAL: Only count if modified AFTER session start
                            if mtime >= threshold:
                                if rel_file not in modified_files:
                                    modified_files.append(rel_file)
                                    files_modified += 1
                                    # Get diff stat for this specific file
                                    f_diff = subprocess.run(
                                        ["git", "-C", str(root_path), "diff", "--stat", "HEAD", "--", rel_file],
                                        capture_output=True,
                                        text=True,
                                        timeout=5,
                                    )
                                    if f_diff.returncode == 0 and f_diff.stdout.strip():
                                        m = DIFF_SUMMARY_REGEX.search(f_diff.stdout.strip().splitlines()[-1])
                                        if m:
                                            insertions += int(m.group(2) or 0)
                                            deletions += int(m.group(3) or 0)
                                        else:
                                            insertions += 5
                        except Exception:
                            pass

        # C. Scan untracked files added during this session
        untracked_files, untracked_lines, untracked_names = _scan_untracked_files(root_path, threshold)
        if untracked_files > 0:
            files_modified += untracked_files
            insertions += untracked_lines
            for uname in untracked_names:
                if uname not in modified_files:
                    modified_files.append(uname)

    except Exception as e:
        logger.error("Git inspection error: %s, falling back to directory scan.", e)
        return _scan_filesystem_fallback(root_path, since_ts)

    has_activity = bool(files_modified > 0 or insertions > 0 or deletions > 0 or len(modified_files) > 0)
    if has_activity:
        summary = (
            f"Git Activity: {files_modified} files modified (+{insertions}, -{deletions}) "
            f"across: {', '.join(modified_files[:5])}"
        )
    else:
        summary = "No git or file modifications detected in workspace during session"

    return {
        "files_modified": files_modified,
        "insertions": insertions,
        "deletions": deletions,
        "has_activity": has_activity,
        "summary": summary,
        "files": modified_files,
    }
