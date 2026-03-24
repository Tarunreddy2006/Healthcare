
import httpx
import logging
from typing import Any, Dict, List, Optional
from datetime import date

from config import get_settings
from models import Medicine, SaveVisitRequest

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Membrain node / relationship type constants ───────────────────────────────
class NodeType:
    PATIENT = "patient_profile"
    VISIT = "clinical_visit"
    SYMPTOM = "symptom"
    DIAGNOSIS = "diagnosis"
    MEDICINE = "medicine"
    PRECAUTION = "precaution"
    ALLERGY = "allergy"


class RelType:
    HAS_VISIT = "HAS_VISIT"
    PRESENTED_WITH = "PRESENTED_WITH"
    DIAGNOSED_WITH = "DIAGNOSED_WITH"
    PRESCRIBED = "PRESCRIBED"
    REQUIRED_PRECAUTION = "REQUIRED_PRECAUTION"
    HAS_ALLERGY = "HAS_ALLERGY"
    FOLLOWS_FROM = "FOLLOWS_FROM"      # links sequential visits
    TRIGGERED_BY = "TRIGGERED_BY"     # symptom recurrence linkage


# ── HTTP client factory ───────────────────────────────────────────────────────
def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.MEMBRAIN_BASE_URL,
        headers={
            "Authorization": f"Bearer {settings.MEMBRAIN_API_KEY}",
            "Content-Type": "application/json",
            "X-Client": "GuardianAI/1.0",
        },
        timeout=15.0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORTANCE SCORE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _importance_for_visit(req: SaveVisitRequest) -> float:
    """
    Derive an importance score for the entire visit node.
    Allergy visits are always CRITICAL (1.0).
    Visits with severe/chronic keywords are HIGH (0.85).
    Everything else defaults to MEDIUM (0.65).
    """
    if req.is_allergy_visit:
        return settings.IMPORTANCE_CRITICAL

    severe_keywords = {
        "cancer", "cardiac", "heart", "stroke", "diabetes", "chronic",
        "renal", "failure", "anaphylaxis", "seizure", "hypertension",
    }
    text = (req.symptoms + " " + req.diagnosis).lower()
    if any(kw in text for kw in severe_keywords):
        return settings.IMPORTANCE_HIGH

    return settings.IMPORTANCE_MEDIUM


def _decay_config(importance: float) -> Dict[str, Any]:
    """
    Return Membrain decay parameters.
    Critical memories have a high floor so they never fade below 90 % relevance.
    """
    if importance >= settings.IMPORTANCE_CRITICAL:
        return {"strategy": "floor", "floor_value": settings.DECAY_FLOOR_CRITICAL}
    if importance >= settings.IMPORTANCE_HIGH:
        return {"strategy": "slow_linear", "half_life_days": 365}
    return {"strategy": "exponential", "half_life_days": 90}


# ═══════════════════════════════════════════════════════════════════════════════
#  STORE VISIT MEMORY
# ═══════════════════════════════════════════════════════════════════════════════

