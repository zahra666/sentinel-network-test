"""Small, verified interoperability catalogue for the synthetic diabetes demo.

This is deliberately not a terminology server or a replacement for local
governance. Entries are constrained to the measurements exercised by the
synthetic fixture. Unknown and ambiguous codes are retained for review but
cannot feed deterministic clinical-review rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
SNOMED_SYSTEM = "http://snomed.info/sct"
VERIFIED_UCUM_CODES = {"%", "mmol/mol", "mmol/L", "umol/L", "mg/dL", "mL/min/{1.73_m2}"}

# Verified 2026-11-05 against the linked official resources in README.
LOINC_VERSION = "2.83"
CATALOGUE_VERIFIED_ON = "2026-11-05"


@dataclass(frozen=True)
class LabTerm:
    loinc_code: str
    display: str
    value_kind: str
    accepted_ucum: tuple[str, ...]
    canonical_ucum: str
    selection_basis: str
    method: str | None = None


LAB_TERMS: dict[str, LabTerm] = {
    "4548-4": LabTerm(
        "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", "hba1c_ngsp", ("%",), "%",
        "Blood, mass-fraction, point-in-time quantitative HbA1c (LOINC component/property/system/scale).",
    ),
    "59261-8": LabTerm(
        "59261-8", "Hemoglobin A1c/Hemoglobin.total standardized per IFCC-RMP for CDT in Blood", "hba1c_ifcc", ("mmol/mol",), "%",
        "Blood, IFCC-standardized substance-fraction HbA1c; distinct from NGSP-percent reporting.",
        "IFCC-RMP",
    ),
    "14749-6": LabTerm(
        "14749-6", "Glucose [Moles/volume] in Serum or Plasma", "glucose_molar", ("mmol/L",), "mmol/L",
        "Serum/plasma, substance-concentration, point-in-time glucose; fasting/random context comes only from a verified local source-code mapping.",
    ),
    "2345-7": LabTerm(
        "2345-7", "Glucose [Mass/volume] in Serum or Plasma", "glucose_mass", ("mg/dL",), "mmol/L",
        "Serum/plasma, mass-concentration, point-in-time glucose; fasting/random context comes only from a verified local source-code mapping.",
    ),
    "2160-0": LabTerm(
        "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", "creatinine", ("mg/dL",), "mg/dL",
        "Serum/plasma, mass-concentration, point-in-time creatinine.",
    ),
    # Renal profile subset, verified 2026-11-21 against loinc.org term pages
    # (long common name, SCnc / Ser-Plas / Qn axes and example UCUM unit).
    "2951-2": LabTerm(
        "2951-2", "Sodium [Moles/volume] in Serum or Plasma", "sodium", ("mmol/L",), "mmol/L",
        "Serum/plasma, substance-concentration, point-in-time sodium.",
    ),
    "2823-3": LabTerm(
        "2823-3", "Potassium [Moles/volume] in Serum or Plasma", "potassium", ("mmol/L",), "mmol/L",
        "Serum/plasma, substance-concentration, point-in-time potassium.",
    ),
    "2075-0": LabTerm(
        "2075-0", "Chloride [Moles/volume] in Serum or Plasma", "chloride", ("mmol/L",), "mmol/L",
        "Serum/plasma, substance-concentration, point-in-time chloride.",
    ),
    "14682-9": LabTerm(
        "14682-9", "Creatinine [Moles/volume] in Serum or Plasma", "creatinine_molar", ("umol/L",), "umol/L",
        "Serum/plasma, substance-concentration creatinine (SI reporting used by Malaysian laboratories); kept distinct from mass-concentration 2160-0.",
    ),
    "22664-7": LabTerm(
        "22664-7", "Urea [Moles/volume] in Serum or Plasma", "urea", ("mmol/L",), "mmol/L",
        "Serum/plasma, substance-concentration urea (not urea nitrogen).",
    ),
    "14933-6": LabTerm(
        "14933-6", "Urate [Moles/volume] in Serum or Plasma", "urate", ("umol/L",), "umol/L",
        "Serum/plasma, substance-concentration urate (reported as uric acid).",
    ),
    "62238-1": LabTerm(
        "62238-1", "Glomerular filtration rate [Volume Rate/Area] in Serum, Plasma or Blood by Creatinine-based formula (CKD-EPI)/1.73 sq M", "egfr_ckd_epi", ("mL/min/{1.73_m2}",), "mL/min/{1.73_m2}",
        "Serum/plasma/blood, volume-rate/area, CKD-EPI creatinine-based formula; equation remains part of the meaning.",
        "CKD-EPI creatinine-based formula / 1.73 sq M",
    ),
}

# Hospital-local codes are explicit controlled mappings, not display-name guesses.
# FPG/RPG are only classified when this context is present with one of the
# compatible glucose LOINC terms.
LOCAL_TEST_CONTEXT: dict[str, str] = {
    "hba1c": "hba1c_ngsp",
    "hba1c_ngsp": "hba1c_ngsp",
    "hba1c_ifcc": "hba1c_ifcc",
    "fpg": "fpg",
    "fasting_glucose": "fpg",
    "rpg": "rpg",
    "random_glucose": "rpg",
    "egfr": "egfr_ckd_epi",
    "egfr_ckd_epi": "egfr_ckd_epi",
    "creatinine": "creatinine",
    "creat_umol": "creatinine_molar",
    "creatinine_umol": "creatinine_molar",
    "na": "sodium",
    "sodium": "sodium",
    "k": "potassium",
    "potassium": "potassium",
    "cl": "chloride",
    "chloride": "chloride",
    "urea": "urea",
    "uric_acid": "urate",
    "urate": "urate",
}

# Single-analyte quantitative renal-profile terms: code semantics alone fix the
# meaning, so a conflicting local context is the only ambiguity to flag.
RENAL_PROFILE_KINDS = frozenset({"sodium", "potassium", "chloride", "creatinine_molar", "urea", "urate"})

SNOMED_CONDITIONS: dict[str, dict[str, str | None]] = {
    "44054006": {
        "display": "Diabetes mellitus type 2 (disorder)",
        # The app does not bundle a SNOMED release. Retain an incoming coding.version
        # when supplied, otherwise record that no source release was provided.
        "version": None,
        "verification_source": "SNOMED CT International browser / Global Patient Set documentation",
    },
    # ASCVD demo subset, verified 2026-11-21 (concept id, FSN and active status)
    # against public SNOMED CT concept listings linked in README.
    "22298006": {
        "display": "Myocardial infarction (disorder)",
        "version": None,
        "verification_source": "SNOMED CT concept 22298006 (active), HL7 CREDS MyocardialInfarction value set",
        "group": "ascvd",
    },
    "230690007": {
        "display": "Cerebrovascular accident (disorder)",
        "version": None,
        "verification_source": "SNOMED CT concept 230690007 (active), US NLM VSAC stroke value set",
        "group": "ascvd",
    },
    "399957001": {
        "display": "Peripheral arterial occlusive disease (disorder)",
        "version": None,
        "verification_source": "SNOMED CT concept 399957001 (active), HL7 CREDS PeripheralArterialOcclusiveDisease value set",
        "group": "ascvd",
    },
}
ASCVD_SNOMED_CODES = frozenset(code for code, entry in SNOMED_CONDITIONS.items() if entry.get("group") == "ascvd")
T2DM_SNOMED_CODE = "44054006"

# WHO ATC subset for the synthetic medication list. Verified 2026-11-21 against
# the WHOCC ATC/DDD Index (A10BA, A10BK; index page last updated 2024-12-27).
ATC_SYSTEM = "http://www.whocc.no/atc"
ATC_INDEX_DATE = "2024-12-27"
ATC_MEDICATIONS: dict[str, dict[str, str]] = {
    "A10BA02": {"display": "metformin", "class": "biguanide"},
    "A10BK01": {"display": "dapagliflozin", "class": "sglt2_inhibitor"},
    "A10BK03": {"display": "empagliflozin", "class": "sglt2_inhibitor"},
}
SGLT2_ATC_GROUP = "A10BK"


def verified_medication(system: str | None, code: str | None) -> dict[str, str] | None:
    if system != ATC_SYSTEM or not code:
        return None
    return ATC_MEDICATIONS.get(code)


class TerminologyError(ValueError):
    pass


def validate_ucum_code(code: str | None) -> str:
    if code not in VERIFIED_UCUM_CODES:
        raise TerminologyError(f"UCUM code {code!r} is outside this verified demo subset")
    return code


def lab_term(system: str | None, code: str | None) -> LabTerm | None:
    if system != LOINC_SYSTEM or not code:
        return None
    return LAB_TERMS.get(code)


def clinical_key(term: LabTerm | None, original_code: str | None) -> tuple[str | None, str]:
    """Return a rule key only where code semantics and local context agree."""
    if not term:
        return None, "needs_terminology_review"
    local_context = LOCAL_TEST_CONTEXT.get((original_code or "").strip().lower())
    if term.value_kind in {"hba1c_ngsp", "hba1c_ifcc", "egfr_ckd_epi", "creatinine"} | RENAL_PROFILE_KINDS:
        if local_context and local_context != term.value_kind:
            return None, "ambiguous_local_mapping"
        return term.value_kind, "verified"
    if term.value_kind in {"glucose_molar", "glucose_mass"}:
        if local_context in {"fpg", "rpg"}:
            return local_context, "verified"
        return None, "needs_local_context_review"
    return None, "needs_terminology_review"


def normalise_value(term: LabTerm, value: float, ucum_unit: str) -> tuple[float, str, dict[str, Any]]:
    """Perform only the explicitly supported, traceable demo conversions."""
    if ucum_unit not in term.accepted_ucum:
        raise TerminologyError(f"UCUM unit {ucum_unit!r} is incompatible with LOINC {term.loinc_code}")
    if term.value_kind == "hba1c_ifcc":
        # NGSP = (0.09148 * IFCC mmol/mol) + 2.152; source documented in README.
        return round((0.09148 * value) + 2.152, 4), "%", {"conversion": "ifcc_mmol_per_mol_to_ngsp_percent", "formula": "NGSP=(0.09148*IFCC)+2.152"}
    if term.value_kind == "glucose_mass":
        # 180.156 g/mol (LOINC group reference) yields this explicit demo factor.
        return round(value / 18.0156, 4), "mmol/L", {"conversion": "glucose_mg_per_dL_to_mmol_per_L", "factor": 18.0156}
    return value, term.canonical_ucum, {"conversion": "identity"}


def map_measurement(
    *, system: str | None, code: str | None, display: str | None, version: str | None,
    original_code: str | None, value: float | None, ucum_unit: str | None,
) -> dict[str, Any]:
    """Return auditable mapping state without fabricating a terminology match."""
    term = lab_term(system, code)
    base = {
        "coding_system": system,
        "standard_code": code,
        "coding_display": display,
        "terminology_version": version or (LOINC_VERSION if term else None),
        "original_code": original_code,
        "clinical_key": None,
        "terminology_status": "needs_terminology_review",
        "normalised_value": None,
        "normalised_ucum_unit": None,
        "conversion_provenance": {},
    }
    if not term:
        return base
    if value is None or not isinstance(value, (int, float)):
        raise TerminologyError("Supported quantitative laboratory observations require a numeric valueQuantity.value")
    if not ucum_unit:
        raise TerminologyError("Supported quantitative laboratory observations require valueQuantity.code")
    normalised_value, normalised_unit, provenance = normalise_value(term, float(value), ucum_unit)
    key, status = clinical_key(term, original_code)
    base.update({
        "coding_display": term.display,
        "terminology_version": version or LOINC_VERSION,
        "clinical_key": key,
        "terminology_status": status,
        "normalised_value": normalised_value,
        "normalised_ucum_unit": normalised_unit,
        "conversion_provenance": provenance,
    })
    return base


def verified_condition(system: str | None, code: str | None) -> dict[str, str | None] | None:
    if system != SNOMED_SYSTEM or not code:
        return None
    return SNOMED_CONDITIONS.get(code)


def compatible_for_rule(item: Any, clinical_key: str) -> bool:
    """Detection must use verified code mappings plus a compatible canonical unit."""
    expected = {
        "hba1c_ngsp": "%",
        "hba1c_ifcc": "%",
        "fpg": "mmol/L",
        "rpg": "mmol/L",
        "egfr_ckd_epi": "mL/min/{1.73_m2}",
        "sodium": "mmol/L",
        "potassium": "mmol/L",
        "chloride": "mmol/L",
        "creatinine_molar": "umol/L",
        "urea": "mmol/L",
        "urate": "umol/L",
    }
    return (
        getattr(item, "clinical_key", None) == clinical_key
        and getattr(item, "terminology_status", None) == "verified"
        and getattr(item, "normalised_ucum_unit", None) == expected.get(clinical_key)
        and getattr(item, "normalised_value", None) is not None
        and getattr(item, "result_status", "final") in {"final", "amended", "corrected"}
    )
