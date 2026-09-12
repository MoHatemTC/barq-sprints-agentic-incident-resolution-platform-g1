# S1.1 Screenshot Evidence

Place final PNG screenshots in this directory. Do not include credentials, tokens, client secrets, or sensitive personal data.

Evidence files:

1. `01-studio-application-scope.png` — application name and generated scope. **Captured.**
   `01b-current-scoped-update-set.png` — working scoped update set selected. **Captured.**
2. `02-incident-ai-section.png` — complete readable Incident form section populated with distinct suggestion/resolution, attribution, timing, and human-control fields. **Captured.**
3. `03-processing-state-choices.png` — all five required choices. **Captured.**
4. `05-confidence-validation.png` — rejected out-of-range value and clear error. **Captured.**
5. `07-secondary-import-preview.png` — clean secondary-instance preview showing the Customer Updates list, 39 inserts, and zero updates, deletions, or collisions. **Captured.**
6. `08-secondary-import-committed.png` — committed state and clean record counts. **Captured.**
7. `09-secondary-import-verification.png` — imported form/fields and confidence validation working on the secondary instance. **Captured.**

Use a dedicated synthetic test Incident rather than real personal/customer data.

Exact duplicate files from the original PR were removed during final evidence cleanup. One genuine capture may support more than one acceptance requirement when all relevant state is visibly present; the evidence package does not inflate its screenshot count with duplicate image files.
