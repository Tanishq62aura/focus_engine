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
    if DATABASE_URL.startswith("sqlite"):
        from sqlalchemy import inspect, text
        inspector = inspect(engine)
        tables = ["tasks", "schedules", "focus_sessions", "distraction_logs", "excuse_logs"]
        with engine.connect() as conn:
            for t in tables:
                if inspector.has_table(t):
                    cols = [c["name"] for c in inspector.get_columns(t)]
                    if "user_id" not in cols:
                        conn.execute(text(f"ALTER TABLE {t} ADD COLUMN user_id VARCHAR(64) DEFAULT 'dev-user' NOT NULL"))
            conn.commit()


def get_db():
    """FastAPI dependency – yields a DB session and ensures it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
