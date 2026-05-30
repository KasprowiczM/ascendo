# Ascendo — Final Push Session: Windows (DP5520WMK)

> Paste this whole file as your first message in a Claude Code session running
> on the **Windows** box (`D:\Dev_Env\Ascendo`), from an **elevated** PowerShell
> when a step needs Administrator. Run the **macOS session first** — it lands the
> shared-core changes you pull here.

You are a senior engineer finishing the Windows side of Ascendo for a
**v1.0-beta production push**. Read `ASCENDO_ULTRA_REVIEW_2.md` §2/§4(Windows)/§7.
The honest-status fix, the deduplicator fail-safe (core), and the 2 macOS test
regressions are already fixed on `main`.

## Ground rules
- Work directly on `main`. **No new worktrees.** Start with `git pull origin main`
  (gets the core deduplicator fail-safe + consent endpoints). Commit + `git push
  origin main` after each task. Don't lose work.
- **TEST-FIRST** where a test is possible; verify before claiming done.
- After each task: `python -m pytest adapters/windows/tests/ -q` (keep green) and
  finally `pwsh bin/validate-windows.ps1 -SkipExpensive`. Confirm the new CI step
  (`adapters/windows/tests` on `windows-latest`) would be green.

## MUST-DO (P1 — pre-push blockers)

### 1. Gate the deduplicator uninstall executor behind explicit opt-in
This is the Windows half of the audit's P0. `adapters/windows/scripts/winget/apply.ps1`
(~line 492-557), `pip/apply.ps1` (~91), and `npm/apply.ps1` (~95) read
`DEDUPLICATION_TASKS.json` and run `winget/npm/pip uninstall`. Core now only
writes that file on explicit opt-in, but harden the executor too so a stray file
can never trigger a silent uninstall:
- Only process `DEDUPLICATION_TASKS.json` when `$env:ASCENDO_DEDUP_AUTO_UNINSTALL
  -eq '1'` **or** the run carries an explicit per-run dedup-approval marker written
  by the dashboard `POST /dedup/apply` (the consent surface the macOS session
  adds). Otherwise skip the uninstall loop and emit an info message
  ("duplicate detected; resolve via the dashboard").
- Keep `-DryRun` honored. Add a Pester/pwsh test (see #4) that proves: file
  present + no opt-in ⇒ **no `winget uninstall` invoked**.

## SHOULD-DO (P2)

### 2. A2/A3 — cache adapter sub-interfaces (match macOS)
`adapters/windows/ascendo_windows/adapter.py:130-146` constructs a NEW
`WindowsInventory`/`WindowsSnapshot`/`WindowsScheduler`/`WindowsElevation` on every
accessor call, so an elevation token registered on one instance is invisible to a
manager that fetched another. Cache singletons exactly like macOS
(`adapters/macos/ascendo_macos/adapter.py:107,159,172,184,192-194`). Test:
`test_adapter_caches_sub_interfaces` (same object returned twice; password
registered on one is visible on the next accessor).

### 3. Windows elevation + secrets hardening
- **P8:** `core/ascendo/ai/persistence.py` does `chmod 0o600` (POSIX no-op on
  Windows). Set a restrictive Windows ACL on `chats.db` via `icacls`/ctypes (owner
  full, remove inherited Users) or warn if world-readable.
- **P11:** `WindowsElevation._run_uac(env=...)` accepts `env` but never applies it
  (the elevated child inherits parent env). Raise `NotImplementedError` if `env`
  is non-None (fail-fast) rather than silently dropping it.
- **P3/P6:** wrap `register_password` callsites in try/finally so the cached
  secret is cleared on error; resolve the elevated `argv[0]` via the full path and
  compare resolved paths (not just basename) before `runas`.

### 4. PowerShell execution tests (T2)
There are currently **zero** PowerShell execution tests — registry mutations,
winget, ARP uninstall, and elevation are unvalidated pre-merge. Add Pester (or
`pwsh`-subprocess) tests for the winget/msstore/arp handlers and the dedup
executor gate (#1). Wire them into `validate-windows.ps1` so CI's
`windows-latest` leg exercises them.

### 5. W10 / W2 parity in Windows web handlers
If the Windows web handlers (`adapters/windows/lib/AscendoWeb.psm1` + web scripts)
have an equivalent discovery / release-feed-regex path, apply the same
fail-loud-on-degradation behavior the macOS session adds (W10 discovery signal,
W2 probe_broken on regex no-match). If not applicable, note it explicitly.

## Finish
- Update `CHANGELOG.md` + a `PLAN.md` note + append a Windows section to
  `HANDOFF.md` Sesja 84.
- Final: `python -m pytest adapters/windows/tests/ -q` green +
  `pwsh bin/validate-windows.ps1 -SkipExpensive` green (run a real apply with
  `bin/run-apply.ps1` only if you want to smoke a true upgrade).
- `git push origin main`. Report which items landed + any Windows-only blockers.
