"""
scripts/migrate_settings_table.py
Upgrades database schema for dynamic study tracks:
1. Alters tasks.category from VARCHAR(6) to VARCHAR(64) in PostgreSQL/Supabase.
2. Creates user_settings table if it does not already exist.
"""
import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("Error: DATABASE_URL not found in .env")
    sys.exit(1)

try:
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        print("[*] Inspecting database schema...")
        inspector = inspect(conn)

        # 1. Expand tasks.category to VARCHAR(64)
        if db_url.startswith("postgres"):
            print("  [*] Altering tasks.category to VARCHAR(64)...")
            try:
                conn.execute(text("ALTER TABLE tasks ALTER COLUMN category TYPE VARCHAR(64);"))
                print("  [OK] tasks.category widened to VARCHAR(64).")
            except Exception as e:
                print(f"  [!] Notice during tasks.category alter: {e}")

        # 2. Create user_settings table
        if not inspector.has_table("user_settings"):
            print("  [*] Creating user_settings table...")
            if db_url.startswith("postgres"):
                conn.execute(text("""
                    CREATE TABLE user_settings (
                        id VARCHAR(36) PRIMARY KEY,
                        user_id VARCHAR(64) NOT NULL,
                        tracks JSONB NOT NULL DEFAULT '[]'::jsonb,
                        ntfy_topic VARCHAR(128),
                        peripheral_boundary_meters INTEGER DEFAULT 3,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX ix_user_settings_user_id ON user_settings(user_id);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE user_settings (
                        id VARCHAR(36) PRIMARY KEY,
                        user_id VARCHAR(64) NOT NULL,
                        tracks TEXT NOT NULL DEFAULT '[]',
                        ntfy_topic VARCHAR(128),
                        peripheral_boundary_meters INTEGER DEFAULT 3,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    );
                    CREATE INDEX ix_user_settings_user_id ON user_settings(user_id);
                """))
            print("  [OK] user_settings table created successfully.")
        else:
            print("  [OK] user_settings table already exists.")

    print("\n[SUCCESS] Settings schema migration completed successfully.")
except Exception as err:
    print(f"[ERROR] Migration failed: {err}")
    sys.exit(1)
