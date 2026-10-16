"""
app/routers/auth.py
Native Supabase user registration and account-scoped authentication router.
Supports GoTrue API and direct Supabase PostgreSQL auth synchronization.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import schemas
from app.auth import (
    AUTH_DISABLED,
    DEV_USER_ID,
    SUPABASE_JWT_SECRET,
    bearer_scheme,
    decode_token,
    get_current_user,
    jwt,
)
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://njxlltvrcmqinrnhlpgh.supabase.co")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()

# Try initializing official Supabase Client
_sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        logger.warning("Could not initialize Supabase client: %s", e)


def _generate_jwt(user_id: str, email: str) -> str:
    """Generates an authentic HS256 JWT compatible with Supabase auth."""
    secret = SUPABASE_JWT_SECRET
    if not secret:
        if AUTH_DISABLED:
            secret = "dev-insecure-local-only-key"
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="SUPABASE_JWT_SECRET must be configured in environment variables.",
            )
    if not jwt:
        raise HTTPException(status_code=500, detail="JWT library unavailable on server")
    
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=30)
    payload = {
        "sub": user_id,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@router.post("/signup", response_model=schemas.AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(data: schemas.AuthRequest, db: Session = Depends(get_db)):
    """
    Registers a new clean-slate user in Supabase Auth.
    Returns access token, user UUID, and email.
    """
    email = data.email.strip().lower()
    password = data.password

    if len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long.",
        )

    # 1. Try GoTrue client if configured
    if _sb_client is not None:
        try:
            res = _sb_client.auth.sign_up({"email": email, "password": password})
            if res.user:
                user_id = str(res.user.id)
                token = res.session.access_token if res.session else _generate_jwt(user_id, email)
                return schemas.AuthResponse(access_token=token, user_id=user_id, email=email)
        except Exception as e:
            logger.info("Supabase client sign_up failed (%s), using direct database sync.", e)

    # 2. Direct Supabase PostgreSQL Auth Synchronization
    try:
        # Check if user already exists
        existing = db.execute(
            text("SELECT id FROM auth.users WHERE lower(email) = :email"),
            {"email": email},
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email address already exists. Please sign in.",
            )

        user_id = str(uuid.uuid4())
        hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        now = datetime.now(timezone.utc)

        db.execute(
            text("""
                INSERT INTO auth.users (
                    id, aud, role, email, encrypted_password, 
                    email_confirmed_at, created_at, updated_at, 
                    raw_app_meta_data, raw_user_meta_data
                ) VALUES (
                    :id, 'authenticated', 'authenticated', :email, :password, 
                    :now, :now, :now, 
                    '{"provider":"email","providers":["email"]}', '{}'
                )
            """),
            {
                "id": user_id,
                "email": email,
                "password": hashed_password,
                "now": now,
            },
        )
        db.commit()

        token = _generate_jwt(user_id, email)
        logger.info("Clean-slate user registered: email=%s, user_id=%s", email, user_id)
        return schemas.AuthResponse(access_token=token, user_id=user_id, email=email)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to register user in Supabase auth: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database authentication error: {str(e)}",
        )


@router.post("/login", response_model=schemas.AuthResponse)
def login(data: schemas.AuthRequest, db: Session = Depends(get_db)):
    """
    Authenticates an existing user and issues a fresh session JWT.
    """
    email = data.email.strip().lower()
    password = data.password

    # 1. Try GoTrue client if configured
    if _sb_client is not None:
        try:
            res = _sb_client.auth.sign_in_with_password({"email": email, "password": password})
            if res.user:
                user_id = str(res.user.id)
                token = res.session.access_token if res.session else _generate_jwt(user_id, email)
                return schemas.AuthResponse(access_token=token, user_id=user_id, email=email)
        except Exception as e:
            logger.info("Supabase client sign_in failed (%s), using direct database sync.", e)

    # 2. Direct Supabase PostgreSQL Auth Synchronization
    try:
        row = db.execute(
            text("SELECT id, encrypted_password FROM auth.users WHERE lower(email) = :email"),
            {"email": email},
        ).fetchone()

        if not row or not row[1]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = str(row[0])
        encrypted_password = str(row[1])

        # Verify bcrypt hash
        if not bcrypt.checkpw(password.encode("utf-8"), encrypted_password.encode("utf-8")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = _generate_jwt(user_id, email)
        return schemas.AuthResponse(access_token=token, user_id=user_id, email=email)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to authenticate user: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed due to server error.",
        )


@router.get("/status")
def auth_status(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """
    Returns the current authentication and user session status.
    """
    if AUTH_DISABLED:
        return {
            "authenticated": True,
            "auth_disabled": True,
            "user_id": DEV_USER_ID,
            "email": "dev-user@local",
        }

    if not credentials or not credentials.credentials:
        return {
            "authenticated": False,
            "auth_disabled": False,
            "user_id": None,
            "email": None,
        }

    try:
        payload = decode_token(credentials.credentials)
        uid = payload.get("sub") or payload.get("user_id") or payload.get("email") or DEV_USER_ID
        email = payload.get("email") or (uid if "@" in str(uid) else None)
        return {
            "authenticated": True,
            "auth_disabled": False,
            "user_id": str(uid),
            "email": email,
        }
    except Exception:
        return {
            "authenticated": False,
            "auth_disabled": False,
            "user_id": None,
            "email": None,
        }


DEV_MODE = os.getenv("DEV_MODE", "true").lower() in ("true", "1", "yes")

@router.post("/dev-token")
def issue_dev_token():
    """Generates an authentic HS256 development JWT for local testing and dev mode."""
    if not DEV_MODE and not AUTH_DISABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Development bypass token is disabled in production.",
        )
    token = _generate_jwt(DEV_USER_ID, "dev-user@local")
    return {"access_token": token, "token_type": "bearer", "user_id": DEV_USER_ID}
