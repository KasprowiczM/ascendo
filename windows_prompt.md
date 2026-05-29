# Windows Hardening Prompts

**Context:** We are continuing the production-hardening plan for the "Ascendo" cross-platform updates app based on the `ASCENDO_ULTRA_REVIEW.md` report. The macOS tasks and cross-platform Python tasks have been completed. This session focuses exclusively on the remaining tasks for Windows.

**Goal:** Implement the Windows-specific hardening tasks listed in PASS E of the ultra review report.

**Tasks:**
1. **P8: Set a Windows ACL on `chats.db` (ctypes) or warn if world-readable:**
   - Modify the persistence or Windows-specific code to restrict access to the `chats.db` file to the current user/system using appropriate Windows ACLs (via `ctypes`). If unable to set, log a clear warning that the file might be world-readable.

2. **P11: Fail-fast in Windows `_run_uac` if env is non-None:**
   - In `adapters/windows/managers/elevation.py` (or similar location for `_run_uac`), raise `NotImplementedError` immediately if `env` is passed as a non-None value.

3. **P3/P6: Protect `register_password` callsites:**
   - Wrap `register_password` callsites in `try/finally` blocks to ensure passwords are not leaked or left lingering in memory if an exception occurs.
   - Resolve the elevated `argv[0]` via `shutil.which` and compare resolved paths to prevent spoofing/hijacking of the target executable.

4. **CREATE_NO_WINDOW fixes:**
   - Implement any remaining Windows-specific fixes for `subprocess.CREATE_NO_WINDOW` behavior as needed to ensure child processes spawn without a visible console window.

**Ground Rules (Non-negotiable):**
1. Respect the existing architecture. Do NOT restructure layers, rename the sidecar schema, or change the 5-phase contract. The legacy schema literal `"ubuntu-aktualizacje/v1"` MUST stay distinct from the canonical `"ascendo/v1"`.
2. Preserve behavior while refactoring. Keep existing tests green.
3. TEST-FIRST: before each fix, write/extend a FAILING test that pins the bug, then make it pass.

Please review the codebase, plan the implementation, and execute it.
