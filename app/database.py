"""
app/database.py
SQLAlchemy engine + session factory supporting both SQLite (offline/local dev)
and hosted Supabase PostgreSQL with connection pooling.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Database URL configuration
# ---------------------------------------------------------------------------
# Default to local SQLite for offline development if DATABASE_URL is not set
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./focus_system.db")

# Normalize Postgres dialect for SQLAlchemy if needed
# e.g., postgres:// or postgresql:// -> postgresql+psycopg2://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Configure engine with dialect-specific options
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},  # Required for SQLite + threading
        echo=False,
    )
else:
    # Hosted PostgreSQL / Supabase pool configuration
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    """Initializes tables and automatically migrates missing columns on SQLite."""
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    if not DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            try:
                conn.execute(text("""
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS custom_tracks JSONB DEFAULT '[]'::jsonb;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS workspace_path VARCHAR(255) DEFAULT 'D:\\code\\workspace';
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS viva_strictness VARCHAR(32) DEFAULT 'standard';
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS auto_fail_paste_bursts BOOLEAN DEFAULT TRUE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS ntfy_topic VARCHAR(128) DEFAULT 'focus_engine_ntfy';
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS peripheral_boundary_enforced BOOLEAN DEFAULT TRUE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS auto_clear_samsung_dnd BOOLEAN DEFAULT TRUE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS peripheral_boundary_meters INTEGER DEFAULT 3;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_min_minutes INTEGER DEFAULT 25;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_target_minutes INTEGER DEFAULT 60;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_stretch_minutes INTEGER DEFAULT 90;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS enable_micro_start_gate BOOLEAN DEFAULT TRUE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS emergency_bail_hold_seconds INTEGER DEFAULT 3;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS penalize_early_abort_streak BOOLEAN DEFAULT TRUE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_soundscape VARCHAR(32) DEFAULT 'off';
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS default_volume FLOAT DEFAULT 0.7;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS auto_zen_mode BOOLEAN DEFAULT FALSE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS blind_focus_mode BOOLEAN DEFAULT FALSE;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS daily_target_minutes INTEGER DEFAULT 180;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS current_streak_days INTEGER DEFAULT 0;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS longest_streak_days INTEGER DEFAULT 0;
                    ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS daily_floor_target INTEGER DEFAULT 2;
                """))
                conn.commit()
            except Exception:
                pass
    else:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = ["tasks", "schedules", "focus_sessions", "distraction_logs", "excuse_logs", "parked_thoughts"]
        with engine.connect() as conn:
            for t in tables:
                if inspector.has_table(t):
                    cols = [c["name"] for c in inspector.get_columns(t)]
                    if "user_id" not in cols:
                        try:
                            conn.execute(text(f"ALTER TABLE {t} ADD COLUMN user_id VARCHAR(64) DEFAULT 'dev-user' NOT NULL"))
                        except Exception:
                            pass
            if inspector.has_table("user_settings"):
                setting_cols = [c["name"] for c in inspector.get_columns("user_settings")]
                sqlite_col_defs = {
                    "custom_tracks": "JSON DEFAULT '[]'",
                    "workspace_path": "VARCHAR(255) DEFAULT 'D:\\code\\workspace'",
                    "viva_strictness": "VARCHAR(32) DEFAULT 'standard'",
                    "auto_fail_paste_bursts": "BOOLEAN DEFAULT 1",
                    "ntfy_topic": "VARCHAR(128) DEFAULT 'focus_engine_ntfy'",
                    "peripheral_boundary_enforced": "BOOLEAN DEFAULT 1",
                    "auto_clear_samsung_dnd": "BOOLEAN DEFAULT 1",
                    "peripheral_boundary_meters": "INTEGER DEFAULT 3",
                    "default_min_minutes": "INTEGER DEFAULT 25",
                    "default_target_minutes": "INTEGER DEFAULT 60",
                    "default_stretch_minutes": "INTEGER DEFAULT 90",
                    "enable_micro_start_gate": "BOOLEAN DEFAULT 1",
                    "emergency_bail_hold_seconds": "INTEGER DEFAULT 3",
                    "penalize_early_abort_streak": "BOOLEAN DEFAULT 1",
                    "default_soundscape": "VARCHAR(32) DEFAULT 'off'",
                    "default_volume": "FLOAT DEFAULT 0.7",
                    "auto_zen_mode": "BOOLEAN DEFAULT 0",
                    "blind_focus_mode": "BOOLEAN DEFAULT 0",
                    "daily_target_minutes": "INTEGER DEFAULT 180",
                    "current_streak_days": "INTEGER DEFAULT 0",
                    "longest_streak_days": "INTEGER DEFAULT 0",
                    "daily_floor_target": "INTEGER DEFAULT 2",
                }
                for cname, cdef in sqlite_col_defs.items():
                    if cname not in setting_cols:
                        try:
                            conn.execute(text(f"ALTER TABLE user_settings ADD COLUMN {cname} {cdef}"))
                        except Exception:
                            pass
            conn.commit()


def get_db():
    """FastAPI dependency – yields a DB session and ensures it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
