# Ubuntu Hardening Prompts

**Context:** We are continuing the production-hardening plan for the "Ascendo" cross-platform updates app based on the `ASCENDO_ULTRA_REVIEW.md` report. The macOS tasks and cross-platform Python tasks have been completed. This session focuses exclusively on the remaining tasks for Ubuntu.

**Goal:** Implement the Ubuntu-specific hardening tasks listed in PASS E of the ultra review report.

**Tasks:**
1. **P1: Implement ISource for Ubuntu (apt GPG key verification):**
   - Implement `verify_signature` in the Ubuntu adapter.
   - Wire `verify_signature` into the apply-phase item processing for APT packages.
   - For other components where `verify_signature` does not apply, return `None` but document the deferral in `ADR-0005`.

**Ground Rules (Non-negotiable):**
1. Respect the existing architecture. Do NOT restructure layers, rename the sidecar schema, or change the 5-phase contract. The legacy schema literal `"ubuntu-aktualizacje/v1"` MUST stay distinct from the canonical `"ascendo/v1"`.
2. Preserve behavior while refactoring. Keep existing tests green.
3. TEST-FIRST: before each fix, write/extend a FAILING test that pins the bug, then make it pass.

Please review the codebase, plan the implementation, and execute it.
