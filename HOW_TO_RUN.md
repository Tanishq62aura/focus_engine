# Focus Engine — How to Run & Setup Guide

A complete step-by-step operational guide for running, configuring, testing, and deploying the **Focus Engine Behavioral Execution & Accountability System**.

---

## Table of Contents
1. [Quick Start (Local Offline Mode)](#1-quick-start-local-offline-mode)
2. [Prerequisites & Installation](#2-prerequisites--installation)
3. [Environment Configuration (`.env`)](#3-environment-configuration-env)
4. [Running the Application](#4-running-the-application)
5. [Running Automated Tests](#5-running-automated-tests)
6. [Hosted Supabase & Google OAuth Setup](#6-hosted-supabase--google-oauth-setup)
7. [Database Migration (SQLite ➔ Supabase Postgres)](#7-database-migration-sqlite--supabase-postgres)
8. [Mobile & Desktop PWA Installation](#8-mobile--desktop-pwa-installation)
9. [Setting Up Watchdogs](#9-setting-up-watchdogs)
   - [VS Code Integrity Watchdog Extension](#vs-code-integrity-watchdog-extension)
   - [YouTube Chrome Watchdog Extension](#youtube-chrome-watchdog-extension)
10. [Troubleshooting & FAQ](#10-troubleshooting--faq)

---

## 1. Quick Start (Local Offline Mode)

To get started immediately with zero external dependencies (uses local SQLite and dev authentication bypass):

```bash
# 1. Navigate to the project directory
cd d:/new_p/focus_engine

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the FastAPI server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open your browser to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

The interactive Command HUD will load immediately with all features enabled in Dev Mode (`dev-user`).

---

## 2. Prerequisites & Installation

### Requirements
- **Python**: 3.10+ (compatible with Python 3.10, 3.11, 3.12, 3.13, 3.14+)
- **Operating System**: Windows, macOS, or Linux
- **Web Browser**: Chrome, Edge, Brave, Safari, or Firefox (Chromium-based recommended for PWA and extension support)

### Install Python Packages
```bash
pip install -r requirements.txt
```

Core libraries installed:
- `fastapi` & `uvicorn` — REST API framework and ASGI web server
- `sqlalchemy` & `psycopg2-binary` — ORM supporting SQLite and hosted Supabase PostgreSQL with connection pooling
- `pydantic` — Data validation and schema definitions
- `python-jose[cryptography]` — JWT verification for Supabase / Google OAuth
- `google-genai` — Gemini AI engine for technical viva examinations and excuse auditing
- `python-dotenv` — Automatic `.env` loading

---

## 3. Environment Configuration (`.env`)

A default `.env` file is included for offline development. You can review all options in [`.env.example`](file:///d:/new_p/focus_engine/.env.example):

```ini
# ===========================================================================
# DATABASE LAYER
# ===========================================================================
# Local SQLite default (offline development):
DATABASE_URL=sqlite:///./focus_system.db

# Hosted Supabase PostgreSQL connection string (Direct port 5432 or Pooler port 6543):
# DATABASE_URL=postgresql+psycopg2://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres

# ===========================================================================
# AUTHENTICATION & SUPABASE OAUTH SETTINGS
# ===========================================================================
# Set to 'true' for local testing without requiring Supabase JWTs
# Set to 'false' to enforce Google OAuth & Supabase authentication
AUTH_DISABLED=true

# Default user ID used when AUTH_DISABLED=true
DEV_USER_ID=dev-user

# Supabase Project credentials (from Project Settings -> API)
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=your_anon_key_here
SUPABASE_JWT_SECRET=your_jwt_secret_here

# ===========================================================================
# GEMINI AI VIVA & EXCUSE AUDITOR
# ===========================================================================
# (Optional: If omitted, built-in heuristic fallback logic is used)
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## 4. Running the Application

### Development Mode (with Live Reload)
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Production / Network Mode
If you want to access the app from your mobile phone on the same Wi-Fi network:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*(Access via `http://<YOUR_COMPUTER_LOCAL_IP>:8000` from your phone).*

### Headless Windows Background Daemon (with Cloudflare Tunnel)
To run the Focus Engine and Cloudflare Tunnel silently in the background without keeping open terminal windows:

- **Launch silently**: Run or double-click `scripts/launch_silent.vbs`. Both the Uvicorn backend (`0.0.0.0:8000`) and `cloudflared.exe` tunnel launch completely hidden. Logs are written to `logs/backend.log` and `logs/tunnel.log`.
- **Get Active Public URL**: Run `python scripts/get_tunnel_url.py` (or `.\scripts\get_tunnel_url.ps1`) to print the active `*.trycloudflare.com` tunnel URL.
- **Stop services**: Run `scripts/stop_engine.bat` to safely find and terminate both the Uvicorn backend PID and `cloudflared.exe`.
- **Install autostart on Windows login**: Run `scripts/install_autostart.bat` to create a shortcut in your Windows Startup directory (`shell:startup`) pointing to `scripts/launch_silent.vbs`.

---

## 5. Running Automated Tests

### Full End-to-End Test Suite
With the server running on `http://127.0.0.1:8000`:
```bash
python e2e_test.py
```

The test pipeline automatically executes all 9 stages:
1. `[PASS]` Task creation with 3-tier goals (min, target, stretch)
2. `[PASS]` Focus session initialization with latency calculation
3. `[PASS]` Distraction logging (phone, youtube, overthinking)
4. `[PASS]` YouTube watchdog audit — off-task video flagging
5. `[PASS]` YouTube watchdog audit — on-task tutorial approval
6. `[PASS]` Focus session completion and focused seconds calculation
7. `[PASS]` Proof submission & AI viva question generation
8. `[PASS]` Viva answer grading and verification verdict (`PASS` / `FAIL`)
9. `[PASS]` Today's metrics report aggregation (`/reports/today`)

### Running against Remote / Staging API with Auth:
```bash
# PowerShell:
$env:FOCUS_API_BASE="https://your-api-domain.com"
$env:FOCUS_AUTH_TOKEN="your_jwt_bearer_token"
python e2e_test.py

# Bash / Linux / macOS:
FOCUS_API_BASE="https://your-api-domain.com" FOCUS_AUTH_TOKEN="your_jwt_token" python e2e_test.py
```

---

## 6. Hosted Supabase & Google OAuth Setup

To migrate from local development to hosted Supabase PostgreSQL:

### Step 1: Create a Supabase Project
1. Go to [supabase.com](https://supabase.com) and create a new project.
2. Under **Project Settings -> Database**, find your Connection URI:
   ```text
   postgresql://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
   ```
3. Under **Project Settings -> API**, copy:
   - **Project URL** (`https://[PROJECT-REF].supabase.co`)
   - **anon / public key**
   - **JWT Secret**

### Step 2: Enable Google OAuth in Supabase
1. Go to **Authentication -> Providers -> Google** in your Supabase dashboard.
2. Toggle Google **ON**.
3. Create OAuth 2.0 Credentials in [Google Cloud Console](https://console.cloud.google.com/apis/credentials):
   - Set Authorized redirect URI to: `https://[PROJECT-REF].supabase.co/auth/v1/callback`
4. Copy Client ID and Client Secret into Supabase and save.

### Step 3: Update `.env`
```ini
DATABASE_URL=postgresql+psycopg2://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres
AUTH_DISABLED=false
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=YOUR_ANON_KEY
SUPABASE_JWT_SECRET=YOUR_JWT_SECRET
```

Restart uvicorn. The Focus Engine is now running entirely on hosted Postgres with multi-user isolation!

---

## 7. Database Migration (SQLite ➔ Supabase Postgres)

If you have existing sessions, tasks, or excuses in `focus_system.db` that you want to move to Supabase:

### Test First with `--dry-run`:
```bash
python scripts/migrate_sqlite_to_supabase.py --target "postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres" --dry-run
```

### Execute the Live Migration:
```bash
python scripts/migrate_sqlite_to_supabase.py --target "postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres"
```

The script will:
- Validate connection to both databases.
- Create Postgres tables automatically (`tasks`, `schedules`, `focus_sessions`, `distraction_logs`, `excuse_logs`).
- Backfill `user_id` for legacy records (defaults to `--default-user-id dev-user`).
- Convert naive timestamps into timezone-aware `TIMESTAMPTZ`.
- Skip duplicates safely (idempotent).

---

## 8. Mobile & Desktop PWA Installation

Focus Engine is a fully compliant **Progressive Web App (PWA)** with offline shell caching.

### On Desktop (Chrome / Edge / Brave):
1. Navigate to `http://127.0.0.1:8000` (or your hosted URL).
2. Look at the right side of the URL address bar: click the **Install icon** (🖥️ or ⊕).
3. Focus Engine will open as an independent window with a native titlebar, desktop shortcut, and dark slate theme.

### On Mobile (Android / Chrome):
1. Open the app URL on Chrome for Android.
2. Tap the `⋮` (menu) button -> tap **"Add to Home screen"** or **"Install app"**.
3. The app will launch in full-screen standalone mode with no browser URL bar.

### On iOS (Safari):
1. Open the URL in Safari.
2. Tap the **Share** button (box with upward arrow).
3. Tap **"Add to Home Screen"**.

---

## 9. Setting Up Watchdogs

### VS Code Integrity Watchdog Extension
Detects large code paste bursts (e.g., pasting generated AI code without understanding) and automatically demands a line-by-line viva justification.

1. Open VS Code.
2. Open the directory `d:/new_p/focus_engine/vscode_integrity_extension`.
3. Press `F5` to open an **Extension Development Host** window (or package with `vsce package`).
4. Ensure Focus Engine backend is running on `http://127.0.0.1:8000`.
5. The extension status bar item `Focus Engine: IDLE` will appear in the bottom-right.
6. When a focus session is active, any paste > 150 characters will be reported to `POST /sessions/active/paste-event` and flagged for viva defense.

### YouTube Chrome Watchdog Extension
Monitors active YouTube video titles during focus sessions. Approves educational/coding tutorials and automatically logs distractions for off-task videos.

1. Open Chrome or Edge.
2. Navigate to `chrome://extensions/`.
3. Enable **Developer mode** (toggle in the top-right corner).
4. Click **Load unpacked** in the top-left.
5. Select the folder: `d:/new_p/focus_engine/browser_youtube_watchdog`.
6. The extension is now active. Whenever you visit YouTube during an active focus session, the service worker verifies the video with `POST /sessions/active/youtube-audit`.

### Mobile Dispatcher & Samsung Modes/Routines Automation
Dispatches asynchronous notifications via `ntfy.sh` when focus blocks start and end, automatically triggering DND (Do Not Disturb) and Grayscale on your phone.

1. Install the free **ntfy** app on your phone (Android or iOS).
2. Subscribe to the topic: `focus_engine_alerts` (or your configured `NTFY_TOPIC` in `.env`).
3. (Optional Samsung Automation): In **Modes and Routines** on Samsung:
   - *If*: Notification received from ntfy containing `FOCUS_START`
   - *Then*: Turn on Do Not Disturb + Grayscale / Bedtime mode.
   - *If*: Notification received containing `FOCUS_END`
   - *Then*: Disable Do Not Disturb + restore color.

---

## 10. Troubleshooting & FAQ

#### Q: I get `401 Unauthorized` when calling the API.
- If you are running locally without Supabase tokens, verify that `AUTH_DISABLED=true` is set in your `.env` file.
- If you are testing with auth enabled, ensure you pass the header: `Authorization: Bearer <YOUR_TOKEN>`.

#### Q: How do I enable real Gemini AI viva questions instead of heuristic fallbacks?
- Obtain an API key from [Google AI Studio](https://aistudio.google.com/).
- Set `GEMINI_API_KEY=AIzaSy...` in your `.env` file.

#### Q: Can I run without PostgreSQL?
- Yes! Focus Engine works 100% offline with SQLite (`DATABASE_URL=sqlite:///./focus_system.db`). PostgreSQL/Supabase is only required when you want cloud sync or hosted multi-device access.

#### Q: Where are the database files stored?
- SQLite file: `d:/new_p/focus_engine/focus_system.db`.
- Backups or fresh resets: You can safely delete `focus_system.db` to start clean; the app will recreate all tables on startup.
