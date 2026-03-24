"""
routes/summary.py — Guardian AI
POST /get-summary — Retrieve semantic memories and generate a Doctor's Briefing.

Flow:
  1. Validate JWT
  2. Call Membrain /memories/search with optional current-complaint context
  3. Feed retrieved memories to Gemini → 3-sentence Doctor's Briefing
  4. Return briefing + metadata
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from models import SummaryRequest, SummaryResponse
from services.auth import get_current_patient_uid
from services.membrain import retrieve_patient_memory
from services.gemini import generate_doctor_briefing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/get-summary", tags=["Summary"])


@router.post("", response_model=SummaryResponse)
async def get_summary(
    req: SummaryRequest,
    current_uid: str = Depends(get_current_patient_uid),
):
    """
    Return a Gemini-generated 3-sentence Doctor's Briefing.

    The `context_hint` (e.g. "patient reports chest pain today") is passed
    to Membrain's semantic search so that the most *relevant* memories for
    the current visit surface first, not just the most recent ones.

    This is the core value of AlphaNimble's 'agentic semantic memory':
    linking past visits to present symptoms dynamically.
    """

    # ── Auth guard: a patient can only pull their own summary ─────────────────
    if req.patient_uid != current_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: cannot retrieve another patient's summary.",
        )

    # ── Retrieve memories from Membrain ───────────────────────────────────────
    memories = await retrieve_patient_memory(
        patient_uid=req.patient_uid,
        query=req.context_hint,
        top_k=10,
    )

    if not memories:
        logger.warning("No memories found in Membrain for patient %s", req.patient_uid)
        return SummaryResponse(
            success=True,
            patient_uid=req.patient_uid,
            briefing=(
                "No prior clinical history found for this patient in the memory system. "
                "This may be their first visit or data is still being indexed."
            ),
            memories_retrieved=0,
        )

    # ── Generate Doctor's Briefing via Gemini ─────────────────────────────────
    briefing = await generate_doctor_briefing(
        patient_uid=req.patient_uid,
        memories=memories,
        current_complaint=req.context_hint,
    )

    logger.info(
        "Summary generated for patient %s using %d memories.",
        req.patient_uid, len(memories)
    )

    return SummaryResponse(
        success=True,
        patient_uid=req.patient_uid,
        briefing=briefing,
        memories_retrieved=len(memories),
    )