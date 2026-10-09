# Focus Engine — Behavioral Execution & Accountability Flight Recorder

An unyielding personal execution engine designed to eliminate procrastination, audit excuses, and verify focused work through AI viva examinations and integrity watchdogs.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** to access the Command HUD.

---

## 📖 Complete Documentation

- 📘 **[HOW_TO_RUN.md](file:///d:/new_p/focus_engine/HOW_TO_RUN.md)** — Step-by-step setup guide, Supabase migration, Google OAuth, PWA installation, and watchdogs.
- 📐 **[SYSTEM_SPEC.md](file:///d:/new_p/focus_engine/SYSTEM_SPEC.md)** — Core philosophy, behavioral state machine, formulas, and verification tracks.
- 🔌 **[API_REFERENCE.md](file:///d:/new_p/focus_engine/API_REFERENCE.md)** — REST API specification, schemas, and endpoints.
- 🗺️ **[ROADMAP.md](file:///d:/new_p/focus_engine/ROADMAP.md)** — Architecture roadmap and phase deliverables.
- ⚙️ **[.env.example](file:///d:/new_p/focus_engine/.env.example)** — Environment variable configuration template.

---

## 🛠️ Architecture Highlights

- **Dual Database Layer**: Works 100% offline with SQLite or connects to hosted Supabase (PostgreSQL) with connection pooling.
- **Google OAuth & Supabase Auth**: Complete JWT token verification with user scoping across all tables and endpoints.
- **PWA Mobile Support**: Installable as a standalone progressive web app on Android, iOS, and Desktop with offline caching.
- **Automated Migration Tool**: Standalone migration script (`scripts/migrate_sqlite_to_supabase.py`) to move records from SQLite to Supabase Postgres.
- **Integrity Watchdogs**:
  - VS Code Extension: Detects large pasted code dumps.
  - Chrome Extension: Audits active YouTube tabs and flags off-task entertainment videos.
- **AI Viva Defense**: Gemini 2.5 Flash technical examination demanding proof defense before marking sessions verified.
