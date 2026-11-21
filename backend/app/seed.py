from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .chain import build_source_batch
from .config import get_settings
from .db import SessionLocal, initialise_database
from .models import AuditEvent, Condition, MedicationStatement, Observation, Organization, Patient, ResearchGrant, SourceBatch, User
from .security import hash_password, verify_password
from .renal_profile import seed_codes, synthetic_renal_panels, synthetic_sex_is_female
from .terminology import ATC_MEDICATIONS, ATC_SYSTEM, LOINC_SYSTEM, LOINC_VERSION, SNOMED_CONDITIONS, SNOMED_SYSTEM, TerminologyError, map_measurement

PASSWORD = "12345"
DEMO_EMAILS = (
    "admin@sentinel.demo",
    "caio@sentinel.demo",
    "clinician.aster@sentinel.demo",
    "clinician.beryl@sentinel.demo",
    "clinician.cobalt@sentinel.demo",
    "researcher.approved@sentinel.demo",
    "researcher.denied@sentinel.demo",
)
ANCHOR = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
# Reference mix for the documented 100-profile cohort: (hospital index, care setting, profiles).
BASE_CARE_MATRIX = [(0, "Outpatient Clinic", 15), (0, "Medical Ward", 8), (0, "Surgical Ward", 7), (0, "O&G Ward", 4), (1, "Outpatient Clinic", 15), (1, "Medical Ward", 9), (1, "Surgical Ward", 5), (1, "O&G Ward", 4), (2, "Outpatient Clinic", 15), (2, "Medical Ward", 8), (2, "Surgical Ward", 6), (2, "O&G Ward", 4)]


def care_matrix_for(total: int) -> list[tuple[int, str, int]]:
    """Scale the reference mix to `total` profiles (largest-remainder rounding).

    With the default of 100 this returns BASE_CARE_MATRIX unchanged, so the
    documented cohort and its fixtures stay identical.
    """
    base = sum(count for *_, count in BASE_CARE_MATRIX)
    exact = [count * total / base for *_, count in BASE_CARE_MATRIX]
    counts = [int(value) for value in exact]
    for index in sorted(range(len(exact)), key=lambda i: exact[i] - counts[i], reverse=True)[: total - sum(counts)]:
        counts[index] += 1
    return [(org, setting, count) for (org, setting, _), count in zip(BASE_CARE_MATRIX, counts)]


def synthetic_egfr(index: int) -> float | None:
    """Deterministic CKD-EPI eGFR spread: mostly preserved, ~1 in 6 below 60, one below 20 per 100."""
    if index % 100 == 9:
        return None  # deliberate missing-eGFR fixture
    if index % 100 == 49:
        return 17.0  # below the guideline SGLT2-inhibitor initiation threshold
    if index % 6 == 0:
        return float(32 + (index * 7) % 27)  # 32-58: CKD G3a/G3b range
    return 72.0 - (index % 9)


