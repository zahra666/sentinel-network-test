"""Sex and kidney-function distribution for the synthetic T2DM cohort.

Real-world calibration (all figures are Malaysian unless stated):

* Sex -- National Diabetes Registry (NDR) Report 2023, Table 2 (registry
  dataset, n = 1,946,133): female 57.08 %, male 42.92 %.
* Creatinine not tested -- NDR Report 2023, Table 6 (audit dataset): 90.99 % of
  113,903 audited T2DM patients had serum creatinine tested in 2023, so 9.01 %
  have no creatinine and therefore no eGFR.
* eGFR categories below 60 -- NDR 2022 audit, 80,360 T2DM patients
  (Sci Rep 2024;14:7247, doi:10.1038/s41598-024-57723-6): G3a 12.4 %,
  G3b 6.9 %, G4 2.4 %, G5 0.7 % (22.4 % below 60).
* G1 vs G2 split of the remaining 77.6 % -- not reported for Malaysia. We use
  the G1:G2 ratio of a UK primary-care T2DM cohort (CPRD, n = 7,766;
  G1 32.8 % : G2 50.2 %; PMC6767485), giving G1 30.7 % and G2 46.9 %.

Lower eGFR is assigned preferentially to older patients and those with ASCVD
or poorer glycaemic evidence (with random spread), so stage shares are exact
while individuals still vary. Everything is deterministic and synthetic.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

NDR_2023_SEX = {"female": 57.08, "male": 42.92}
CREATININE_TESTED_PCT = 90.99
_ABOVE_60 = 100 - 22.4
_UK_G1, _UK_G2 = 32.8, 50.2
EGFR_STAGES: list[tuple[str, float, float, float]] = [
    # (category, low, high, share % among patients with an eGFR)
    ("G5", 8.0, 14.9, 0.7),
    ("G4", 15.0, 29.9, 2.4),
    ("G3b", 30.0, 44.9, 6.9),
    ("G3a", 45.0, 59.9, 12.4),
    ("G2", 60.0, 89.9, round(_ABOVE_60 * _UK_G2 / (_UK_G1 + _UK_G2), 1)),
    ("G1", 90.0, 112.0, round(_ABOVE_60 * _UK_G1 / (_UK_G1 + _UK_G2), 1)),
]
SOURCES = {
    "sex": "NDR Report 2023, Table 2 (registry dataset, n=1,946,133)",
    "creatinine_tested": "NDR Report 2023, Table 6 (audit dataset, n=113,903)",
    "egfr_below_60": "NDR 2022 audit, Sci Rep 2024;14:7247 (n=80,360)",
    "g1_g2_ratio": "UK CPRD T2DM cohort, PMC6767485 (ratio only; no Malaysian G1/G2 split published)",
}
# Fixtures that other features rely on keep a fixed eGFR.
FIXED_EGFR = {49: 17.0}   # below the SGLT2-inhibitor initiation threshold (index % 100)
KEEP_EGFR = {35, 49, 65}  # stopped-SGLT2 / low-eGFR / unmapped-medication fixtures always have an eGFR


@dataclass
class CohortMember:
    index: int
    age_group: str
    care_setting: str
    pregnancy_related: bool
    has_ascvd: bool
    abnormal_glycaemia: bool


def _counts(total: int, shares: dict[str, float]) -> dict[str, int]:
    base = sum(shares.values())
    exact = {key: total * value / base for key, value in shares.items()}
    counts = {key: int(value) for key, value in exact.items()}
    for key in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: total - sum(counts.values())]:
        counts[key] += 1
    return counts


def assign_sex(members: list[CohortMember]) -> dict[int, str]:
    """Exactly NDR 2023 shares; obstetric (O&G ward / pregnancy-related) records are female."""
    female_target = _counts(len(members), NDR_2023_SEX)["female"]
    forced = [m for m in members if m.pregnancy_related or m.care_setting == "O&G Ward"]
    others = sorted((m for m in members if m not in forced), key=lambda m: random.Random(m.index * 2654435761).random())
    female = {m.index for m in forced} | {m.index for m in others[: max(0, female_target - len(forced))]}
    return {m.index: ("female" if m.index in female else "male") for m in members}


def plan_egfr(members: list[CohortMember]) -> dict[int, float | None]:
    """CKD-EPI eGFR per patient (None = creatinine not tested this year)."""
    total = len(members)
    untested_target = total - round(total * CREATININE_TESTED_PCT / 100)
    candidates = sorted((m for m in members if m.index % 100 not in KEEP_EGFR), key=lambda m: random.Random(m.index * 40503).random())
    untested = {m.index for m in candidates[:untested_target]}
    tested = [m for m in members if m.index not in untested]

    counts = _counts(len(tested), {name: share for name, _, _, share in EGFR_STAGES})
    plan: dict[int, float | None] = {index: None for index in untested}
    for m in tested:
        if m.index % 100 in FIXED_EGFR:
            plan[m.index] = FIXED_EGFR[m.index % 100]
            stage = next(name for name, low, high, _ in EGFR_STAGES if low <= plan[m.index] <= high)
            counts[stage] -= 1

    def risk(m: CohortMember) -> float:
        age = {"65+": 3.2, "45-64": 1.6}.get(m.age_group, 0.0)
        return age + 1.0 * m.has_ascvd + 0.8 * m.abnormal_glycaemia + 2.4 * random.Random(m.index * 7907).random()

    ranked = sorted((m for m in tested if m.index not in plan), key=risk, reverse=True)  # highest risk -> lowest eGFR
    position = 0
    for name, low, high, _ in EGFR_STAGES:
        for m in ranked[position: position + counts[name]]:
            plan[m.index] = round(low + (high - low) * random.Random(m.index * 15485863).random(), 1)
        position += counts[name]
    return plan


def egfr_category(egfr: float | None) -> str | None:
    if egfr is None:
        return None
    for name, low, _high, _ in reversed(EGFR_STAGES):  # G1 first
        if egfr >= low:
            return name
    return "G5"
