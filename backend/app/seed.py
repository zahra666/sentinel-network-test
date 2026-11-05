from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .chain import build_source_batch
from .db import SessionLocal, initialise_database
from .models import AuditEvent, Condition, Observation, Organization, Patient, ResearchGrant, User
from .security import hash_password
from .terminology import LOINC_SYSTEM, LOINC_VERSION, SNOMED_SYSTEM, TerminologyError, map_measurement

PASSWORD = "SentinelDemo!2026"
ANCHOR = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)


def seed() -> None:
    initialise_database()
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
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
            User(email="clinician.aster@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[0].id),
            User(email="clinician.beryl@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[1].id),
            User(email="clinician.cobalt@sentinel.demo", password_hash=hash_password(PASSWORD), role="clinician", organization_id=organisations[2].id),
            User(email="researcher.approved@sentinel.demo", password_hash=hash_password(PASSWORD), role="researcher"),
            User(email="researcher.denied@sentinel.demo", password_hash=hash_password(PASSWORD), role="researcher"),
        ]
        db.add_all(users)
        db.flush()
        db.add(ResearchGrant(user_id=users[4].id, organization_id=None, purpose="Synthetic regional surveillance demonstration", scope="fixed-district-rate", expires_at=ANCHOR + timedelta(days=365)))

        care_matrix = [(0, "Outpatient Clinic", 15), (0, "Medical Ward", 8), (0, "Surgical Ward", 7), (0, "O&G Ward", 4), (1, "Outpatient Clinic", 15), (1, "Medical Ward", 9), (1, "Surgical Ward", 5), (1, "O&G Ward", 4), (2, "Outpatient Clinic", 15), (2, "Medical Ward", 8), (2, "Surgical Ward", 6), (2, "O&G Ward", 4)]
        patients: list[Patient] = []
        ordinal = 0
        for org_index, setting, count in care_matrix:
            for local_index in range(count):
                ordinal += 1
                patient = Patient(reference=f"SYN-DM-{ordinal:03d}", organization_id=organisations[org_index].id, care_setting=setting, age_group=("18-44" if ordinal % 3 == 0 else "45-64" if ordinal % 3 == 1 else "65+"), symptomatic=ordinal % 17 == 0, pregnancy_related=setting == "O&G Ward" and local_index == 0, body_mass_index=33.0 if ordinal in {11, 22, 44} else 24.0 + (ordinal % 7), documented_complexity=ordinal % 11 == 0)
                patients.append(patient)
                db.add(patient)
        db.flush()

        conditions: list[Condition] = []
        observations: list[Observation] = []
        for index, patient in enumerate(patients, start=1):
            abnormal = index % 11 == 0 or index in {1, 17, 45}
            hba1c_early, hba1c_late = (6.0, 7.1) if abnormal else (5.8, 6.0)
            fpg_early, fpg_late = (6.1, 7.6) if abnormal else (5.4, 5.8)
            unit = "mg/dL" if index == 7 else "mmol/L"
            values = [("hba1c", hba1c_early, "%", 50), ("fpg", fpg_early, unit, 48), ("hba1c", hba1c_late, "%", 12), ("fpg", fpg_late, unit, 10)]
            if index != 9:
                values.append(("egfr", 72.0 - (index % 9), "mL/min/{1.73_m2}", 8))
            conditions.append(Condition(
                patient_id=patient.id, source_id=f"COND-{index:03d}", version=1,
                clinical_status="active", verification_status="confirmed",
                source_coding_system=SNOMED_SYSTEM, source_code="44054006",
                source_display="Diabetes mellitus type 2 (disorder)",
                snomed_code="44054006", snomed_display="Diabetes mellitus type 2 (disorder)",
                terminology_status="verified", recorded_at=ANCHOR,
                provenance={"source": "deterministic synthetic seed", "synthetic_only": True},
            ))
            for sequence, (code, value, item_unit, days_ago) in enumerate(values, start=1):
                loinc_code, local_code = {
                    "hba1c": ("4548-4", "HBA1C"),
                    "fpg": ("14749-6", "FPG"),
                    "egfr": ("62238-1", "EGFR_CKD_EPI"),
                }[code]
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
                observations.append(Observation(
                    patient_id=patient.id, code=code, value=value, unit=item_unit,
                    observed_at=ANCHOR - timedelta(days=days_ago),
                    ingested_at=ANCHOR - timedelta(days=max(days_ago - 1, 0)),
                    source_id=f"SRC-{index:03d}-{sequence}", version=1,
                    is_late_arrival=index == 13 and sequence == 1,
                    ucum_unit=item_unit, result_status="final", method=("CKD-EPI creatinine-based formula / 1.73 sq M" if code == "egfr" else None),
                    egfr_equation=("CKD-EPI" if code == "egfr" else None),
                    provenance={"source": "deterministic synthetic seed", "synthetic_only": True, **mapping["conversion_provenance"]},
                    **{key: mapping[key] for key in ("original_code", "coding_system", "standard_code", "coding_display", "terminology_version", "clinical_key", "terminology_status", "normalised_value", "normalised_ucum_unit")},
                ))
        db.add_all(conditions)
        db.add_all(observations)
        db.flush()
        batch = build_source_batch(observations)
        db.add(batch)
        db.flush()
        db.add(AuditEvent(actor_id=users[0].id, event_type="seed.completed", entity_type="source_batch", entity_id=batch.id, detail={"patients": len(patients), "observations": len(observations), "care_mix": {"outpatient": 45, "medical": 25, "surgical": 18, "og": 12}}))
        db.commit()


if __name__ == "__main__":
    seed()
