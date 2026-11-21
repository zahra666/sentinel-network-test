"""Glucose-lowering treatment modality for the synthetic T2DM cohort.

The cohort mix is calibrated to Malaysia's National Diabetes Registry (NDR)
Report 2023, Table 10 "Utilization of anti-diabetic drugs" (audit dataset,
N = 113,903 T2DM patients in MOH public health clinics, page 37):

    Monotherapy (OAD) 32.07 %   >=2 OAD 35.24 %   OAD + insulin 22.60 %
    Diet only 5.58 %            Insulin (any) 27.12 %
    Metformin 85.57 %           Sulphonylureas 42.40 %

The report has no separate "insulin only" row; it is derived as
insulin (any) - OAD + insulin = 27.12 - 22.60 = 4.52 %, which makes the four
modalities sum to 100 %. Note there is no oral insulin in routine care:
"oral" here means oral glucose-lowering drugs (OGLD/OAD); insulin is injected.

Assignment is deterministic and uses exact largest-remainder counts, so a
500-profile cohort reproduces the NDR shares to within one patient. Patients
with poorer glycaemic evidence, lower eGFR, ASCVD, older age, medical-ward care
or a pregnancy-related record are more likely to be on insulin, in line with
the associations reported from the NDR. This is a synthetic demonstration, not
a prescribing model: nothing here recommends or changes treatment.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from .terminology import ATC_MEDICATIONS

NDR_2023 = {
    "source": "National Diabetes Registry Report 2023, Ministry of Health Malaysia, Table 10 (audit dataset, N=113,903), p.37",
    "url": "https://moh.gov.my/moh/resources/Penerbitan/Laporan/Umum/NDR_Report_2023_Final.pdf",
    "monotherapy_oad": 32.07,
    "two_or_more_oad": 35.24,
    "oad_plus_insulin": 22.60,
    "diet_only": 5.58,
    "insulin_any": 27.12,
    "insulin_only_derived": 4.52,
    "metformin": 85.57,
    "sulphonylurea": 42.40,
}
MODALITY_SHARES = {
    "oral_only": NDR_2023["monotherapy_oad"] + NDR_2023["two_or_more_oad"],
    "insulin_plus_oral": NDR_2023["oad_plus_insulin"],
    "insulin_only": NDR_2023["insulin_only_derived"],
    "diet_only": NDR_2023["diet_only"],
}
MODALITY_LABELS = {
    "oral_only": "Oral (OGLD) only",
    "insulin_plus_oral": "Insulin injection + oral",
    "insulin_only": "Insulin injection only",
    "diet_only": "Diet / lifestyle only",
    "medication_list_needs_review": "Medication list needs review",
}
FIXTURE_INDICES = {35, 65}  # stopped-SGLT2 and unmapped-code fixtures keep their documented shape


@dataclass
class Regimen:
    modality: str
    metformin: bool = False
    gliclazide: bool = False
    insulin_atc: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Profile:
    index: int
    egfr: float | None
    has_ascvd: bool
    on_sglt2: bool
    abnormal_glycaemia: bool
    age_group: str
    care_setting: str
    pregnancy_related: bool


def largest_remainder(total: int, shares: dict[str, float]) -> dict[str, int]:
    base = sum(shares.values())
    exact = {key: total * value / base for key, value in shares.items()}
    counts = {key: int(value) for key, value in exact.items()}
    for key in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: total - sum(counts.values())]:
        counts[key] += 1
    return counts


def insulin_score(p: Profile) -> float:
    """Higher = more likely to be on insulin (synthetic, deterministic)."""
    score = 3.0 * p.abnormal_glycaemia + 1.0 * p.has_ascvd + 1.0 * (p.age_group == "65+") + 1.0 * (p.care_setting == "Medical Ward") + 3.0 * p.pregnancy_related
    if p.egfr is not None:
        score += 4.0 if p.egfr < 30 else 2.0 if p.egfr < 45 else 0.0
    return score + random.Random(p.index * 7919).random()


def plan_treatment(profiles: list[Profile]) -> dict[int, Regimen]:
    total = len(profiles)
    counts = largest_remainder(total, MODALITY_SHARES)
    by_score = sorted(profiles, key=insulin_score, reverse=True)
    free = lambda p: p.index % 100 not in FIXTURE_INDICES  # noqa: E731
    taken: dict[int, str] = {}

    # Insulin-only and diet-only cannot carry an existing SGLT2 inhibitor (that is an oral drug).
    for p in [p for p in by_score if free(p) and not p.on_sglt2][: counts["insulin_only"]]:
        taken[p.index] = "insulin_only"
    for p in [p for p in by_score if p.index not in taken and free(p)][: counts["insulin_plus_oral"]]:
        taken[p.index] = "insulin_plus_oral"
    for p in [p for p in reversed(by_score) if p.index not in taken and free(p) and not p.on_sglt2 and not p.abnormal_glycaemia and not p.pregnancy_related][: counts["diet_only"]]:
        taken[p.index] = "diet_only"

    plan: dict[int, Regimen] = {}
    for p in profiles:
        modality = taken.get(p.index, "oral_only")
        metformin_ok = p.egfr is None or p.egfr >= 30  # metformin is not used below eGFR 30
        regimen = Regimen(modality=modality, metformin=metformin_ok and modality in {"oral_only", "insulin_plus_oral"})
        if modality == "insulin_only":
            regimen.insulin_atc = "A10AD01"  # premixed human insulin
        elif modality == "insulin_plus_oral":
            regimen.insulin_atc = "A10AC01"  # basal (intermediate-acting) human insulin added to OGLD
        plan[p.index] = regimen

    def oral_count(p: Profile, r: Regimen) -> int:
        return int(r.metformin) + int(r.gliclazide) + int(p.on_sglt2 and r.modality in {"oral_only", "insulin_plus_oral"})

    lookup = {p.index: p for p in profiles}
    oral_only = [p for p in by_score if plan[p.index].modality == "oral_only"]
    # Every OGLD user needs at least one oral agent.
    for p in profiles:
        r = plan[p.index]
        if r.modality in {"oral_only", "insulin_plus_oral"} and oral_count(p, r) == 0:
            r.gliclazide = True
    # Metformin intolerance: switch some monotherapy patients to gliclazide to reach the NDR metformin share.
    metformin_target = round(total * NDR_2023["metformin"] / 100)
    surplus = sum(r.metformin for r in plan.values()) - metformin_target
    for p in sorted([p for p in oral_only if free(p) and not p.on_sglt2 and plan[p.index].metformin and not plan[p.index].gliclazide], key=lambda p: random.Random(p.index * 104729).random()):
        if surplus <= 0:
            break
        plan[p.index].metformin, plan[p.index].gliclazide = False, True
        plan[p.index].notes.append("metformin not tolerated (synthetic)")
        surplus -= 1
    # >=2 OAD among oral-only patients: add gliclazide to the highest-score monotherapy patients.
    needed = round(total * NDR_2023["two_or_more_oad"] / 100) - sum(oral_count(p, plan[p.index]) >= 2 for p in oral_only)
    for p in [p for p in oral_only if free(p) and oral_count(p, plan[p.index]) == 1 and plan[p.index].metformin]:
        if needed <= 0:
            break
        plan[p.index].gliclazide = True
        needed -= 1
    # Sulphonylurea share: top up within insulin + oral patients.
    needed = round(total * NDR_2023["sulphonylurea"] / 100) - sum(r.gliclazide for r in plan.values())
    for p in [p for p in by_score if plan[p.index].modality == "insulin_plus_oral" and not plan[p.index].gliclazide]:
        if needed <= 0:
            break
        plan[p.index].gliclazide = True
        needed -= 1
    assert all(oral_count(lookup[i], r) >= 1 for i, r in plan.items() if r.modality in {"oral_only", "insulin_plus_oral"})
    return plan


def modality_of(medications: Iterable) -> dict:
    """Derive the modality from the active medication list (the stored source of truth)."""
    latest: dict[str, object] = {}
    for item in medications:
        existing = latest.get(item.source_id)
        if existing is None or item.version > existing.version:
            latest[item.source_id] = item
    active = [item for item in latest.values() if item.status == "active"]
    if any(item.terminology_status != "verified" for item in active):
        modality = "medication_list_needs_review"
    else:
        routes = {ATC_MEDICATIONS.get(item.atc_code, {}).get("route") for item in active}
        has_insulin, has_oral = "injection" in routes, "oral" in routes
        modality = ("insulin_plus_oral" if has_insulin and has_oral else "insulin_only" if has_insulin
                    else "oral_only" if has_oral else "diet_only")
    oral = [item for item in active if ATC_MEDICATIONS.get(item.atc_code, {}).get("route") == "oral"]
    return {
        "modality": modality, "label": MODALITY_LABELS[modality],
        "insulin": any(ATC_MEDICATIONS.get(item.atc_code, {}).get("class") == "insulin" for item in active),
        "oral_agent_count": len(oral),
        "medications": [{"atc_code": item.atc_code, "display": item.atc_display or item.source_display,
                         "class": ATC_MEDICATIONS.get(item.atc_code, {}).get("class"),
                         "route": ATC_MEDICATIONS.get(item.atc_code, {}).get("route"),
                         "terminology_status": item.terminology_status} for item in active],
    }


def cohort_summary(modalities: list[dict]) -> dict:
    """Cohort shares next to the NDR 2023 reference values."""
    n = len(modalities) or 1
    pct = lambda count: round(100 * count / n, 2)  # noqa: E731
    count = lambda key: sum(m["modality"] == key for m in modalities)  # noqa: E731
    any_insulin = sum(m["insulin"] for m in modalities)
    oral_only = [m for m in modalities if m["modality"] == "oral_only"]
    classes = lambda cls: sum(any(med["class"] == cls for med in m["medications"]) for m in modalities)  # noqa: E731
    rows = [
        {"key": "oral_only", "label": MODALITY_LABELS["oral_only"], "count": count("oral_only"), "cohort_pct": pct(count("oral_only")), "ndr_2023_pct": round(MODALITY_SHARES["oral_only"], 2)},
        {"key": "insulin_plus_oral", "label": MODALITY_LABELS["insulin_plus_oral"], "count": count("insulin_plus_oral"), "cohort_pct": pct(count("insulin_plus_oral")), "ndr_2023_pct": NDR_2023["oad_plus_insulin"]},
        {"key": "insulin_only", "label": MODALITY_LABELS["insulin_only"], "count": count("insulin_only"), "cohort_pct": pct(count("insulin_only")), "ndr_2023_pct": NDR_2023["insulin_only_derived"], "derived": True},
        {"key": "diet_only", "label": MODALITY_LABELS["diet_only"], "count": count("diet_only"), "cohort_pct": pct(count("diet_only")), "ndr_2023_pct": NDR_2023["diet_only"]},
    ]
    review = count("medication_list_needs_review")
    return {
        "patients": len(modalities), "modalities": rows, "needs_review": review,
        "detail": [
            {"label": "Any insulin", "cohort_pct": pct(any_insulin), "ndr_2023_pct": NDR_2023["insulin_any"]},
            {"label": "Oral monotherapy", "cohort_pct": pct(sum(m["oral_agent_count"] == 1 for m in oral_only)), "ndr_2023_pct": NDR_2023["monotherapy_oad"]},
            {"label": "≥2 oral agents", "cohort_pct": pct(sum(m["oral_agent_count"] >= 2 for m in oral_only)), "ndr_2023_pct": NDR_2023["two_or_more_oad"]},
            {"label": "Metformin", "cohort_pct": pct(classes("biguanide")), "ndr_2023_pct": NDR_2023["metformin"]},
            {"label": "Sulphonylurea", "cohort_pct": pct(classes("sulfonylurea")), "ndr_2023_pct": NDR_2023["sulphonylurea"]},
        ],
        "reference": {"source": NDR_2023["source"], "url": NDR_2023["url"],
                      "note": "Insulin-only is derived (insulin any − OAD+insulin). NDR covers MOH public health clinics; hospital cohorts use more insulin."},
        "synthetic_only": True,
    }
