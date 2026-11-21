# Patients evidence tabs design

## Intent and scope

Make the Patients page easier to scan by replacing the long right-hand patient-detail stack with three tabs: `Renal profile`, `Outreach`, and `Glucose-lowering therapy`. Default the selected patient to `SYN-DM-003` when available. Keep the patient list on the left and retain its observation, condition, and assessment content as a case summary above the tabs. The main left and right panels should have matching heights and scroll vertically within their content areas.

## Goals and boundaries

- Reuse existing organisation-scoped patient, renal-profile, and treatment data from the current branch APIs.
- Put patient observations, conditions, and review context in a case summary above the tabs; show renal history, patient-specific outreach, and that patient's medication list in their respective tabs.
- Keep both main panels aligned to the same height, with independent vertical scrolling for the roster and active tab content.
- Move patient-specific Outreach into its tab; preserve the Cohort profile and cohort therapy summary cards below the main pair of panels.
- Do not change backend APIs, stored data, or clinical interpretation.
- Preserve the synthetic-data-only demo boundary.

## Approaches

1. **Three tabs in the right panel (selected).** Matches the requested labels and shows one focused patient view at a time; uses the APIs and display components already present in this branch.
2. **Three collapsible sections.** Keeps content in one view but remains vertically long and makes switching among the requested views less direct.

The user selected tabs, confirmed vertical scrolling, approved the three-tab design, and declined adding a Cohort profile tab.

## Implementation direction

Use accessible tab buttons and render one corresponding view at a time. Select `SYN-DM-003` by default when present, falling back to the first authorised patient. Selecting a different patient keeps the selected tab unless the current content needs to reset safely. Keep the case summary and tab controls inside the matched-height detail panel, with the summary and active tab content vertically scrollable while keeping the tab controls reachable. Preserve the existing cohort profile and cohort therapy summary cards below the equal-height pair. On narrow screens, retain the stacked layout and allow horizontal tab scrolling if needed.

## Files and verification

Limit code changes to `frontend/src/main.tsx` and `frontend/src/styles.css`. Run `npm run build` and visually inspect the Patients page at desktop and narrow widths, including the three tabs, inner scrolling, and equal-height primary panels.
