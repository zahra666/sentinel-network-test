"""Synthetic renal profile (U&E panel) for the Sentinel demo.

Seven analytes, matching the layout of a Malaysian hospital "Renal Profile":
sodium, potassium, chloride, creatinine, urea, uric acid and eGFR.

Everything here is synthetic. Values are generated deterministically per
patient so the cohort and its fixtures are reproducible. Creatinine is
back-calculated from the patient's existing CKD-EPI eGFR so the two are
internally consistent (the latest eGFR is unchanged, so the SGLT2 care-gap and
CPG review logic see exactly the same input as before).

Reference intervals are display aids only. H/L flags mirror what a laboratory
report prints; they are not a diagnosis, triage or urgency classification.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable

from .terminology import LAB_TERMS

# (observation code, LOINC, local code, label, UCUM unit, decimals)
ANALYTES: list[tuple[str, str, str, str, str, int]] = [
    ("sodium", "2951-2", "NA", "Sodium", "mmol/L", 1),
    ("potassium", "2823-3", "K", "Potassium", "mmol/L", 1),
    ("chloride", "2075-0", "CL", "Chloride", "mmol/L", 1),
    ("creatinine", "14682-9", "CREAT_UMOL", "Creatinine", "umol/L", 1),
    ("urea", "22664-7", "UREA", "Urea", "mmol/L", 1),
    ("uric_acid", "14933-6", "URIC_ACID", "Uric acid", "umol/L", 0),
    ("egfr", "62238-1", "EGFR_CKD_EPI", "eGFR", "mL/min/{1.73_m2}", 1),
]
CODE_FOR_LOINC = {loinc: code for code, loinc, *_ in ANALYTES}

# Adult reference intervals. Creatinine and urate are sex-specific: University
# Hospitals Birmingham NHS (creatinine F 49-90 / M 64-104 umol/L, Abbott;
# urate F 140-360 / M 200-430 umol/L, UK Pathology Harmony). Na, K, Cl and urea
# follow the layout of a Malaysian hospital renal profile. Real intervals are
# laboratory- and method-specific.
REFERENCE_INTERVALS: dict[str, dict] = {
    "sodium": {"low": 136, "high": 145},
    "potassium": {"low": 3.5, "high": 5.1},
    "chloride": {"low": 98, "high": 107},
    "creatinine": {"by_sex": {"female": {"low": 49, "high": 90}, "male": {"low": 64, "high": 104}}},
    "urea": {"low": 2.8, "high": 8.1},
    "uric_acid": {"by_sex": {"female": {"low": 140, "high": 360}, "male": {"low": 200, "high": 430}}},
    "egfr": {"low": 60, "high": None, "note": "values below 60 are flagged; a single value is not a CKD diagnosis"},
}
REFERENCE_SOURCE = ("Adult reference intervals; creatinine and uric acid are sex-specific (UHB NHS: Abbott creatinine, "
                    "UK Pathology Harmony urate). Illustrative only; not for clinical use.")


def reference_for(code: str, sex: str | None) -> dict:
    interval = REFERENCE_INTERVALS[code]
    if "by_sex" not in interval:
        return interval
    if sex in interval["by_sex"]:
        return {**interval["by_sex"][sex], "sex": sex}
    # Sex unknown: show the widest span so neither sex is flagged spuriously.
    low = min(v["low"] for v in interval["by_sex"].values())
    high = max(v["high"] for v in interval["by_sex"].values())
    return {"low": low, "high": high, "note": "sex not recorded; combined female/male span"}


# Panels drawn at these offsets (days before the seed anchor). The final panel
# reuses the patient's existing eGFR observation (8 days before anchor).
PANEL_DAYS_AGO = (370, 190, 8)
AGE_FOR_GROUP = {"18-44": 38, "45-64": 55, "65+": 71}


def ckd_epi_2021(creatinine_umol: float, age: float, female: bool) -> float:
    """CKD-EPI 2021 race-free creatinine equation (mL/min/1.73 m2)."""
    scr = creatinine_umol / 88.4
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    value = 142 * min(scr / kappa, 1) ** alpha * max(scr / kappa, 1) ** -1.200 * 0.9938 ** age
    return value * (1.012 if female else 1.0)


def creatinine_for_egfr(egfr: float, age: float, female: bool) -> float:
    """Invert CKD-EPI 2021 by bisection (eGFR falls monotonically with creatinine)."""
    low, high = 10.0, 2000.0
    for _ in range(60):
        mid = (low + high) / 2
        if ckd_epi_2021(mid, age, female) > egfr:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def synthetic_renal_panels(index: int, age_group: str, final_egfr: float | None, sex: str | None = None) -> list[list[tuple[str, float, str, int]]]:
    """Return panels oldest-first as [(code, value, unit, days_ago), ...].

    The final panel omits eGFR because the seed already records it. When eGFR
    is None (creatinine not tested, ~9 % per NDR 2023), creatinine and eGFR are
    omitted at every visit; the other five analytes are still reported.
    """
    rng = random.Random(index * 1009 + 17)
    female = sex == "female"
    age = AGE_FOR_GROUP.get(age_group, 55)
    # Annual eGFR loss: faster with existing CKD, slow otherwise.
    if final_egfr is None:
        yearly_loss = 0.0
    elif final_egfr < 30:
        yearly_loss = rng.uniform(4.0, 7.0)
    elif final_egfr < 60:
        yearly_loss = rng.uniform(2.0, 5.0)
    else:
        yearly_loss = rng.uniform(0.3, 1.8)
    sodium_base = rng.gauss(140.0, 1.6)
    chloride_base = rng.gauss(103.0, 1.8)
    potassium_base = rng.gauss(4.25, 0.22)
    urate_base = rng.gauss(275.0 if female else 335.0, 35.0)
    # A small deterministic set shows a high chloride or urate, like real panels.
    if index % 23 == 0:
        chloride_base += 5.0
    if index % 19 == 0:
        urate_base += 110.0

    panels = []
    for position, days_ago in enumerate(PANEL_DAYS_AGO):
        is_final = position == len(PANEL_DAYS_AGO) - 1
        egfr = None if final_egfr is None else (final_egfr if is_final else round(final_egfr + yearly_loss * (days_ago - PANEL_DAYS_AGO[-1]) / 365 + rng.uniform(-1.5, 1.5), 1))
        kidney_load = 0.0 if egfr is None else max(0.0, 60.0 - egfr)
        potassium = potassium_base + kidney_load * 0.022 + rng.gauss(0, 0.12)
        urea = 4.2 + kidney_load * 0.17 + rng.gauss(0, 0.6) + (0.6 if age >= 65 else 0)
        urate = urate_base + kidney_load * 2.6 + rng.gauss(0, 18)
        panel = [
            ("sodium", round(sodium_base + rng.gauss(0, 1.2), 1), "mmol/L", days_ago),
            ("potassium", round(potassium, 1), "mmol/L", days_ago),
            ("chloride", round(chloride_base + rng.gauss(0, 1.2), 1), "mmol/L", days_ago),
            ("urea", round(max(urea, 2.2), 1), "mmol/L", days_ago),
            ("uric_acid", float(round(urate)), "umol/L", days_ago),
        ]
        if egfr is not None:
            panel.insert(3, ("creatinine", round(creatinine_for_egfr(egfr, age, female), 1), "umol/L", days_ago))
            if not is_final:
                panel.append(("egfr", egfr, "mL/min/{1.73_m2}", days_ago))
        panels.append(panel)
    return panels


def seed_codes(code: str) -> tuple[str, str]:
    """(LOINC, local code) for a renal-profile observation code."""
    for item_code, loinc, local, *_ in ANALYTES:
        if item_code == code:
            return loinc, local
    raise KeyError(code)


def flag(code: str, value: float | None, sex: str | None = None) -> str | None:
    if value is None or code not in REFERENCE_INTERVALS:
        return None
    interval = reference_for(code, sex)
    if interval.get("low") is not None and value < interval["low"]:
        return "L"
    if interval.get("high") is not None and value > interval["high"]:
        return "H"
    return None


def kdigo_g_category(egfr: float | None) -> str | None:
    """KDIGO GFR category label for a single value (descriptive only)."""
    if egfr is None:
        return None
    for limit, label in ((90, "G1"), (60, "G2"), (45, "G3a"), (30, "G3b"), (15, "G4")):
        if egfr >= limit:
            return label
    return "G5"


def renal_profile(observations: Iterable, sex: str | None = None) -> dict:
    """Chart-ready series for the seven analytes, using effective source versions."""
    latest_version: dict[str, object] = {}
    for item in observations:
        if item.standard_code not in CODE_FOR_LOINC:
            continue
        existing = latest_version.get(item.source_id)
        if existing is None or (item.version, item.ingested_at) > (existing.version, existing.ingested_at):
            latest_version[item.source_id] = item
    by_code: dict[str, list] = defaultdict(list)
    for item in latest_version.values():
        by_code[CODE_FOR_LOINC[item.standard_code]].append(item)

    analytes = []
    panel_dates: set[str] = set()
    for code, loinc, _local, label, unit, decimals in ANALYTES:
        items = sorted(by_code.get(code, []), key=lambda obs: obs.observed_at)
        series = []
        for obs in items:
            usable = obs.terminology_status == "verified" and obs.normalised_value is not None
            value = obs.normalised_value if usable else None
            day = obs.observed_at.date().isoformat()
            panel_dates.add(day)
            series.append({"observed_at": obs.observed_at.isoformat(), "date": day, "value": value,
                           "original_value": obs.value, "original_unit": obs.unit, "flag": flag(code, value, sex),
                           "terminology_status": obs.terminology_status, "source_id": obs.source_id, "version": obs.version})
        term = LAB_TERMS.get(loinc)
        latest = next((point for point in reversed(series) if point["value"] is not None), None)
        analytes.append({
            "code": code, "label": label, "unit": unit.replace("{1.73_m2}", "1.73m²").replace("umol", "µmol"), "decimals": decimals,
            "loinc": loinc, "loinc_display": term.display if term else None,
            "reference": reference_for(code, sex), "series": series, "latest": latest,
            "category": kdigo_g_category(latest["value"]) if code == "egfr" and latest else None,
        })
    return {"analytes": analytes, "panel_dates": sorted(panel_dates), "reference_source": REFERENCE_SOURCE,
            "sex": sex,
            "egfr_equation_note": "Synthetic creatinine is back-calculated from the stored eGFR with CKD-EPI 2021, the patient's sex and an age-group midpoint.",
            "synthetic_only": True}
