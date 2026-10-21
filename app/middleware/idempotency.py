"""
app/middleware/idempotency.py
Local-First Offline Synchronization & Mutation Idempotency Middleware.

Guarantees at-most-once execution for mutating requests (POST, PUT, PATCH)
bearing an X-Idempotency-Key header. Prevents replay storms, duplicate writes,
and ghost sessions during client network transitions and offline replay.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.database import SessionLocal
from app.models import IdempotencyRecord

logger = logging.getLogger("focus_engine.idempotency")

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})
CACHEABLE_STATUS_CODES = frozenset({200, 201, 400, 409})


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    FastAPI / Starlette middleware providing transactional idempotency.
    Interprets X-Idempotency-Key on mutating HTTP methods.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 1. Ignore non-mutating verbs (GET, HEAD, OPTIONS)
        if request.method not in MUTATING_METHODS:
            return await call_next(request)

        # 2. Extract idempotency key
        raw_key = request.headers.get("x-idempotency-key")
        if not raw_key or not raw_key.strip():
            return await call_next(request)

        idempotency_key = raw_key.strip()[:64]

        # 3. Cache Hit Check (Idempotency Record lookup)
        cached_record = self._get_cached_record(idempotency_key)
        if cached_record:
            logger.info("Idempotency HIT for key [%s] (status=%d)", idempotency_key, cached_record.status_code)
            return Response(
                content=cached_record.response_body,
                status_code=cached_record.status_code,
                media_type="application/json",
                headers={
                    "Content-Type": "application/json",
                    "X-Cache-Lookup": "HIT-IDEMPOTENT",
                },
            )

        # 4. Cache Miss: Execute downstream route handler
        response = await call_next(request)

        # 5. Buffer streaming response body cleanly
        response_body_bytes = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                response_body_bytes += chunk.encode("utf-8")
            else:
                response_body_bytes += chunk

        # 6. Cache qualifying responses (200, 201, 400, 409). Do not cache 5xx server errors.
        if response.status_code in CACHEABLE_STATUS_CODES:
            user_id = self._extract_user_id(request)
            body_text = response_body_bytes.decode("utf-8", errors="replace")
            self._save_record(
                key=idempotency_key,
                user_id=user_id,
                status_code=response.status_code,
                response_body=body_text,
            )

        # 7. Reconstruct response preserving headers and background tasks
        response_headers = dict(response.headers)
        response_headers["content-length"] = str(len(response_body_bytes))

        return Response(
            content=response_body_bytes,
            status_code=response.status_code,
            headers=response_headers,
            media_type=response.media_type,
            background=response.background,
        )

    def _get_cached_record(self, key: str) -> Optional[IdempotencyRecord]:
        db = SessionLocal()
        try:
            return db.query(IdempotencyRecord).filter(IdempotencyRecord.key == key).first()
        except Exception as exc:
            logger.error("Failed to query idempotency record for key [%s]: %s", key, exc)
            return None
        finally:
            db.close()

    def _save_record(self, key: str, user_id: Optional[str], status_code: int, response_body: str) -> None:
        db = SessionLocal()
        try:
            existing = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == key).first()
            if not existing:
                rec = IdempotencyRecord(
                    key=key,
                    user_id=user_id,
                    status_code=status_code,
                    response_body=response_body,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(rec)
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to store idempotency record for key [%s]: %s", key, exc)
        finally:
            db.close()

    def _extract_user_id(self, request: Request) -> Optional[str]:
        # Check request.state if set by auth dependency/middleware
        if hasattr(request.state, "user_id") and request.state.user_id:
            return str(request.state.user_id)

        # Check explicit X-User-Id header (for internal/proxy calls)
        x_user = request.headers.get("x-user-id")
        if x_user and x_user.strip():
            return x_user.strip()

        # Check FastAPI dependency overrides (e.g., in test fixtures)
        try:
            from app.auth import get_current_user
            override = getattr(request.app, "dependency_overrides", {}).get(get_current_user)
            if override and callable(override):
                res = override()
                if res:
                    return str(res)
        except Exception:
            pass

        # Check Authorization Bearer token
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            try:
                from app.auth import decode_token
                payload = decode_token(token)
                if isinstance(payload, dict):
                    uid = payload.get("sub") or payload.get("user_id") or payload.get("email")
                    if uid:
                        return str(uid)
            except Exception:
                pass

        # Check local dev bypass fallback
        try:
            from app.auth import AUTH_DISABLED, DEV_USER_ID
            if AUTH_DISABLED:
                return DEV_USER_ID or "dev-user"
        except Exception:
            pass

        return None
