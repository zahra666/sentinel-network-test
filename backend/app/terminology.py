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
VERIFIED_UCUM_CODES = {"%", "mmol/mol", "mmol/L", "mg/dL", "mL/min/{1.73_m2}"}

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
}

SNOMED_CONDITIONS: dict[str, dict[str, str | None]] = {
    "44054006": {
        "display": "Diabetes mellitus type 2 (disorder)",
        # The app does not bundle a SNOMED release. Retain an incoming coding.version
        # when supplied, otherwise record that no source release was provided.
        "version": None,
        "verification_source": "SNOMED CT International browser / Global Patient Set documentation",
    }
}


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
    if term.value_kind in {"hba1c_ngsp", "hba1c_ifcc", "egfr_ckd_epi", "creatinine"}:
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
    }
    return (
        getattr(item, "clinical_key", None) == clinical_key
        and getattr(item, "terminology_status", None) == "verified"
        and getattr(item, "normalised_ucum_unit", None) == expected.get(clinical_key)
        and getattr(item, "normalised_value", None) is not None
        and getattr(item, "result_status", "final") in {"final", "amended", "corrected"}
    )
