# Focus Engine — API & Database Reference

## Database Schema (PostgreSQL Supabase / SQLite)
All tables feature `user_id` string scoping and timezone-aware UTC timestamps (`TIMESTAMPTZ` in Postgres):
- **tasks**: `id`, `user_id`, `title`, `category`, `priority`, `estimated_duration_min`, `min_goal`, `target_goal`, `stretch_goal`, `is_completed`, `created_at`
- **schedules**: `id`, `user_id`, `task_id`, `scheduled_start`, `scheduled_end`, `status`, `snooze_count`
- **focus_sessions**: `id`, `user_id`, `task_id`, `schedule_id`, `is_five_min_intervention`, `started_at`, `ended_at`, `start_latency_min`, `actual_focused_seconds`, `verification_status`, `pasted_burst_detected`, `proof_text`, `viva_question`, `viva_answer`, `viva_feedback`, `notes`
- **distraction_logs**: `id`, `user_id`, `session_id`, `timestamp`, `interruption_type`
- **excuse_logs**: `id`, `user_id`, `schedule_id`, `timestamp`, `stated_reason`, `is_valid_blocker`, `ai_audit_response`

## Authentication & Headers
- `Authorization: Bearer <JWT_TOKEN>` (Supabase HS256 JWT or Google OAuth session)
- Development bypass: When `AUTH_DISABLED=true` in `.env`, defaults to user `"dev-user"`.

## REST Endpoints
- `GET /auth/status` — Check auth status and active user
- `POST /tasks` — Create task with 3-tier goals (user-scoped)
- `GET /tasks` — List current user tasks
- `POST /schedules` — Timeblock a task
- `POST /schedules/{id}/snooze` — Snooze task by 10 min
- `POST /excuses/audit` — Analyze stated reason, apply 5-minute rule
- `POST /sessions/start` — Launch focus timer with latency tracking
- `POST /sessions/{id}/end` — Stop session, set status to pending_viva
- `POST /sessions/active/paste-event` — VS Code integrity watchdog paste logger
- `POST /sessions/active/youtube-audit` — Browser watchdog off-task video logger
- `POST /distractions` — 1-tap distraction log (phone, youtube, overthinking)
- `POST /sessions/{id}/request-viva` — Generate AI technical viva question
- `POST /sessions/{id}/submit-viva` — Grade answer, mark verified or rejected
- `GET /reports/today` — Aggregated daily metrics for current user
- `GET /reports/friction-analysis?days=7` — Friction & Habit Analytics Engine (System Friction Index, category resistance breakdown, high friction culprit)

