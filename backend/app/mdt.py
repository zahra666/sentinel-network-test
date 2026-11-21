from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .guideline_tree import assessment
from .models import Observation, Patient

MDT_MEMBERS = (
    "Evidence and confirmation reviewer",
    "Data-quality and complexity reviewer",
    "Care-coordination reviewer",
)


def run_three_member_mdt(db: Session, patient: Patient) -> dict:
    """A deterministic three-member MDT review, not three autonomous clinical decision makers."""
    observations = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.observed_at)).all())
    guideline = assessment(patient, observations)
    evidence_member = {
        "member": MDT_MEMBERS[0],
        "finding": guideline["status"],
        "evidence": guideline["evidence"],
        "reference": "CPG Section 2.4, pages 25-29",
        "boundary": "Evidence state only; not a diagnosis.",
    }
    missing_nodes = [node for node in guideline["nodes"] if node["id"] == "baseline-audit" and node["state"] == "incomplete"]
    complexity_member = {
        "member": MDT_MEMBERS[1],
        "documented_bmi": patient.body_mass_index,
        "documented_complexity": patient.documented_complexity,
        "missing_context": [node["detail"] for node in missing_nodes],
        "reference": "CPG Section 3.1, pages 31-35",
        "boundary": "Completeness and documented-context review only; no risk prediction.",
    }
    considerations: list[dict] = []
    if guideline["status"] in {"evidence_requires_clinician_review", "confirmation_gap"} and patient.documented_complexity:
        considerations.append({"pathway": "endocrinology_review", "state": "clinician_consideration", "rationale": "Synthetic record has documented complexity plus unresolved CPG evidence-review state.", "reference": "CPG Sections 3.1 and 3.7", "not_a_referral": True})
    if patient.body_mass_index is not None and patient.body_mass_index >= 32:
        considerations.append({"pathway": "bariatric_surgery_consideration", "state": "context_incomplete", "rationale": "BMI is in the CPG Figure 3-5 consideration range, but treatment-response and local eligibility data require clinician confirmation.", "reference": "CPG Figure 3-5, page 80", "not_a_referral": True})
    if guideline["status"] == "no_qualifying_evidence" and not patient.documented_complexity:
        considerations.append({"pathway": "gp_continuity_consideration", "state": "clinician_consideration", "rationale": "No configured evidence-review trigger is present in available synthetic data.", "reference": "CPG Section 3.8", "not_a_referral": True})
    if not considerations:
        considerations.append({"pathway": "clinician_review_required", "state": "insufficient_context", "rationale": "The MDT cannot select a pathway from incomplete or out-of-scope synthetic information.", "reference": "CPG statement of intent", "not_a_referral": True})
    coordinator_member = {
        "member": MDT_MEMBERS[2],
        "considerations": considerations,
        "boundary": "No referral is ordered, sent, or escalated outside the clinician review queue.",
    }
    return {
        "label": "Synthetic data · demonstration only",
        "members": [evidence_member, complexity_member, coordinator_member],
        "draft_clinician_review_plan": ["Verify evidence and missing context.", "Use local policy and professional judgement to decide any care pathway.", "Attest to a selected consideration only after human review."],
        "requires_clinician_attestation": True,
    }
