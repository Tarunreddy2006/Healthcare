"""
services/auth_service.py — Guardian AI
Lightweight JWT-based session tokens.
Issued on successful /login; required as Bearer token on all other endpoints.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings

settings = get_settings()
_bearer = HTTPBearer()

ALGORITHM = "HS256"


def create_access_token(patient_uid: str) -> str:
    """Create a signed JWT containing the patient's UID."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": patient_uid,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """
    Decode and validate a JWT. Returns the patient_uid ('sub') on success,
    None on any error.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def get_current_patient_uid(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """
    FastAPI dependency. Validates the Bearer token and returns the patient UID.
    Raise 401 if token is missing or invalid.
    """
    uid = decode_access_token(credentials.credentials)
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return uid