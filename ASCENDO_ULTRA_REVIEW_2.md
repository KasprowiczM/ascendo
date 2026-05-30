# Ascendo Ultra Review — Final Production Push Edition (2nd pass)

> **Method.** Forensic, read-only verification of the *current* tree at
> `/Users/mk/Dev_Env/Ascendo` against the first review (`ASCENDO_ULTRA_REVIEW.md`)
> and the post-review implementation tracker (`HANDOFF_PLAN.md` / `HANDOFF_TASK.md`).
> Every claim was checked against actual code with `file:line` evidence; the
> highest-stakes findings were adversarially cross-checked. A multi-agent
> workflow was attempted first but the runtime failed to return structured
> output, so the audit was completed inline with direct file reads + a full
> test-suite run. Six low-risk, high-leverage fixes were applied directly and
> re-validated (see §9–§10).
>
> Date: 2026-05-31. Reviewer model: Claude Opus 4.8 (ultracode).

---

## 1. Executive Summary

The implementation pass landed its **single most important fix correctly**: the
dishonest inventory status map (`failed→outdated`, `triggered→up_to_date`) is now
honest (`core/ascendo/orchestrator/run_async.py:247-261`), and macOS `verify.sh`
escalates a stuck `triggered_pending` to `action_required` after a timeout
(`adapters/macos/scripts/web/verify.sh:100-102`). That genuinely addresses the
core "web apps look green but never updated" complaint at the data layer.
`RunStatus.CANCELLED` + skip-flush-on-cancel (E11), the loopback CORS default
(P5), Ubuntu `verify_signature` wiring (P1 — *better* than the tracker claimed),
`PRAGMA user_version` (I8), and a real 3-OS CI matrix + 4 bats suites are all
real and good.

**It was not production-ready as delivered, for three reasons:**

1. **A latent destructive P0.** The cross-source deduplicator auto-uninstalls
   non-preferred duplicate packages, and its only consent gate —
   `_confirm_uninstall` — returned `True` **unconditionally in any non-TTY
   context** (`deduplicator.py:18-19`). Wired into `runner.py:270` after *every*
   CHECK, it writes `DEDUPLICATION_TASKS.json`, which Windows
   `winget/npm/pip apply.ps1` **execute** (`winget/apply.ps1:540`). A user
   clicking "Safe update" in the dashboard (non-TTY) could have packages
   uninstalled with zero consent. Dormant *only by accident*: the platform with
   the executor (Windows) ships no config; the platforms with config
   (macOS/Ubuntu) have no executor. One routine "parity" commit re-arms it.
   **Fixed** this session (fail-safe default + explicit opt-in).

2. **The suite was red and CI couldn't see it.** On a clean run,
   `adapters/macos/tests` had **2 failures** (`test_msupdate_apply_calls_msupdate_install`,
   `test_shipped_registry_has_perplexity_macv3_entry`) — yet the tracker claimed
   "556 passed / 100% green." Both the implementers' command *and* CI's pytest
   job (`.github/workflows/validate.yml:488`) run only
   `tests/contract|cross-cut|integration` — **`adapters/*/tests` never gated
   merges.** **Fixed** both tests + the CI blind spot this session.

3. **A recurring "shelfware" pattern — `[x]` ≠ wired.** Several tracker-checked
   items are methods + passing tests with **zero production call sites**:
   `reconcile()` (I2/D4) and `scan_meta`/`set_scan_complete` (I9) are dead code
   (orphan eviction actually works via the *pre-existing* `_replace_buckets_in_db`
   clear+replace, so no data bug — but the new code is misleading waste). Two
   items are marked done but **the code is unchanged**: W10 (no
   `DISCOVERY_OK/FAILED` signal exists) and W11 (`sort -V` still in all 6 macOS
   npm/pip scripts).

**Verdict: CONDITIONAL GO for 1.0-beta.** With the six applied fixes, the P0 is
neutralized, the suite is green, and the CI blind spot is closed. Remaining
pre-push work is small and platform-scoped (see the per-platform prompts).
Per the operator's decision, Fedora/Arch/other-Linux support is **explicitly
deferred to the next version** and is *not* a v1 blocker.

---

## 2. Implementation Status Matrix