def synthetic_ascvd_code(index: int) -> str | None:
    """About 1 in 5 profiles carry an ASCVD condition alongside T2DM (multimorbidity)."""
    if index % 5 != 0:
        return None
    return ("22298006", "230690007", "399957001")[(index // 5) % 3]


def synthetic_medications(index: int, patient_id: str, egfr: float | None, has_ascvd: bool) -> list[MedicationStatement]:
    """Deterministic current-medication list. Includes deliberate review fixtures:
    a stopped SGLT2 inhibitor (index %100 == 35) and an unmapped local code (index %100 == 65)."""
    items: list[MedicationStatement] = []
    started = ANCHOR - timedelta(days=200)

    def add(suffix: str, atc: str | None, status: str = "active", local: str | None = None) -> None:
        entry = ATC_MEDICATIONS.get(atc) if atc else None
        items.append(MedicationStatement(
            patient_id=patient_id, source_id=f"MED-{index:03d}-{suffix}", version=1, status=status,
            source_coding_system=ATC_SYSTEM if atc else "urn:sentinel:local-formulary",
            source_code=atc or local, source_display=entry["display"] if entry else "Local formulary item (unmapped)",
            atc_code=atc if entry else None, atc_display=entry["display"] if entry else None,
            terminology_status="verified" if entry else "needs_terminology_review",
            effective_from=started, recorded_at=ANCHOR,
            provenance={"source": "deterministic synthetic seed", "synthetic_only": True},
        ))

    if egfr is None or egfr >= 30:
        add("METF", "A10BA02")
    if index % 100 == 35:
        add("SGLT2", "A10BK01", status="stopped")
        return items
    if index % 100 == 65:
        add("LOCAL", None, local="LOCAL-FORMULARY-0417")
        return items
    eligible = has_ascvd or (egfr is not None and 20 <= egfr < 60)
    if (eligible and (index * 7) % 10 < 4) or (not eligible and index % 13 == 0):
        add("SGLT2", "A10BK01" if index % 2 else "A10BK03")
    return items


def seed() -> None:
    initialise_database()
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            demo_users = list(db.scalars(select(User).where(User.email.in_(DEMO_EMAILS))).all())
            updated_accounts = 0
            changes_pending = False
            for user in demo_users:
                if not verify_password(PASSWORD, user.password_hash):
                    user.password_hash = hash_password(PASSWORD)
                    updated_accounts += 1
                    changes_pending = True
            if not db.scalar(select(SourceBatch.id).limit(1)):
                observations = list(db.scalars(select(Observation)).all())
                if observations:
                    batch = build_source_batch(observations)
                    db.add(batch)
                    db.flush()
                    admin = db.scalar(select(User).where(User.role == "administrator"))
                    db.add(AuditEvent(actor_id=admin.id if admin else None, event_type="seed.source_batch_completed", entity_type="source_batch", entity_id=batch.id, detail={"observations": len(observations), "synthetic_only": True}))
                    changes_pending = True
            if updated_accounts:
                admin = db.scalar(select(User).where(User.role == "administrator"))
                db.add(AuditEvent(
                    actor_id=admin.id if admin else None,
                    event_type="seed.demo_credentials_aligned",
                    entity_type="synthetic_dataset",
                    entity_id="sentinel-100",
                    detail={"accounts_updated": updated_accounts, "synthetic_only": True},
                ))
            if changes_pending:
                db.commit()
            return
        organisations = [
            Organization(code="ASTER", name="Aster General Hospital", district="Aster District"),
            Organization(code="BERYL", name="Beryl Medical Centre", district="Beryl District"),
            Organization(code="COBALT", name="Cobalt Community Hospital", district="Cobalt District"),
        ]
        db.add_all(organisations)
        db.flush()
        users = [
            User(email="admin@sentinel.demo", password_hash=hash_password(PASSWORD), role="administrator"),
            User(email="caio@sentinel.demo", password_hash=hash_password(PASSWORD), role="caio"),
            User(email="clinician.aster@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[0].id),
            User(email="clinician.beryl@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[1].id),
            User(email="clinician.cobalt@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[2].id),
            User(email="researcher.approved@sentinel.demo", password_hash=hash_password(PASSWORD), role="researcher"),
            User(email="researcher.denied@sentinel.demo", password_hash=hash_password(PASSWORD), role="researcher"),
        ]
        db.add_all(users)
        db.flush()
        db.add(ResearchGrant(user_id=users[5].id, organization_id=None, purpose="Synthetic regional surveillance demonstration", scope="fixed-district-rate", expires_at=ANCHOR + timedelta(days=365)))

        total_profiles = get_settings().seed_patient_count
        care_matrix = care_matrix_for(total_profiles)
        patients: list[Patient] = []
        ordinal = 0
        for org_index, setting, count in care_matrix:
            for local_index in range(count):
                ordinal += 1
                patient = Patient(reference=f"SYN-DM-{ordinal:03d}", organization_id=organisations[org_index].id, care_setting=setting, age_group=("18-44" if ordinal % 3 == 0 else "45-64" if ordinal % 3 == 1 else "65+"), symptomatic=ordinal % 17 == 0, pregnancy_related=setting == "O&G Ward" and local_index % 4 == 0, body_mass_index=33.0 if ordinal % 100 in {11, 22, 44} else 24.0 + (ordinal % 7), documented_complexity=ordinal % 11 == 0)
                patients.append(patient)
                db.add(patient)
        db.flush()

        conditions: list[Condition] = []
        medications: list[MedicationStatement] = []
        observations: list[Observation] = []
        for index, patient in enumerate(patients, start=1):
            abnormal = index % 11 == 0 or index % 100 in {1, 17, 45}
            hba1c_early, hba1c_late = (6.0, 7.1) if abnormal else (5.8, 6.0)
            fpg_early, fpg_late = (6.1, 7.6) if abnormal else (5.4, 5.8)
            unit = "mg/dL" if index % 100 == 7 else "mmol/L"
            values = [("hba1c", hba1c_early, "%", 50), ("fpg", fpg_early, unit, 48), ("hba1c", hba1c_late, "%", 12), ("fpg", fpg_late, unit, 10)]
            egfr = synthetic_egfr(index)
            if egfr is not None:
                values.append(("egfr", egfr, "mL/min/{1.73_m2}", 8))
            # Synthetic renal profile (Na, K, Cl, creatinine, urea, uric acid, eGFR)
            # at three visits; the latest eGFR above is reused, not duplicated.
            for panel in synthetic_renal_panels(index, patient.age_group, egfr):
                values.extend(panel)
            conditions.append(Condition(
                patient_id=patient.id, source_id=f"COND-{index:03d}", version=1,
                clinical_status="active", verification_status="confirmed",
                source_coding_system=SNOMED_SYSTEM, source_code="44054006",
                source_display="Diabetes mellitus type 2 (disorder)",
                snomed_code="44054006", snomed_display="Diabetes mellitus type 2 (disorder)",
                terminology_status="verified", recorded_at=ANCHOR,
                provenance={"source": "deterministic synthetic seed", "synthetic_only": True},
            ))
            if (ascvd_code := synthetic_ascvd_code(index)):
                conditions.append(Condition(
                    patient_id=patient.id, source_id=f"COND-{index:03d}-ASCVD", version=1,
                    clinical_status="active", verification_status="confirmed",
                    source_coding_system=SNOMED_SYSTEM, source_code=ascvd_code,
                    source_display=SNOMED_CONDITIONS[ascvd_code]["display"],
                    snomed_code=ascvd_code, snomed_display=SNOMED_CONDITIONS[ascvd_code]["display"],
                    terminology_status="verified", recorded_at=ANCHOR - timedelta(days=400),
                    provenance={"source": "deterministic synthetic seed", "synthetic_only": True, "multimorbidity": "ascvd"},
                ))
            medications.extend(synthetic_medications(index, patient.id, egfr, ascvd_code is not None))
            for sequence, (code, value, item_unit, days_ago) in enumerate(values, start=1):
                loinc_code, local_code = {
                    "hba1c": ("4548-4", "HBA1C"),
                    "fpg": ("14749-6", "FPG"),
                    "egfr": ("62238-1", "EGFR_CKD_EPI"),
                }.get(code) or seed_codes(code)
                try:
                    mapping = map_measurement(
                        system=LOINC_SYSTEM, code=loinc_code, display=None, version=LOINC_VERSION,
                        original_code=local_code, value=value, ucum_unit=item_unit,
                    )
                except TerminologyError:
                    # Deliberate synthetic inconsistent-unit fixture. It remains
                    # preserved and is explicitly excluded from rule comparisons.
                    mapping = {
                        "coding_system": LOINC_SYSTEM, "standard_code": loinc_code,
                        "coding_display": None, "terminology_version": LOINC_VERSION,
                        "original_code": local_code, "clinical_key": None,
                        "terminology_status": "unit_incompatible", "normalised_value": None,
                        "normalised_ucum_unit": None,
                        "conversion_provenance": {"reason": "deliberate synthetic incompatible-unit fixture"},
                    }
                renal_extra = ({"renal_profile": True, "creatinine_back_calculated": "CKD-EPI 2021",
                                "assumed_sex": "female" if synthetic_sex_is_female(index) else "male"} if code == "creatinine" else
                               {"renal_profile": True} if loinc_code not in {"4548-4", "14749-6"} else {})
                observations.append(Observation(
                    patient_id=patient.id, code=(mapping["clinical_key"] if code not in {"hba1c", "fpg", "egfr"} and mapping["clinical_key"] else code), value=value, unit=item_unit,
                    observed_at=ANCHOR - timedelta(days=days_ago),
                    ingested_at=ANCHOR - timedelta(days=max(days_ago - 1, 0)),
                    source_id=f"SRC-{index:03d}-{sequence}", version=1,
                    is_late_arrival=index % 100 == 13 and sequence == 1,
                    ucum_unit=item_unit, result_status="final", method=("CKD-EPI creatinine-based formula / 1.73 sq M" if code == "egfr" else None),
                    egfr_equation=("CKD-EPI" if code == "egfr" else None),
                    provenance={"source": "deterministic synthetic seed", "synthetic_only": True, **renal_extra, **mapping["conversion_provenance"]},
                    **{key: mapping[key] for key in ("original_code", "coding_system", "standard_code", "coding_display", "terminology_version", "clinical_key", "terminology_status", "normalised_value", "normalised_ucum_unit")},
                ))
        db.add_all(conditions)
        db.add_all(medications)
        db.add_all(observations)
        db.flush()
        batch = build_source_batch(observations)
        db.add(batch)
        db.flush()
        db.add(AuditEvent(actor_id=users[0].id, event_type="seed.completed", entity_type="source_batch", entity_id=batch.id, detail={"patients": len(patients), "observations": len(observations), "conditions": len(conditions), "medication_statements": len(medications), "care_mix": {setting: sum(c for _, name, c in care_matrix if name == setting) for setting in ("Outpatient Clinic", "Medical Ward", "Surgical Ward", "O&G Ward")}}))
        db.commit()


if __name__ == "__main__":
    seed()
