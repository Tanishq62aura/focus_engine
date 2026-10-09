# Focus Engine — Architecture & Development Roadmap

## Phase 1: Core Foundation (COMPLETED)
- SQLite schema (tasks, schedules, sessions, distractions, excuses)
- FastAPI REST endpoints with UTC latency tracking
- Option A Excuse Auditor & 5-minute micro-start floor
- Dark-mode Command HUD with 1-tap distraction logger
- Gemini AI Technical Viva service (code viva, scorecards, theory defense)

## Phase 2: Laptop Watchdogs & Integrity (COMPLETED)
- VS Code Extension: Monitor onDidChangeTextDocument to catch bulk code pastes
- YouTube Tab Auditor: Check active video titles during focus sessions

## Phase 3: Mobile & Cloud Sync (COMPLETED / IN PROGRESS)
- Cloud migration: PostgreSQL/Supabase with connection pooling (`psycopg2-binary`)
- Google OAuth2 & Supabase JWT validation (`python-jose[cryptography]`)
- Multi-user isolation via `user_id` across all database models and endpoints
- Mobile PWA capabilities: `manifest.json`, `sw.js` offline shell caching, vector/PNG app icons
- Standalone migration script: `scripts/migrate_sqlite_to_supabase.py`
- Samsung Modes & Routines webhook integration (Grayscale + DND toggle)
