from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .models import Observation, Patient
from .terminology import compatible_for_rule

RULES = json.loads((Path(__file__).parent / "rules" / "t2dm_demo_rules.json").read_text(encoding="utf-8"))


def assessment(patient: Patient, observations: Iterable[Observation]) -> dict:
    """Return transparent CPG evidence/audit states, never a diagnosis or prescription."""
    source_items = _effective_source_versions(observations)
    items = sorted(source_items, key=lambda item: item.observed_at)
    nodes: list[dict] = []
    blocked = [item for item in items if item.terminology_status != "verified" or item.result_status not in {"final", "amended", "corrected"}]
    if blocked:
        nodes.append({
            "id": "data-validity", "state": "review_required", "reference": RULES["references"]["diagnosis"],
            "detail": "One or more source measurements is unmapped, ambiguous, incompatible, or not a final/amended/corrected result; it is excluded from comparison.",
            "observation_ids": [item.id for item in blocked],
        })
        return _result("data_quality_review", nodes, items, ["Resolve the terminology, unit, status, or source-data issue before comparison."])
    nodes.append({"id": "data-validity", "state": "valid", "reference": RULES["references"]["diagnosis"], "detail": "Verified terminology mappings and compatible canonical UCUM units are present."})

    if patient.pregnancy_related:
        nodes.append({"id": "applicability", "state": "out_of_scope", "reference": RULES["references"]["scope"], "detail": "Pregnancy-related record is not evaluated by this T2DM decision tree."})
        return _result("out_of_scope", nodes, items, ["Use the applicable local pregnancy pathway; no automated recommendation is provided."])
    nodes.append({"id": "applicability", "state": "in_scope", "reference": RULES["references"]["diagnosis"], "detail": "Synthetic adult T2DM evidence-review scope."})

    latest: dict[str, Observation] = {}
    for item in items:
        if item.clinical_key:
            latest[item.clinical_key] = item
    positive = []
    if (item := latest.get("fpg")) and compatible_for_rule(item, "fpg") and (item.normalised_value or 0) >= 7.0:
        positive.append("fasting plasma glucose")
    if (item := latest.get("rpg")) and compatible_for_rule(item, "rpg") and (item.normalised_value or 0) >= 11.1:
        positive.append("random plasma glucose")
    hba1c = latest.get("hba1c_ngsp") or latest.get("hba1c_ifcc")
    if hba1c and compatible_for_rule(hba1c, hba1c.clinical_key) and (hba1c.normalised_value or 0) >= 6.3:
        positive.append("HbA1c")

    if patient.symptomatic and positive:
        diagnostic_state = "evidence_requires_clinician_review"
        detail = "A qualifying synthetic result is present in a record marked symptomatic; clinician confirmation is required."
    elif len(positive) >= 2:
        diagnostic_state = "evidence_requires_clinician_review"
        detail = "Two qualifying synthetic evidence items are present; clinician confirmation is required."
    elif positive:
        diagnostic_state = "confirmation_gap"
        detail = "One qualifying synthetic evidence item is present without the required confirmation context."
    else:
        diagnostic_state = "no_qualifying_evidence"
        detail = "No configured diagnostic-evidence threshold is met in available synthetic observations."
    nodes.append({"id": "diagnostic-evidence", "state": diagnostic_state, "reference": RULES["references"]["diagnosis"], "detail": detail, "positive_evidence": positive})

    required = {"fpg", "egfr_ckd_epi"}
    missing = sorted(required - set(latest))
    if not hba1c:
        missing.insert(0, "hba1c")
    nodes.append({"id": "baseline-audit", "state": "incomplete" if missing else "complete", "reference": RULES["references"]["initial_assessment"], "detail": "Missing illustrative baseline data: " + ", ".join(missing) if missing else "Illustrative baseline data present."})
    nodes.append({"id": "clinician-action", "state": "review_required", "reference": RULES["references"]["monitoring"], "detail": "The clinician may record review, local-pathway follow-up, or referral consideration. No action is submitted by the system."})
    return _result(diagnostic_state, nodes, items, ["Review supplied evidence and missing context.", "Document clinician decision outside this system's automated logic."])


def _result(status: str, nodes: list[dict], observations: list[Observation], considerations: list[str]) -> dict:
    evidence = [{
        "id": item.id, "source_id": item.source_id, "source_version": item.version,
        "code": item.code, "original_code": item.original_code, "coding": {"system": item.coding_system, "code": item.standard_code, "display": item.coding_display, "version": item.terminology_version},
        "value": item.value, "unit": item.unit, "normalised_value": item.normalised_value, "normalised_ucum_unit": item.normalised_ucum_unit,
        "terminology_status": item.terminology_status, "result_status": item.result_status,
        "corrects_observation_id": item.corrects_observation_id, "observed_at": item.observed_at.isoformat(),
    } for item in observations]
    raw = json.dumps({"status": status, "nodes": nodes, "evidence": evidence}, sort_keys=True, separators=(",", ":"))
    return {"status": status, "rule_version": RULES["version"], "nodes": nodes, "evidence": evidence, "considerations": considerations, "evidence_hash": hashlib.sha256(raw.encode()).hexdigest()}


def _effective_source_versions(observations: Iterable[Observation]) -> list[Observation]:
    """A corrected source version supersedes its earlier version for rule input.

    The earlier record remains in storage and is still available through the
    source chain/provenance; it is simply not double-counted in a timeline.
    """
    latest: dict[str, Observation] = {}
    for item in observations:
        existing = latest.get(item.source_id)
        if existing is None or (item.version, item.ingested_at, item.id) > (existing.version, existing.ingested_at, existing.id):
            latest[item.source_id] = item
    return list(latest.values())
