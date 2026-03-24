"""
routes/auth.py — Guardian AI
POST /login  — Authenticate UID + DOB against MySQL.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
import aiomysql

from db import get_db, hash_dob
from models import LoginRequest, LoginResponse, PatientInfo
from services.auth import create_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/login", tags=["Authentication"])


@router.post("", response_model=LoginResponse)
async def login(req: LoginRequest, db: aiomysql.Connection = Depends(get_db)):
    """
    Authenticate a patient by UID + DOB.

    - DOB is never stored in plain text — only its SHA-256 hash.
    - Returns a short-lived JWT on success.
    - Returns 401 on any mismatch (we deliberately don't distinguish
      "UID not found" from "wrong DOB" to prevent enumeration attacks).
    """
    dob_hash = hash_dob(req.dob)

    # ── MySQL lookup ──────────────────────────────────────────────────────────
    async with db.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            """
            SELECT uid, full_name, gender
            FROM   patients
            WHERE  uid = %s
            AND    dob_hash = %s
            LIMIT  1
            """,
            (req.uid, dob_hash),
        )
        row = await cur.fetchone()

    # ── Validate ──────────────────────────────────────────────────────────────
    if not row:
        logger.warning("Failed login attempt for UID: %s", req.uid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid UID or Date of Birth.",
        )

    # ── Issue token ───────────────────────────────────────────────────────────
    token = create_access_token(patient_uid=row["uid"])
    logger.info("Successful login for patient: %s", row["uid"])

    return LoginResponse(
        success=True,
        message="Login successful.",
        access_token=token,
        patient=PatientInfo(
            uid=row["uid"],
            full_name=row["full_name"],
            gender=row["gender"],
        ),
    )