async def store_visit_memory(req: SaveVisitRequest, visit_db_id: int) -> Optional[str]:
    """
    Send a full clinical visit to Membrain as a semantic memory node.

    Returns the `node_id` assigned by Membrain, which we persist in MySQL
    so we can reference it later.

    Membrain payload structure (AlphaNimble spec):
    {
        "namespace": "<patient_uid>",
        "node": { ... visit content ... },
        "relationships": [ ... graph edges ... ],
        "importance": 0.0–1.0,
        "decay": { ... }
    }
    """
    importance = _importance_for_visit(req)
    decay = _decay_config(importance)

    # ── Build the central visit node ─────────────────────────────────────────
    medicines_text = "; ".join(m.to_text() for m in req.medicines) or "None"
    visit_node = {
        "type": NodeType.VISIT,
        "external_id": f"visit_{visit_db_id}",
        "content": (
            f"Visit on {req.visit_date}. "
            f"Symptoms: {req.symptoms}. "
            f"Diagnosis: {req.diagnosis}. "
            f"Medicines: {medicines_text}. "
            f"Precautions: {req.precautions or 'None'}."
        ),
        "metadata": {
            "visit_date": str(req.visit_date),
            "attending_doctor": req.attending_doctor,
            "is_allergy_visit": req.is_allergy_visit,
            "allergen": req.new_allergen,
            "allergen_severity": req.allergen_severity,
        },
    }

    # ── Build graph relationships ─────────────────────────────────────────────
    relationships = [
        # Patient root → this visit
        {
            "from_node": f"patient_{req.patient_uid}",
            "to_node": f"visit_{visit_db_id}",
            "type": RelType.HAS_VISIT,
            "weight": importance,
        }
    ]

    # Symptom sub-nodes (one per sentence/phrase for granular recall)
    for symptom_fragment in req.symptoms.split(","):
        fragment = symptom_fragment.strip()
        if fragment:
            relationships.append({
                "from_node": f"visit_{visit_db_id}",
                "to_node": {"type": NodeType.SYMPTOM, "content": fragment},
                "type": RelType.PRESENTED_WITH,
                "weight": settings.IMPORTANCE_MEDIUM,
            })

    # Diagnosis node
    relationships.append({
        "from_node": f"visit_{visit_db_id}",
        "to_node": {"type": NodeType.DIAGNOSIS, "content": req.diagnosis},
        "type": RelType.DIAGNOSED_WITH,
        "weight": importance,
    })

    # Medicine nodes — each prescription is a separate node for conflict matching
    for med in req.medicines:
        relationships.append({
            "from_node": f"visit_{visit_db_id}",
            "to_node": {
                "type": NodeType.MEDICINE,
                "content": med.name,
                "metadata": {"dosage": med.dosage, "duration": med.duration},
            },
            "type": RelType.PRESCRIBED,
            "weight": settings.IMPORTANCE_MEDIUM,
        })

    # Allergy node (CRITICAL importance + no-decay flag)
    if req.is_allergy_visit and req.new_allergen:
        relationships.append({
            "from_node": f"patient_{req.patient_uid}",
            "to_node": {
                "type": NodeType.ALLERGY,
                "content": req.new_allergen,
                "metadata": {
                    "severity": req.allergen_severity,
                    "discovered_on": str(req.visit_date),
                },
            },
            "type": RelType.HAS_ALLERGY,
            "weight": settings.IMPORTANCE_CRITICAL,
        })

    # ── Assemble final payload ────────────────────────────────────────────────
    payload = {
        "namespace": req.patient_uid,
        "node": visit_node,
        "relationships": relationships,
        "importance": importance,
        "decay": decay,
        "tags": ["clinical_visit", req.patient_uid],
    }

    # ── Call Membrain API ─────────────────────────────────────────────────────
    try:
        async with _get_client() as client:
            resp = await client.post("/memories", json=payload)
            resp.raise_for_status()
            data = resp.json()
            node_id: str = data.get("node_id") or data.get("id")
            logger.info("Membrain stored visit memory. node_id=%s", node_id)
            return node_id
    except httpx.HTTPStatusError as e:
        logger.error("Membrain HTTP error on store: %s — %s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.error("Membrain store_visit_memory failed: %s", str(e))
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  RETRIEVE PATIENT MEMORY  (for /get-summary)
# ═══════════════════════════════════════════════════════════════════════════════

async def retrieve_patient_memory(
    patient_uid: str,
    query: Optional[str] = None,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Semantic search over all memory nodes in the patient's namespace.

    If `query` is provided (e.g. today's complaint), Membrain ranks the
    most contextually relevant memories first.  Otherwise it returns the
    top_k highest-importance nodes.

    Returns a list of memory objects:
    [
        {
            "node_id": "...",
            "content": "...",
            "importance": 0.85,
            "metadata": {...},
            "relevance_score": 0.92
        },
        ...
    ]
    """
    payload = {
        "namespace": patient_uid,
        "query": query or f"complete clinical history of patient {patient_uid}",
        "top_k": top_k,
        "include_graph": True,         # return linked nodes too
        "decay_aware": True,           # Membrain down-weights stale low-importance memories
        "filters": {
            "min_importance": 0.30,    # ignore very low-importance noise
        },
    }

    try:
        async with _get_client() as client:
            resp = await client.post("/memories/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            memories: List[Dict] = data.get("results", [])
            logger.info("Membrain retrieved %d memories for patient %s", len(memories), patient_uid)
            return memories
    except httpx.HTTPStatusError as e:
        logger.error("Membrain HTTP error on retrieve: %s", e.response.status_code)
        return []
    except Exception as e:
        logger.error("Membrain retrieve_patient_memory failed: %s", str(e))
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFLICT SIGNALS  (for /check-conflict)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_conflict_signals(
    patient_uid: str,
    proposed_medicines: List[Medicine],
    proposed_diagnosis: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Ask Membrain to cross-reference proposed prescriptions against the
    patient's stored allergy nodes, past medicine nodes, and condition nodes.

    Returns a list of conflict objects:
    [
        {
            "conflict_type": "allergy",
            "severity": "critical",
            "description": "Patient has a known allergy to Penicillin (life-threatening).",
            "source_memory_id": "node_xyz",
            "recommendation": "Avoid Amoxicillin; consider Azithromycin as an alternative."
        },
        ...
    ]
    """
    medicines_payload = [
        {"name": m.name, "dosage": m.dosage, "duration": m.duration}
        for m in proposed_medicines
    ]

    payload = {
        "namespace": patient_uid,
        "proposed": {
            "medicines": medicines_payload,
            "diagnosis": proposed_diagnosis,
        },
        # Tell Membrain which relationship types to scan for conflicts
        "check_against": [
            NodeType.ALLERGY,
            NodeType.MEDICINE,
            NodeType.DIAGNOSIS,
        ],
        "conflict_rules": [
            "allergy_match",           # exact/semantic allergen vs drug name
            "drug_drug_interaction",   # cross-reference known DDI pairs
            "contraindication",        # drug contraindicated for a known condition
        ],
        "importance_threshold": settings.IMPORTANCE_HIGH,   # only surface high-confidence flags
    }

    try:
        async with _get_client() as client:
            resp = await client.post("/memories/conflict", json=payload)
            resp.raise_for_status()
            data = resp.json()
            conflicts: List[Dict] = data.get("conflicts", [])
            logger.info(
                "Membrain conflict check: %d signal(s) for patient %s",
                len(conflicts), patient_uid
            )
            return conflicts
    except httpx.HTTPStatusError as e:
        logger.error("Membrain HTTP error on conflict: %s", e.response.status_code)
        return []
    except Exception as e:
        logger.error("Membrain get_conflict_signals failed: %s", str(e))
        return []