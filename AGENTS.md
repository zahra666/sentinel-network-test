# Sentinel Network repository instructions

This file supplements `MIT-agents.md`. The stricter instruction applies. The project is a **synthetic-data-only** clinical-decision-support demonstration; never introduce autonomous diagnosis, prescribing, urgency classification, referral submission, or external patient contact.

## Interoperability MVP boundary

- Implement HL7 FHIR **R4 (4.0.1)** JSON only for the documented subset of `Patient`, `Observation`, `Condition`, and `DiagnosticReport`. Do not claim full FHIR conformance, profile validation, or a general-purpose FHIR server.
- Retain the existing SQL model. Add explicit mapping/serialization and provenance fields; do not replace the data store or discard the original local source code, value, unit, timestamp, source identifier, or version.
- Patient-level FHIR endpoints require the same server-side clinician organisation boundary as existing clinical routes. Researchers remain aggregate-only. Imports/export must be authenticated, audited, and bounded to authorised synthetic patients.
- Treat corrected or late results as versioned source events. Preserve the prior result, link the correction, and re-run affected deterministic review logic. Never overwrite a prior source result silently.

## Terminology and units

- Only introduce demo terminology entries whose code, display, system URI, and version were verified against current official HL7, LOINC, UCUM, and SNOMED CT materials. Record the verification source/date in the catalogue and README.
- Store coding system URI, code, display, terminology version when available, and original/local code independently. Unmapped or ambiguous terms are retained only with an explicit `needs_terminology_review` state; they must not drive a detection rule. An LLM must never invent a mapping.
- Store original numerical value/unit and separately validated UCUM code plus any normalised value/unit. Implement only documented, tested conversions. Reject or flag incompatible measurements; never compare them based on label text.
- Keep HbA1c NGSP percent and IFCC mmol/mol distinct, and record the conversion provenance if the explicitly supported equation is used. Keep the eGFR equation/method distinct; do not compare eGFR values from unspecified or mismatched equations in a trend rule.
- Support a tiny SNOMED CT condition subset only. Preserve incoming ICD and other source codes without assuming a one-to-one SNOMED mapping. Do not download, bundle, or redistribute the full SNOMED CT terminology.

## Required verification

- Test FHIR R4 import/export round trips, invalid resources, patient-reference resolution, duplicate idempotency, terminology ambiguity, unsupported units/conversions, corrected-result provenance/reprocessing, and access controls on every FHIR route.
- Update the Supabase migration/RLS-denial test when schema changes. Preserve the browser-to-FastAPI-only access model.
- Update README with the subset, catalogue, limitations, source links, and safe sample commands. Keep test results and unverified cloud work clearly separated.
