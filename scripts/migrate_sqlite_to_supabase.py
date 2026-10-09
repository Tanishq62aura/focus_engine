#!/usr/bin/env python3
"""
scripts/migrate_sqlite_to_supabase.py
Standalone migration utility to migrate Focus Engine records from local SQLite
(focus_system.db) to hosted Supabase PostgreSQL.

Features:
- Validates connections to both source SQLite and target Supabase Postgres.
- Auto-creates target schema via SQLAlchemy models if missing.
- Migrates tables in foreign-key dependency order:
    1. tasks
    2. schedules
    3. focus_sessions
    4. distraction_logs
    5. excuse_logs
- Handles schema evolution: backfills 'user_id' for legacy SQLite records.
- Converts naive timestamps to timezone-aware UTC for Postgres TIMESTAMPTZ.
- Skips records already present in target by primary key (idempotent).
- Supports --dry-run mode for pre-flight validation.

Usage:
    python scripts/migrate_sqlite_to_supabase.py --target "postgresql://postgres:password@db.xxxx.supabase.co:5432/postgres"
    # Or using environment variables:
    export TARGET_DATABASE_URL="postgresql://postgres:password@db.xxxx.supabase.co:5432/postgres"
    python scripts/migrate_sqlite_to_supabase.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dateutil import parser as dt_parser
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

# Add project root to sys.path so app models can be imported
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.models import (  # noqa: E402
    Base,
    DistractionLog,
    ExcuseLog,
    FocusSession,
    Schedule,
    Task,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migration")


def parse_timestamp(val: Any) -> Optional[datetime]:
    """Ensures a datetime string or timestamp is parsed into a timezone-aware UTC datetime."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    try:
        dt = dt_parser.parse(str(val))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as e:
        logger.warning("Could not parse timestamp %r: %s", val, e)
        return None


def normalize_postgres_url(url: str) -> str:
    """Normalizes postgres:// and postgresql:// to postgresql+psycopg2:// for SQLAlchemy."""
    url = url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def get_sqlite_table_columns(sqlite_conn: sqlite3.Connection, table_name: str) -> List[str]:
    """Retrieves column names from SQLite table."""
    cursor = sqlite_conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name});")
    return [row[1] for row in cursor.fetchall()]


def read_sqlite_rows(sqlite_conn: sqlite3.Connection, table_name: str) -> List[Dict[str, Any]]:
    """Reads all rows from a SQLite table as dictionaries."""
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = [dict(row) for row in cursor.fetchall()]
        return rows
    except sqlite3.OperationalError as e:
        logger.warning("Could not read table '%s' from SQLite: %s", table_name, e)
        return []


