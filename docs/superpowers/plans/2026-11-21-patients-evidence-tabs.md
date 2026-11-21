# Patients evidence tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize the Patients page into a default-selected patient, a case summary, and three scrollable tabs while keeping the patient list and detail panel equal in height.

**Architecture:** Reuse existing patient detail and renal-profile APIs, plus `TherapyList`, `TreatmentSummary`, `RenalProfile`, and `PatientOutreach`. Keep the patient's observations, conditions, and assessment above the tabs; show patient and cohort therapy in the therapy tab, with only Cohort profile below the primary workspace.

**Tech Stack:** React 18, TypeScript, Vite, existing CSS, FastAPI APIs already present on `feature/renal-profile`.

**Spec:** `docs/superpowers/specs/2026-11-21-patients-evidence-tabs-design.md`

## Global Constraints

- Use synthetic data exclusively.
- Keep clinician patient access bounded to the clinician's organisation.
- Do not change backend APIs, stored data, or clinical interpretation.
- Do not add dependencies.

## Review Focus

- The authorised patient list may not contain `SYN-DM-003`; default to the first authorised record and keep the view usable when the list is empty.
- Rapid patient switching may leave stale detail state; clear old detail before loading the selected record.
- Renal data may fail or be absent; retain an explicit empty state rather than breaking the other tabs.
- Long patient or renal content must scroll inside the matched-height panels.
- At narrow widths the panels stack, and the tab strip remains reachable by horizontal scrolling.

---

### Task 1: Build the tabbed Patients workspace

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/patients`, `GET /api/patients/{patient_id}`, `GET /api/patients/{patient_id}/renal-profile`, `TherapyList`, and `RenalProfile`.
- Produces: default selection of `SYN-DM-003` when authorised, a case summary above accessible `Renal profile`, `Outreach`, and `Glucose-lowering therapy` tabs, matched-height primary panels, and Cohort profile below them.

- [x] Select `SYN-DM-003` by default when available, otherwise the first authorised patient; keep the initial state empty when no patients are available.
- [x] Keep patient observations, conditions, and assessment as a case summary above the tabs.
- [x] Add three keyboard-accessible tabs labelled `Renal profile`, `Outreach`, and `Glucose-lowering therapy`; switch the visible tab panel on activation.
- [x] Render `RenalProfile`, `PatientOutreach`, and patient-level `TherapyList` in their respective tabs, removing only their existing duplicate locations.
- [x] Keep Cohort profile below the primary panels; display the cohort therapy summary in the Glucose-lowering therapy tab.
- [x] Update CSS so the patient list and tab panel have the same bounded height with separate vertical scrolling; preserve responsive stacking and horizontal tab scrolling.
- [x] Run `npm run build` in `frontend`; expect TypeScript and Vite production build to complete successfully.
- [ ] Open the Patients page and visually verify default selection, all three tabs, patient changes, long-content scrolling, equal primary panel heights, and narrow-width behavior.
