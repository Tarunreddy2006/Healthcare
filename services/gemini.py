
import httpx
import logging
from typing import Any, Dict, List, Optional

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{settings.GEMINI_MODEL}:generateContent"
    f"?key={settings.GEMINI_API_KEY}"
)

# ── Shared system instruction (persona context) ───────────────────────────────
SYSTEM_INSTRUCTION = (
    "You are Guardian AI's clinical reasoning engine. "
    "You receive structured medical history retrieved from a semantic memory system. "
    "Be concise, clinically accurate, and strictly evidence-based on the data provided. "
    "Never invent symptoms or diagnoses not present in the source data. "
    "Always prioritise patient safety."
)


async def _call_gemini(prompt: str, max_tokens: int = 512) -> Optional[str]:
    """
    POST a single-turn prompt to Gemini and return the text response.
    Returns None on any failure so callers can degrade gracefully.
    """
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,          # low temp → factual, less hallucination
            "topP": 0.9,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(GEMINI_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text: str = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            return text.strip() if text else None
    except httpx.HTTPStatusError as e:
        logger.error("Gemini HTTP error: %s — %s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.error("Gemini call failed: %s", str(e))
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCTOR'S BRIEFING
# ═══════════════════════════════════════════════════════════════════════════════

def _memories_to_context(memories: List[Dict[str, Any]]) -> str:
    """Convert Membrain memory objects into a numbered context block for the prompt."""
    lines = []
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        importance = mem.get("importance", 0)
        relevance = mem.get("relevance_score", 0)
        meta = mem.get("metadata", {})

        line = f"[{i}] (importance={importance:.2f}, relevance={relevance:.2f}) {content}"
        if meta.get("is_allergy_visit"):
            line += f"  ⚠ ALLERGY: {meta.get('allergen')} [{meta.get('allergen_severity')}]"
        lines.append(line)
    return "\n".join(lines) if lines else "No prior history available."


async def generate_doctor_briefing(
    patient_uid: str,
    memories: List[Dict[str, Any]],
    current_complaint: Optional[str] = None,
) -> str:
    """
    Generate a 3-sentence Doctor's Briefing from Membrain memories.

    Sentence structure:
      1. Patient's key chronic/recurring conditions and allergies.
      2. Most recent or relevant diagnosis and treatment.
      3. Critical precautions or patterns the doctor must know right now.
    """
    context = _memories_to_context(memories)
    complaint_clause = (
        f"The patient is presenting today with: '{current_complaint}'."
        if current_complaint
        else "No specific current complaint provided."
    )

    prompt = f"""
You are generating a DOCTOR'S BRIEFING for patient UID: {patient_uid}.
{complaint_clause}

Below is the patient's retrieved clinical history (ranked by importance and relevance):

{context}

---
Instructions:
- Write EXACTLY 3 sentences.
- Sentence 1: Summarise any known chronic conditions, allergies, or recurring symptoms.
- Sentence 2: Describe the most recent or most relevant visit, diagnosis, and treatment.
- Sentence 3: State any critical precautions, drug sensitivities, or actionable patterns the attending doctor must be aware of right now.
- Be specific, avoid vague language.
- If there are ALLERGY flags, always mention them.
- Do NOT add a preamble or labels like "Sentence 1:".
"""

    result = await _call_gemini(prompt, max_tokens=300)
    if result:
        return result

    # Graceful degradation — return a plain text summary without Gemini
    logger.warning("Gemini unavailable; falling back to raw memory summary.")
    top_3 = memories[:3]
    fallback = " | ".join(m.get("content", "")[:120] for m in top_3)
    return f"[Briefing unavailable — raw history excerpt]: {fallback}"


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFLICT ADVISORY SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

async def summarise_conflicts(
    conflicts: List[Dict[str, Any]],
    proposed_medicines: List[str],
) -> Optional[str]:
    """
    Produce a single concise advisory sentence summarising all detected conflicts.
    Used in the /check-conflict response as `ai_summary`.
    Returns None if there are no conflicts.
    """
    if not conflicts:
        return None

    conflict_text = "\n".join(
        f"- [{c.get('conflict_type','unknown').upper()}] {c.get('description','')} "
        f"(severity: {c.get('severity','unknown')}) → {c.get('recommendation','')}"
        for c in conflicts
    )
    drugs = ", ".join(proposed_medicines)

    prompt = f"""
The following conflicts were detected for a proposed prescription of: {drugs}

{conflict_text}

---
Write ONE concise advisory sentence (max 40 words) for the attending doctor, 
summarising the most critical risk and the most important recommended action.
Do not repeat the conflict list; synthesise it.
"""

    return await _call_gemini(prompt, max_tokens=100)