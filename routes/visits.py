
import json
import logging
from datetime import date

import aiomysql
from fastapi import APIRouter, Depends, HTTPException, status

from db import get_db
from models import SaveVisitRequest, SaveVisitResponse
from services.auth import get_current_patient_uid
from services.membrain import store_visit_memory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/save-visit", tags=["Visits"])


@router.post("", response_model=SaveVisitResponse)
async def save_visit(
    req: SaveVisitRequest,
    current_uid: str = Depends(get_current_patient_uid),
    db: aiomysql.Connection = Depends(get_db),
):
    """
    Save a full clinical visit.

    Dual-write architecture:
      • MySQL  → structured, queryable record
      • Membrain → semantic memory node with graph relationships
    """

    # ── Security: ensure the token belongs to the same patient ───────────────
    if req.patient_uid != current_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only save visits for your own patient record.",
        )

    # ── 1. Insert visit into MySQL ────────────────────────────────────────────
    medicines_json = json.dumps([m.model_dump() for m in req.medicines])

    async with db.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO visits
                (patient_uid, visit_date, symptoms, diagnosis,
                 medicines, precautions, attending_doctor)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                req.patient_uid,
                req.visit_date or date.today(),
                req.symptoms,
                req.diagnosis,
                medicines_json,
                req.precautions,
                req.attending_doctor,
            ),
        )
        visit_id: int = cur.lastrowid

    logger.info("Visit %d saved to MySQL for patient %s", visit_id, req.patient_uid)

    # ── 2. Insert allergy record if applicable ────────────────────────────────
    if req.is_allergy_visit and req.new_allergen:
        async with db.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO allergies (patient_uid, allergen, reaction, severity)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    severity = VALUES(severity)
                """,
                (
                    req.patient_uid,
                    req.new_allergen,
                    req.diagnosis,          # use diagnosis text as reaction description
                    req.allergen_severity or "moderate",
                ),
            )
        logger.info(
            "Allergy '%s' recorded for patient %s", req.new_allergen, req.patient_uid
        )

    # ── 3. Store semantic memory in Membrain ──────────────────────────────────
    membrain_node_id = await store_visit_memory(req, visit_id)

    # ── 4. Back-fill membrain_node_id in MySQL (best-effort) ─────────────────
    if membrain_node_id:
        async with db.cursor() as cur:
            await cur.execute(
                "UPDATE visits SET membrain_node_id = %s WHERE id = %s",
                (membrain_node_id, visit_id),
            )

    return SaveVisitResponse(
        success=True,
        visit_id=visit_id,
        membrain_node_id=membrain_node_id,
        message=(
            "Visit saved successfully with semantic memory."
            if membrain_node_id
            else "Visit saved to database. Membrain sync pending (will retry)."
        ),
    )