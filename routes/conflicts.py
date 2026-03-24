"""
routes/conflicts.py — Guardian AI
POST /check-conflict — Proactively flag prescription conflicts against patient memory.

Flow:
  1. Validate JWT
  2. Call Membrain /memories/conflict with proposed medicines
  3. Map raw Membrain signals → typed ConflictDetail objects
  4. Ask Gemini to synthesise a single advisory sentence
  5. Return full conflict list + AI advisory
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from models import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    ConflictDetail,
)
from services.auth import get_current_patient_uid
from services.membrain import get_conflict_signals
from services.gemini import summarise_conflicts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/check-conflict", tags=["Conflict Detection"])


def _map_conflict(raw: dict) -> ConflictDetail:
    """
    Translate a raw Membrain conflict signal into a typed ConflictDetail.
    Membrain may use different field names across versions; we normalise here.
    """
    return ConflictDetail(
        conflict_type=raw.get("conflict_type") or raw.get("type", "unknown"),
        severity=raw.get("severity", "warning"),
        description=raw.get("description") or raw.get("message", "Conflict detected."),
        source_memory_id=raw.get("source_memory_id") or raw.get("node_id", ""),
        recommendation=raw.get("recommendation") or raw.get("action", "Consult a specialist."),
    )


def _prioritise(conflicts: List[ConflictDetail]) -> List[ConflictDetail]:
    """Sort conflicts: critical first, then warning, then info."""
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(conflicts, key=lambda c: order.get(c.severity, 99))


@router.post("", response_model=ConflictCheckResponse)
async def check_conflict(
    req: ConflictCheckRequest,
    current_uid: str = Depends(get_current_patient_uid),
):
    """
    Check proposed prescriptions against the patient's full Membrain memory graph.

    Detects:
      • Allergy conflicts  (e.g. prescribing Penicillin to a patient with a known allergy)
      • Drug-drug interactions (e.g. Warfarin + Aspirin)
      • Contraindications   (e.g. NSAIDs for a patient with a recorded renal condition)

    Returns a ranked list of ConflictDetail objects and a Gemini-generated advisory.
    """

    # ── Auth guard ────────────────────────────────────────────────────────────
    if req.patient_uid != current_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    if not req.proposed_medicines:
        return ConflictCheckResponse(
            success=True,
            patient_uid=req.patient_uid,
            has_conflicts=False,
            message="No medicines provided to check.",
        )

    # ── Membrain conflict analysis ────────────────────────────────────────────
    raw_signals = await get_conflict_signals(
        patient_uid=req.patient_uid,
        proposed_medicines=req.proposed_medicines,
        proposed_diagnosis=req.proposed_diagnosis,
    )

    # ── Map and rank ──────────────────────────────────────────────────────────
    conflicts: List[ConflictDetail] = _prioritise(
        [_map_conflict(s) for s in raw_signals]
    )
    has_conflicts = len(conflicts) > 0

    # ── Gemini advisory summary ───────────────────────────────────────────────
    ai_summary = None
    if has_conflicts:
        medicine_names = [m.name for m in req.proposed_medicines]
        ai_summary = await summarise_conflicts(
            [c.model_dump() for c in conflicts],
            medicine_names,
        )

    logger.info(
        "Conflict check for patient %s: %d conflict(s) found.",
        req.patient_uid, len(conflicts),
    )

    return ConflictCheckResponse(
        success=True,
        patient_uid=req.patient_uid,
        has_conflicts=has_conflicts,
        conflicts=conflicts,
        ai_summary=ai_summary,
        message=(
            f"{len(conflicts)} conflict(s) detected. Review before prescribing."
            if has_conflicts
            else "No conflicts detected. Safe to proceed."
        ),
    )