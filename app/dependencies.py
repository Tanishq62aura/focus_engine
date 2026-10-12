"""
app/dependencies.py
FastAPI dependency injection provider for Focus Engine.
Extracts authenticated user context from JWT tokens and provides database sessions.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth import (
    AUTH_DISABLED,
    DEV_USER_ID,
    bearer_scheme,
    decode_token,
    get_current_user as auth_get_current_user,
)
from app.database import get_db

logger = logging.getLogger(__name__)


async def get_current_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """
    Decodes the Bearer JWT to extract the unique subject claim (user_id / UUID).
    Enforces clean-slate isolation across user accounts.
    Only allows dev-user fallback if explicitly configured in development mode.
    """
    user_id = await auth_get_current_user(credentials)
    return str(user_id)


# Backward-compatible alias
get_current_user = get_current_user_id