def migrate_tasks(
    sqlite_conn: sqlite3.Connection,
    pg_session: Session,
    default_user_id: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows = read_sqlite_rows(sqlite_conn, "tasks")
    if not rows:
        logger.info("ℹ️ No records found in SQLite 'tasks' table.")
        return 0, 0

    existing_ids = {r[0] for r in pg_session.query(Task.id).all()}
    inserted = 0
    skipped = 0

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue

        task = Task(
            id=row_id,
            user_id=row.get("user_id") or default_user_id,
            title=row.get("title", "Untitled Task"),
            category=row.get("category", "coding"),
            priority=row.get("priority", 3),
            estimated_duration_min=row.get("estimated_duration_min", 60),
            min_goal=row.get("min_goal", 25),
            target_goal=row.get("target_goal", 50),
            stretch_goal=row.get("stretch_goal", 90),
            is_completed=bool(row.get("is_completed", False)),
            created_at=parse_timestamp(row.get("created_at")) or datetime.now(timezone.utc),
        )
        if not dry_run:
            pg_session.add(task)
        inserted += 1

    if not dry_run:
        pg_session.commit()
    logger.info("Tasks: %d inserted, %d skipped (already present)", inserted, skipped)
    return inserted, skipped


def migrate_schedules(
    sqlite_conn: sqlite3.Connection,
    pg_session: Session,
    default_user_id: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows = read_sqlite_rows(sqlite_conn, "schedules")
    if not rows:
        logger.info("ℹ️ No records found in SQLite 'schedules' table.")
        return 0, 0

    existing_ids = {r[0] for r in pg_session.query(Schedule.id).all()}
    valid_task_ids = {r[0] for r in pg_session.query(Task.id).all()}
    inserted = 0
    skipped = 0

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue
        if row["task_id"] not in valid_task_ids:
            logger.warning("Skipping schedule %s (foreign key task_id %s not in target)", row_id, row["task_id"])
            skipped += 1
            continue

        sched = Schedule(
            id=row_id,
            user_id=row.get("user_id") or default_user_id,
            task_id=row["task_id"],
            scheduled_start=parse_timestamp(row.get("scheduled_start")) or datetime.now(timezone.utc),
            scheduled_end=parse_timestamp(row.get("scheduled_end")) or datetime.now(timezone.utc),
            status=row.get("status", "pending"),
            snooze_count=row.get("snooze_count", 0),
        )
        if not dry_run:
            pg_session.add(sched)
        inserted += 1

    if not dry_run:
        pg_session.commit()
    logger.info("Schedules: %d inserted, %d skipped", inserted, skipped)
    return inserted, skipped


def migrate_sessions(
    sqlite_conn: sqlite3.Connection,
    pg_session: Session,
    default_user_id: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows = read_sqlite_rows(sqlite_conn, "focus_sessions")
    if not rows:
        logger.info("ℹ️ No records found in SQLite 'focus_sessions' table.")
        return 0, 0

    existing_ids = {r[0] for r in pg_session.query(FocusSession.id).all()}
    valid_task_ids = {r[0] for r in pg_session.query(Task.id).all()}
    valid_sched_ids = {r[0] for r in pg_session.query(Schedule.id).all()}
    inserted = 0
    skipped = 0

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue
        if row["task_id"] not in valid_task_ids:
            logger.warning("Skipping focus_session %s (task_id %s missing in target)", row_id, row["task_id"])
            skipped += 1
            continue

        schedule_id = row.get("schedule_id")
        if schedule_id and schedule_id not in valid_sched_ids:
            schedule_id = None

        session = FocusSession(
            id=row_id,
            user_id=row.get("user_id") or default_user_id,
            task_id=row["task_id"],
            schedule_id=schedule_id,
            is_five_min_intervention=bool(row.get("is_five_min_intervention", False)),
            started_at=parse_timestamp(row.get("started_at")),
            ended_at=parse_timestamp(row.get("ended_at")),
            start_latency_min=row.get("start_latency_min", 0),
            actual_focused_seconds=row.get("actual_focused_seconds", 0),
            verification_status=row.get("verification_status", "unverified"),
            pasted_burst_detected=bool(row.get("pasted_burst_detected", False)),
            proof_text=row.get("proof_text"),
            viva_question=row.get("viva_question"),
            viva_answer=row.get("viva_answer"),
            viva_feedback=row.get("viva_feedback"),
            notes=row.get("notes"),
        )
        if not dry_run:
            pg_session.add(session)
        inserted += 1

    if not dry_run:
        pg_session.commit()
    logger.info("Focus Sessions: %d inserted, %d skipped", inserted, skipped)
    return inserted, skipped


def migrate_distractions(
    sqlite_conn: sqlite3.Connection,
    pg_session: Session,
    default_user_id: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows = read_sqlite_rows(sqlite_conn, "distraction_logs")
    if not rows:
        logger.info("ℹ️ No records found in SQLite 'distraction_logs' table.")
        return 0, 0

    existing_ids = {r[0] for r in pg_session.query(DistractionLog.id).all()}
    valid_session_ids = {r[0] for r in pg_session.query(FocusSession.id).all()}
    inserted = 0
    skipped = 0

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue
        if row["session_id"] not in valid_session_ids:
            skipped += 1
            continue

        log = DistractionLog(
            id=row_id,
            user_id=row.get("user_id") or default_user_id,
            session_id=row["session_id"],
            timestamp=parse_timestamp(row.get("timestamp")) or datetime.now(timezone.utc),
            interruption_type=row.get("interruption_type", "other"),
        )
        if not dry_run:
            pg_session.add(log)
        inserted += 1

    if not dry_run:
        pg_session.commit()
    logger.info("Distraction Logs: %d inserted, %d skipped", inserted, skipped)
    return inserted, skipped


def migrate_excuses(
    sqlite_conn: sqlite3.Connection,
    pg_session: Session,
    default_user_id: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    rows = read_sqlite_rows(sqlite_conn, "excuse_logs")
    if not rows:
        logger.info("ℹ️ No records found in SQLite 'excuse_logs' table.")
        return 0, 0

    existing_ids = {r[0] for r in pg_session.query(ExcuseLog.id).all()}
    valid_schedule_ids = {r[0] for r in pg_session.query(Schedule.id).all()}
    inserted = 0
    skipped = 0

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue
        if row["schedule_id"] not in valid_schedule_ids:
            skipped += 1
            continue

        log = ExcuseLog(
            id=row_id,
            user_id=row.get("user_id") or default_user_id,
            schedule_id=row["schedule_id"],
            timestamp=parse_timestamp(row.get("timestamp")) or datetime.now(timezone.utc),
            stated_reason=row.get("stated_reason", ""),
            is_valid_blocker=bool(row.get("is_valid_blocker", False)),
            ai_audit_response=row.get("ai_audit_response"),
        )
        if not dry_run:
            pg_session.add(log)
        inserted += 1

    if not dry_run:
        pg_session.commit()
    logger.info("Excuse Logs: %d inserted, %d skipped", inserted, skipped)
    return inserted, skipped


def run_migration(
    sqlite_path: Path,
    target_url: str,
    default_user_id: str = "dev-user",
    dry_run: bool = False,
) -> bool:
    print("\n" + "=" * 65)
    print("  FOCUS ENGINE – SQLITE TO HOSTED SUPABASE (POSTGRESQL)")
    print("=" * 65)
    print(f" Source SQLite DB : {sqlite_path}")
    print(f" Target Postgres  : {target_url.split('@')[-1] if '@' in target_url else target_url}")
    print(f" Default User ID  : {default_user_id}")
    print(f" Dry Run Mode     : {dry_run}")
    print("-" * 65)

    if not sqlite_path.exists():
        logger.error("❌ SQLite database file not found at: %s", sqlite_path)
        return False

    # Connect to SQLite
    try:
        sqlite_conn = sqlite3.connect(str(sqlite_path))
        logger.info("✅ Connected to SQLite source.")
    except Exception as e:
        logger.error("❌ Failed to connect to SQLite: %s", e)
        return False

    # Connect to PostgreSQL / Supabase
    pg_url = normalize_postgres_url(target_url)
    try:
        pg_engine = create_engine(
            pg_url,
            pool_pre_ping=True,
            pool_size=5,
            echo=False,
        )
        with pg_engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        logger.info("✅ Connected to target Supabase PostgreSQL.")
    except Exception as e:
        logger.error("❌ Failed to connect to PostgreSQL target: %s", e)
        sqlite_conn.close()
        return False

    # Ensure schema exists on target PostgreSQL
    if not dry_run:
        logger.info("🔨 Ensuring tables exist in target Supabase DB...")
        Base.metadata.create_all(bind=pg_engine)
        logger.info("✅ Target schema validated.")

    PgSessionLocal = sessionmaker(bind=pg_engine)
    pg_session = PgSessionLocal()

    try:
        logger.info("🚀 Beginning data copy...")
        t_ins, t_skip = migrate_tasks(sqlite_conn, pg_session, default_user_id, dry_run)
        s_ins, s_skip = migrate_schedules(sqlite_conn, pg_session, default_user_id, dry_run)
        f_ins, f_skip = migrate_sessions(sqlite_conn, pg_session, default_user_id, dry_run)
        d_ins, d_skip = migrate_distractions(sqlite_conn, pg_session, default_user_id, dry_run)
        e_ins, e_skip = migrate_excuses(sqlite_conn, pg_session, default_user_id, dry_run)

        print("-" * 65)
        print("  MIGRATION SUMMARY")
        print("-" * 65)
        print(f"  • Tasks           : {t_ins} copied, {t_skip} skipped")
        print(f"  • Schedules       : {s_ins} copied, {s_skip} skipped")
        print(f"  • Focus Sessions  : {f_ins} copied, {f_skip} skipped")
        print(f"  • Distraction Logs: {d_ins} copied, {d_skip} skipped")
        print(f"  • Excuse Logs     : {e_ins} copied, {e_skip} skipped")
        total_migrated = t_ins + s_ins + f_ins + d_ins + e_ins
        print("=" * 65)
        if dry_run:
            print(f"  DRY RUN COMPLETED: {total_migrated} record(s) eligible for migration.")
        else:
            print(f"  MIGRATION COMPLETE: {total_migrated} record(s) successfully transferred.")
        print("=" * 65 + "\n")
        return True
    except Exception as e:
        pg_session.rollback()
        logger.error("❌ Migration failed with error: %s", e, exc_info=True)
        return False
    finally:
        pg_session.close()
        sqlite_conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Focus Engine records from local SQLite to Supabase PostgreSQL."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=os.getenv("SQLITE_PATH", str(_ROOT / "focus_system.db")),
        help="Path to existing SQLite file (default: ./focus_system.db)",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL", ""),
        help="Target PostgreSQL connection string (or set TARGET_DATABASE_URL env var)",
    )
    parser.add_argument(
        "--default-user-id",
        type=str,
        default=os.getenv("DEFAULT_USER_ID", "dev-user"),
        help="User ID to assign to legacy SQLite rows without user scoping (default: dev-user)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview records to be migrated without committing to target",
    )

    args = parser.parse_args()

    if not args.target or args.target.startswith("sqlite"):
        print("\n❌ Error: Please provide a valid PostgreSQL / Supabase target URL.")
        print("Example:")
        print('  python scripts/migrate_sqlite_to_supabase.py --target "postgresql://postgres:password@db.xxxx.supabase.co:5432/postgres"\n')
        sys.exit(1)

    success = run_migration(
        sqlite_path=Path(args.source),
        target_url=args.target,
        default_user_id=args.default_user_id,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
