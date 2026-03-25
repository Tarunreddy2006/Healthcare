"""
routes/register.py — Guardian AI

POST /register — Register a new patient into the system.

Flow:
  1. Accept patient details (UID, name, DOB, gender)
  2. Hash DOB for secure storage
  3. Insert into MySQL
  4. Prevent duplicate UID
  5. Return success response
"""

import logging
import aiomysql
from fastapi import APIRouter, Depends, HTTPException, status

from db import get_db, hash_dob
from models import RegisterRequest, RegisterResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/register", tags=["Registration"])


@router.post("", response_model=RegisterResponse)
async def register(
    req: RegisterRequest,
    db: aiomysql.Connection = Depends(get_db),
):
    """
    Register a new patient.

    - UID must be unique
    - DOB is stored as SHA-256 hash
    """

    dob_hash = hash_dob(req.dob)

    async with db.cursor(aiomysql.DictCursor) as cur:
        # ── Check if UID already exists ───────────────────────────────
        await cur.execute(
            "SELECT uid FROM patients WHERE uid = %s LIMIT 1",
            (req.uid,),
        )
        existing = await cur.fetchone()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Patient with this UID already exists.",
            )

        # ── Insert new patient ────────────────────────────────────────
        await cur.execute(
            """
            INSERT INTO patients (uid, full_name, gender, dob_hash)
            VALUES (%s, %s, %s, %s)
            """,
            (req.uid, req.full_name, req.gender, dob_hash),
        )

    logger.info("New patient registered: %s", req.uid)

    return RegisterResponse(
        success=True,
        message="Patient registered successfully.",
        uid=req.uid,
    )