"""
app/watchdogs/git_guard.py
Track-Scoped Local File/Git Proof Validation Watchdog for Focus Engine.

Inspects workspace activity via Git diff, status, and recursive filesystem scanning
to verify that genuine coding occurred during a coding focus session before unlocking
AI-powered Viva defense.
Non-coding tracks (theory, tp_aptitude, reading) skip file inspection.
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
    ".py", ".ts", ".js", ".cpp", ".java", ".go", ".rs", ".md",
    ".c", ".h", ".hpp", ".txt", ".json", ".sql", ".html", ".css", ".jsx", ".tsx"
}

IGNORED_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".gemini", "logs", "dist", "build", ".idea", ".vscode", ".pytest_cache"
}

DIFF_SUMMARY_REGEX = re.compile(
    r"(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
    re.IGNORECASE,
)


def parse_git_diff_stat(output: str) -> tuple[int, int, int, List[str]]:
    """
    Parses `git diff --stat` output in memory using a regex parser.
    Extracts individual file paths and aggregate line changes.
    Returns: (files_changed, insertions, deletions, modified_files)
    """
    if not output or not output.strip():
        return 0, 0, 0, []

    modified_files: List[str] = []
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return 0, 0, 0, []

    summary_match = None
    for line in reversed(lines):
        m = DIFF_SUMMARY_REGEX.search(line)
        if m:
            summary_match = m
            break

    for line in lines:
        if "|" in line:
            if summary_match and line == summary_match.string:
                continue
            fname = line.split("|")[0].strip().strip('"').strip("'")
            if fname and fname not in modified_files:
                modified_files.append(fname)

    if summary_match:
        files_changed = int(summary_match.group(1) or 0)
        insertions = int(summary_match.group(2) or 0)
        deletions = int(summary_match.group(3) or 0)
    else:
        files_changed = len(modified_files)
        insertions = 0
        deletions = 0

    return files_changed, insertions, deletions, modified_files


def run_batch_git_diff(
    root_path: Union[Path, str],
    base_ref: Optional[str] = "HEAD",
    timeout: float = 5.0,
) -> tuple[int, int, int, List[str], str]:
    """
    Executes a single repository-wide batch git diff command against the working tree.
    Guards against repos without commits (fatal: ambiguous argument 'HEAD') by falling back
    to `git diff --stat` (staged/unstaged against working tree).
    Sets strict subprocess timeouts (timeout=5.0) and catches TimeoutExpired and SubprocessError
    gracefully, returning zero-change summaries rather than crashing the watchdog.
    """
    root_str = str(root_path)
    cmd = ["git", "-C", root_str, "diff", "--stat"]
    if base_ref:
        cmd.append(base_ref)

    output = ""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            output = proc.stdout
        else:
            err = (proc.stderr or "").lower()
            if "ambiguous argument" in err or "unknown revision" in err or (base_ref and "head" in err):
                logger.info("HEAD not found in %s; falling back to git diff --stat", root_str)
                fb_proc = subprocess.run(
                    ["git", "-C", root_str, "diff", "--stat"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if fb_proc.returncode == 0:
                    output = fb_proc.stdout
                    if not output.strip():
                        cached_proc = subprocess.run(
                            ["git", "-C", root_str, "diff", "--stat", "--cached"],
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                        )
                        if cached_proc.returncode == 0:
                            output = cached_proc.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.warning("Git diff batch execution error or timeout: %s", exc)
        return 0, 0, 0, [], ""
    except Exception as exc:
        logger.warning("Unexpected error during git diff batch execution: %s", exc)
        return 0, 0, 0, [], ""

    files_cnt, ins_cnt, del_cnt, files = parse_git_diff_stat(output)
    return files_cnt, ins_cnt, del_cnt, files, output.strip()


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


DANGEROUS_SYSTEM_ROOTS: Set[str] = {
    "/", "\\", "c:\\", "c:/", "/etc", "/bin", "/usr", "/var",
    "c:\\windows", "c:\\windows\\system32", "c:\\program files", "c:\\program files (x86)"
}


def _scan_filesystem_fallback(target_dir: Path, since_ts: Optional[datetime]) -> Dict[str, Any]:
    """
    Fallback recursive directory scanning using os.scandir when git is clean or unavailable.
    Checks st_mtime >= since_timestamp for target file extensions.
    Guarded with depth and count bounds to prevent DoS.
    """
    files_modified = 0
    insertions = 0
    deletions = 0
    modified_files: List[str] = []
    threshold = since_ts.timestamp() if since_ts else 0.0
    scanned_count = 0

    def _walk_dir(current_dir: Path, depth: int = 0):
        nonlocal files_modified, insertions, scanned_count
        if depth > 5 or scanned_count >= 500 or files_modified >= 100:
            return
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    scanned_count += 1
                    if scanned_count >= 500 or files_modified >= 100:
                        break
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in IGNORED_DIRS and not entry.name.startswith("."):
                            _walk_dir(Path(entry.path), depth + 1)
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
                                            # Read at most first 1000 lines to prevent memory exhaustion on giant files
                                            line_cnt = 0
                                            for _ in f:
                                                line_cnt += 1
                                                if line_cnt >= 1000:
                                                    break
                                            insertions += line_cnt
                                    except Exception:
                                        insertions += 10
                            except Exception:
                                pass
        except Exception:
            pass

    _walk_dir(target_dir, 0)

    has_activity = files_modified > 0
    summary = (
        f"{files_modified} files modified (+{insertions} lines) in workspace: "
        f"{', '.join(modified_files[:5])}"
        if has_activity
        else "Zero file modifications detected in your workspace during this coding block."
    )

    return {
        "has_activity": has_activity,
        "files_changed_count": files_modified,
        "files_modified": files_modified,
        "summary": summary,
        "insertions": insertions,
        "deletions": deletions,
        "files": modified_files,
    }


def check_workspace_activity(
    target_dir: str = ".",
    since_timestamp: Optional[Union[datetime, str, float]] = None,
) -> Dict[str, Any]:
    """
    Runs git diff --stat HEAD and git status --porcelain via subprocess.
    If git returns clean or target is not a repo, scans the workspace recursively
    using os.scandir for files modified (st_mtime >= since_timestamp) with extensions
    .py, .ts, .js, .cpp, .java, .go, .rs, .md.
    """
    if not target_dir or not str(target_dir).strip():
        target_dir = "."

    root_path = Path(target_dir).resolve()

    # Security boundary check: disallow system root drives and critical system directories
    is_drive_root = (root_path == root_path.parent) or (len(root_path.parts) <= 1)
    normalized_path = str(root_path).lower().rstrip("/\\")
    is_system_dir = normalized_path in DANGEROUS_SYSTEM_ROOTS or any(
        normalized_path.startswith(d) for d in ("c:\\windows", "c:\\program files", "/etc", "/var", "/bin", "/usr")
    )
    if is_drive_root or is_system_dir or not root_path.exists() or not root_path.is_dir():
        logger.warning("Blocked workspace inspection of invalid or restricted path: %s", root_path)
        return {
            "has_activity": False,
            "files_changed_count": 0,
            "summary": "Workspace path is invalid or restricted.",
            "files": [],
        }

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
        logger.info("Target directory %s is not a git repo; falling back to os.scandir.", root_path)
        return _scan_filesystem_fallback(root_path, since_ts)

    # 2. Query git activity
    files_modified = 0
    insertions = 0
    deletions = 0
    modified_files: List[str] = []
    raw_diff_parts: List[str] = []

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
            commit_ref = f"{base_commit}..HEAD" if base_commit else "HEAD"
            c_cnt, c_ins, c_del, c_files, c_raw = run_batch_git_diff(
                root_path, base_ref=commit_ref, timeout=5.0
            )
            if c_raw:
                raw_diff_parts.append(c_raw)
            for f in c_files:
                if f not in modified_files:
                    modified_files.append(f)
            files_modified += c_cnt
            insertions += c_ins
            deletions += c_del

        # B. Check uncommitted modified/staged files in working directory via single batch diff
        b_cnt, b_ins, b_del, b_files, b_raw = run_batch_git_diff(
            root_path, base_ref="HEAD", timeout=5.0
        )
        if b_raw:
            raw_diff_parts.append(b_raw)
        for f in b_files:
            if f not in modified_files:
                modified_files.append(f)
        files_modified += b_cnt
        insertions += b_ins
        deletions += b_del

        # C. Scan untracked files added during this session
        untracked_files, untracked_lines, untracked_names = _scan_untracked_files(root_path, threshold)
        if untracked_files > 0:
            files_modified += untracked_files
            insertions += untracked_lines
            for uname in untracked_names:
                if uname not in modified_files:
                    modified_files.append(uname)
            raw_diff_parts.append(f"Untracked files: {', '.join(untracked_names)} (+{untracked_lines} lines)")

    except Exception as e:
        logger.error("Git inspection error: %s, falling back to directory scan.", e)
        return _scan_filesystem_fallback(root_path, since_ts)

    files_modified = max(files_modified, len(modified_files))

    # D. If git returns clean, fall back to recursive directory scan
    if files_modified == 0 and len(modified_files) == 0:
        fs_res = _scan_filesystem_fallback(root_path, since_ts)
        if fs_res["has_activity"]:
            return fs_res

    has_activity = bool(files_modified > 0 or insertions > 0 or deletions > 0 or len(modified_files) > 0)
    if has_activity:
        if raw_diff_parts:
            summary = "\n".join(raw_diff_parts)
        else:
            summary = (
                f"Git Activity: {files_modified} files modified (+{insertions}, -{deletions}) "
                f"across: {', '.join(modified_files[:5])}"
            )
    else:
        summary = "Zero file modifications detected in your workspace during this coding block."

    return {
        "has_activity": has_activity,
        "files_changed_count": files_modified,
        "files_modified": files_modified,
        "summary": summary,
        "insertions": insertions,
        "deletions": deletions,
        "files": modified_files,
    }


# Backwards compatibility alias
verify_workspace_activity = check_workspace_activity