| ID | Claimed | Actual (`file:line`) | Classification | Quality | Plat | Sev |
|---|---|---|---|---|---|---|
| honest-status | failed→failed, triggered→triggered_pending | `run_async.py:247-261` confirmed | **implemented_well** | excellent | cross | — |
| E11 CANCELLED | status + skip flush | `run_async.py:74,603-606,619-627` | **implemented_well** | excellent | cross | — |
| E8 | warn on bad phase | `run_async.py:376-382` | implemented_well | good | cross | — |
| E5 | catch SidecarWriteError | runner.py | implemented_well | good | cross | — |
| stream-log race | store path on RunState | `run_async.py:582-584` **still sets `os.environ` global** | **implemented_incorrectly** | risky | cross | P2 |
| W4/W13 | triggered_pending→action_required | `verify.sh:100-102` real | **implemented_well** | good | macos | — |
| P5 CORS | loopback default | `app.py:267-281` loopback + `allow_credentials=False`; docstring lied (fixed) | implemented_partial | acceptable | cross | P2 |
| P12 stale lock | detect + doctor | `cli:351-362` (doctor only), not acquisition | implemented_partial | acceptable | cross | P2 |
| P1 verify_signature | (tracker: not done) | **Done+wired** Ubuntu: `apt.py:105`→`source.py:91` | implemented_well | acceptable | linux | P2 |
| I8 user_version | PRAGMA=2 | `inventory_db.py:239` | implemented_well | good | cross | — |
| D3/T7 normalize_id | allowlist refine | `run_async.py:272-324` wired; VCRedist preserved | implemented_well | good | cross | — |
| I9 scan_meta | per-cat watermark | defined; **0 callers**; `is_fresh` uses old `get_meta` | **not_implemented (dead)** | poor | cross | P3 |
| I2/D4/D8/D11 reconcile | reconcile routine | `inventory_db.py:606`; **0 call sites** | **not_implemented (dead)** | poor | cross | P3 |
| W10 discovery signal | DISCOVERY_OK/FAILED | **no such token** in check.sh | **not_implemented** | n/a | macos | P2 |
| W11 sort -V | replaced in 6 scripts | `sort -V` **still present** in all 6 | **not_implemented** | n/a | macos | P3 |
| W2 regex fail-loud | probe_broken on no-match | helper exists; raw-fallback gating unverified | implemented_partial | risky | macos | P2 |
| A5 core→adapter | (deferred) | `web_config.py:38-240`→`ascendo_macos`; `service.py:101`→`ascendo_windows` | not_implemented | risky | cross | P2 |
| A2/A3 caching | (deferred) | Windows `adapter.py:131-146` new instances/call | not_implemented | risky | windows | P2 |
| A6 plugins_loader | (deferred) | `core/ascendo/plugins_loader/__init__.py` unwired | not_implemented (dead) | poor | cross | P3 |
| Deduplicator | integrated | consent bypassed non-TTY; executed on Windows | **implemented_incorrectly** | poor→**fixed** | cross | **P0→mitigated** |
| A4 Fedora/Arch | (deferred) | `adapter_factory:385-389` silent Ubuntu fallback | not_implemented | — | linux | **DEFERRED to v-next** |

Refuted-and-left-alone (still non-bugs, not re-opened): I6, E2, P2, W5, D1/D2. ✔

---

## 3. Architecture Review

- **A5 (P2):** `dashboard/routes/web_config.py` hardwires the generic web-config
  editor to `ascendo_macos.web_registry` (lines 38, 64, 130, 145, 240);
  `service.py:101` to `ascendo_windows`. Net: web-config tab is **macOS-only**,
  service mgmt **Windows-only** — cross-platform inconsistency under a unified UI.
- **A2/A3 (P2):** Windows rebuilds `WindowsElevation()`/inventory/snapshot/scheduler
  per accessor (`adapter.py:131-146`); macOS caches singletons (`107`, `192-194`).
  Per-instance elevation state registered on one call is invisible to a manager
  that fetched another instance — fragile for the in-memory elevation token.
- **A6 (P3):** `plugins_loader/` dead code; decide wire-or-remove.
- **Strengths intact:** adapter factory, thin orchestrator, frozen models,
  phase-priority flush.

---

## 4. Platform Reviews

### Windows — weakest hardened, highest residual risk
- **Works:** winget/msstore/npm/pip; `apply.ps1` reads `DEDUPLICATION_TASKS.json`, DryRun honored.
- **Weak:** **executes** the dedup uninstall tasks (`winget/apply.ps1:540`) — must gate on the opt-in; A2/A3 per-call construction; **no Pester / PS unit tests** (T2); `adapters/windows/tests` not in CI before this session's fix.

### macOS — most mature, but the regressions lived here
- **Works well:** honest status; W4/W13 escalation; the false-positive fixes
  (`c3fb776`) are **honest** (Ms365 matches `CFBundleShortVersionString` only
  when MAU reports no pending; msupdate RC-95 surfaces action-required instead
  of hanging).
- **Landed broken (fixed this session):** 2 test regressions; the perplexity
  one was a genuine regression (unverified `builtin→sparkle` flip contradicting
  its own comment + the test invariant).
- **Weak:** W10 not implemented; W11 not replaced; W2 fail-loud gating unverified.

### Linux/Ubuntu — trails; v1 scope = Ubuntu/Debian only
- **Works:** apt/snap/brew/npm/pip/flatpak; `verify_signature` implemented +
  wired (`apt.py:105`, `source.py:91`); richest dedup config.
- **Deferred to v-next (operator decision):** Fedora/Arch/RHEL silent fallback
  (A4) and dnf/pacman adapters. Document "Ubuntu/Debian only" for v1.
- **Weak:** `adapters/ubuntu/tests` not in CI before this session's fix.

---

## 5. Quality & Honesty Review

