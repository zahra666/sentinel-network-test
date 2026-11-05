# Five-minute Sentinel Network demonstration

1. Start the API, worker, frontend, and local Hardhat node. Log in as the Aster clinician.
2. Open Overview and run the baseline scan. Confirm persisted Sync, Analyse, and Route events.
3. Open an alert and inspect the CPG Review panel: evidence, source observations, data-quality state, and clinician-review wording. Generate a three-member MDT review, select an already-offered care-pathway consideration, and record a clinician attestation; confirm no referral/order was created.
4. Use Sentinel Agent to queue an integrity verification. Confirm its persisted task is role-scoped. An administrator may queue an allowlisted MOH/MEMS crawl; verify the task returns public reference material only.
5. As administrator, inject the documented synthetic abnormal observation, run the same scan service, and acknowledge the new alert. Run the Sentinel tick twice; confirm the second run creates no duplicate in-app reminder or review-queue escalation.
6. Sign in as the approved researcher. After both batch and synthetic research grant are anchored, review the aggregate district rate, denominator, period boundaries, and suppression notes. Sign in as the denied researcher and confirm access is refused.
7. Open Integrity Ledger. Anchor a source batch, verify its mined commitment, then run the controlled tamper action. Verification must fail. Restore the source record and verify again; audit history remains.
8. Revoke the approved research grant and confirm that the identical aggregate request is denied.
9. Sign in as `caio@sentinel.demo` and open **AI Governance**. Start a five-minute MCP simulation and confirm it appears separately from observed agent activity. Issue a short-lived CAIO `status:read` MCP key as administrator, call `service_status` from an external MCP client, confirm observed activity increments, revoke the key, and confirm the next call is denied. The CAIO browser account must be refused from Patients and MCP key issuance.

All displayed records, users, hospitals, and observations are synthetic fixtures.
