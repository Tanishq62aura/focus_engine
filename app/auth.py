"""
app/auth.py
Authentication and user scoping for Focus Engine.
Supports Supabase JWT tokens, Google OAuth via Supabase, and a mock/dev bypass mode.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AUTH_DISABLED = os.getenv("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
DEV_USER_ID = os.getenv("DEV_USER_ID", "dev-user").strip()

# HTTP Bearer dependency (auto_error=False to allow dev bypass)
bearer_scheme = HTTPBearer(auto_error=False)

# Optional JWT engine import (support both python-jose and PyJWT)
try:
    from jose import JWTError, jwt
except ImportError:
    try:
        import jwt
        JWTError = Exception
    except ImportError:
        jwt = None
        JWTError = Exception


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a JWT access token for authentication."""
    secret = SUPABASE_JWT_SECRET or "dev-insecure-local-only-key"
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=7))
    to_encode = data.copy()
    to_encode.update({"iat": int(now.timestamp()), "exp": int(expire.timestamp())})
    if "aud" not in to_encode:
        to_encode["aud"] = "authenticated"
    if "role" not in to_encode:
        to_encode["role"] = "authenticated"
    return jwt.encode(to_encode, secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """
    Decodes and validates a Supabase / Google OAuth JWT token.
    Raises HTTPException on signature or expiry failures.
    """
    if not jwt:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT library not available on server.",
        )

    # 1. If SUPABASE_JWT_SECRET is provided, verify HS256 signature
    if SUPABASE_JWT_SECRET:
        try:
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return payload
        except JWTError as e:
            logger.warning("Supabase JWT validation failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired authentication token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            logger.warning("Unexpected JWT decode error: %s", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate authentication credentials.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 2. If no JWT secret is configured and AUTH_DISABLED is true, decode unverified payload
    if AUTH_DISABLED:
        try:
            return jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
        except Exception:
            return {"sub": DEV_USER_ID}

    # If auth is enabled but no secret is configured, warn and reject
    logger.error("SUPABASE_JWT_SECRET is not configured on the server.")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Server authentication secret is not configured.",
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """
    FastAPI dependency that extracts and validates the authenticated user ID.
    If AUTH_DISABLED=true, returns DEV_USER_ID (or decodes token if provided).
    """
    # ── Development Bypass Mode ─────────────────────────────────────────────
    if AUTH_DISABLED:
        if credentials and credentials.credentials:
            try:
                payload = decode_token(credentials.credentials)
                user_id = payload.get("sub") or payload.get("user_id") or payload.get("email")
                if user_id:
                    return str(user_id)
            except Exception:
                pass
        return DEV_USER_ID

    # ── Enforced Authentication ─────────────────────────────────────────────
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token)

    user_id = payload.get("sub") or payload.get("user_id") or payload.get("email")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token: missing subject (sub) claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return str(user_id)
