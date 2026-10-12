import os
import sqlite3
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = os.getenv("DATABASE_URL")
print(f"DATABASE_URL configured: {db_url.split('@')[-1] if '@' in db_url else db_url}")

# 1. SQLite Inspection
sqlite_conn = sqlite3.connect("focus_system.db")
sc = sqlite_conn.cursor()
sc.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
sqlite_tables = [r[0] for r in sc.fetchall()]
print("\n--- Local SQLite (focus_system.db) ---")
for t in sqlite_tables:
    cnt = sc.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} rows")

# 2. Remote PostgreSQL Inspection
engine = create_engine(db_url, pool_pre_ping=True)
print("\n--- Supabase PostgreSQL (Remote) ---")
with engine.connect() as conn:
    res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()
    pg_tables = [r[0] for r in res]
    for t in sorted(pg_tables):
        cnt = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        print(f"  {t}: {cnt} rows")
