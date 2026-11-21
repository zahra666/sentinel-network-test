# Patients evidence tabs design

## Intent and scope

Make the Patients page easier to scan by placing the case summary and evidence views in four right-hand tabs: the selected patient reference, `Renal Profile`, `Glucose-Lowering Therapy`, and `Outreach`, in that order. Default the selected patient to `SYN-DM-007` when available. Make the left patient list taller than the right evidence panel and let content scroll within each panel.

## Goals and boundaries

- Reuse existing organisation-scoped patient, renal-profile, and treatment data from the current branch APIs.
- Put patient observations, conditions, and review context in the selected patient's tab; show renal history, patient-specific outreach, and both the selected patient's therapy and cohort therapy summary in their respective tabs.
- Make the left patient roster taller than the right tabbed evidence panel, with independent vertical scrolling.
- Move patient-specific Outreach into its tab and move the cohort therapy summary into the `Glucose-lowering therapy` tab; keep Cohort profile below the main pair of panels.
- Do not change backend APIs, stored data, or clinical interpretation.
- Preserve the synthetic-data-only demo boundary.

## Approaches

1. **Three tabs in the right panel (selected).** Matches the requested labels and shows one focused patient view at a time; uses the APIs and display components already present in this branch.
2. **Three collapsible sections.** Keeps content in one view but remains vertically long and makes switching among the requested views less direct.

The user selected tabs, confirmed vertical scrolling, approved the three-tab design, and declined adding a Cohort profile tab.

## Implementation direction

Use accessible tab buttons in this order: selected patient reference, `Renal Profile`, `Glucose-Lowering Therapy`, and `Outreach`. Render one corresponding view at a time. Select `SYN-DM-007` by default when present, falling back to the first authorised patient. Selecting another patient updates the first tab label and evidence, while leaving the evidence tab order unchanged. Keep the left roster taller than the right detail panel, with scrollable content and reachable tab controls. Preserve the Cohort profile card below the panels. On narrow screens, retain the stacked layout and allow horizontal tab scrolling if needed.

## Files and verification

Limit code changes to `frontend/src/main.tsx` and `frontend/src/styles.css`. Run `npm run build` and visually inspect the Patients page at desktop and narrow widths, including the three tabs, inner scrolling, and equal-height primary panels.