- **Data-layer honesty: genuinely improved** (the headline win — verified real).
- **UI follow-through: incomplete (P2).** `app/frontend/components.js:32-35`
  `STATUS()` maps any status not in `{ok,warn,err,info,neutral}` → `"neutral"`;
  the old `.st-*` set has `st-triggered` but no `st-failed`/`st-triggered_pending`.
  The honest backend statuses likely render as neutral grey, not red/amber.
- **Process honesty regressed:** `HANDOFF_TASK.md` `[x]` marks are unreliable
  (dead code + unchanged code checked done; "100% green" excluded the broken
  adapter tests). **Trust the code + CI, not the tracker.**

---

## 6. Performance Review

No regressions (suite ~85s). Open P3 (unaddressed): uncached `/health/check`
re-runs `shutil.which()` ×12/call; no `/runs/stats`/metrics/structured logs.
The dead `scan_meta` machinery was meant to refine freshness but is unwired;
`is_fresh()` keys on the old (correctly full-scan-scoped) `set_meta`, so no
stale-partial bug — just no per-category granularity.

---

## 7. Security & Production Hardening Review

- **P5 CORS (acceptable):** loopback allowlist + `allow_credentials=False`
  (`app.py:267-281`); browser-CSRF closed. **Residual P2:** no bind-host refusal
  on `--host 0.0.0.0` and **no auth/CSRF token** on mutating endpoints
  (`/runs/async`, `/elevation/auth`).
- **Deduplicator P0:** mitigated (fail-safe; Windows executor gate is platform work).
- **P1 verify_signature:** real for Ubuntu, fail-closed; macOS/Windows rely on
  in-handler `spctl`/Authenticode — acceptable; document deferral in ADR-0005.
- **P12 stale lock:** in `doctor`; acquisition errors after ~6.5s — acceptable.
- **Unaddressed (Windows-only, P2/P3):** P8 ChatsDB ACL, P11 UAC `env` ignored,
  P3/P6 password try/finally + symlink-resolved `argv[0]`.

---

## 8. Severity Legend

P0 = do not ship · P1 = fix before push · P2 = ship with documented risk ·
P3 = post-launch.

---

## 9. Code Changes Made (2026-05-31)

All low-risk, behavior-preserving-in-the-safe-direction, verified. See the
per-platform prompts for the remaining work.

| File | Change | Why |
|---|---|---|
| `core/ascendo/orchestrator/deduplicator.py` | `_confirm_uninstall`: non-TTY → **`False`** (was `True`); explicit `ASCENDO_DEDUP_AUTO_UNINSTALL=1` opt-in; interactive default → No | **Neutralizes the P0** silent uninstall; recommend-only in the dashboard |
| `tests/test_deduplicator.py` | both auto-uninstall tests set the opt-in env via `monkeypatch` | preserve coverage of the destructive path under explicit opt-in |
| `adapters/macos/config/web_apps.toml` | perplexity `handler` `sparkle`→**`builtin`**, drop unverified `appcast_url` | fix the regression; restore "zero fake-silent-install" invariant |
| `adapters/macos/tests/test_web_handler_msupdate_docker.py` | assert RC **95** + no `--install` | match the intentional "drop silent msupdate installs" decision |
| `core/ascendo/dashboard/app.py` | CORS docstring "Default `[*]`" → loopback allowlist | the code was fixed; the doc still lied |
| `.github/workflows/validate.yml` | matrix runs `adapters/<os>/tests` on its **native** runner | closes the CI blind spot that hid the 2 regressions |

---

## 10. Validation Results

- **Before fixes:** `2 failed, 973 passed, 2 skipped, 1 xfailed` (EXIT=1).
- **After fixes:** **`975 passed, 2 skipped, 1 xfailed` (0 failed)** — zero
  regressions introduced.
- Affected-tests focused run: 14/14 pass. `validate.yml` parses. Dedup
  fail-safe verified: non-TTY default `False`, opt-in `True`.
- **Simulated/not runnable here:** the Windows + Ubuntu legs of the new CI step
  (each adapter's tests run on its native runner) — only the macOS leg was
  verified locally (green after fixes).
- **Pre-existing lint debt:** trailing/blank-line whitespace (W293) across the
  touched files — not introduced by these edits; out of scope to mass-reformat.

---

## 11. Push Readiness Verdict

**CONDITIONAL GO for 1.0-beta.** With the six applied fixes, the release blockers
(silent destructive uninstall; red suite; CI blind spot) are closed. The
remaining pre-push work is small, platform-scoped, and captured in
`PROMPT_MACOS.md`, `PROMPT_WINDOWS.md`, `PROMPT_UBUNTU.md`. Fedora/Arch/other-Linux
is deferred to the next version by operator decision and is not a v1 blocker.

After the three platform sessions complete their must-do items (deduplicator
consent finish, honest-status pills, Windows executor gate + security, native
adapter-test green), tag **v1.0-beta**.

---

*Cross-references: `ASCENDO_ULTRA_REVIEW.md` (1st pass, finding IDs),
`HANDOFF.md` Sesja 84, the three `PROMPT_*.md` files.*
