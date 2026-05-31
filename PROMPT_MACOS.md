# Ascendo — Final Push Session: macOS (core + macOS adapter)

> Paste this whole file as your first message in a Claude Code session running
> on the **Mac** (`/Users/mk/Dev_Env/Ascendo`). This session owns the
> **shared-core** items plus macOS-specific work. Run it FIRST — the Windows
> and Ubuntu sessions pull your core changes.

> **Status (Sesja 85): CI is GREEN.** The `Validate Config` workflow passes
> 6/6 on `main`. `git pull origin main` first. CI runs the harness in
> **reduced** mode (`--quick`), so as a FINAL real-hardware check this session
> run the **full** `bash bin/validate-macos.sh` (no `--quick`) and confirm a
> real `brew`/`mas` apply + the dashboard work — CI does not cover those.
> Note: PROMPT §4 (A5 core→adapter coupling) is still open — CI sidesteps it by
> installing the macOS adapter in the python-tests job; the proper decoupling
> is the real fix.

You are a senior engineer finishing Ascendo for a **v1.0-beta production push**.
Read `ASCENDO_ULTRA_REVIEW_2.md` (the 2nd-pass audit) §9–§11 for context. The P0
silent-uninstall fail-safe and the 2 macOS test regressions are already fixed and
committed. Your job is the remaining macOS + cross-platform-core items below.

## Ground rules
- Work directly on `main` in `/Users/mk/Dev_Env/Ascendo`. **No new worktrees.**
  Start with `git pull origin main`. Commit + `git push origin main` after each
  task (one finding per commit, finding ID in the message). Don't lose work.
- **TEST-FIRST.** For each fix write/extend a failing test, then make it pass.
- **Verify before claiming done.** After each task run:
  `python3 -m pytest tests/contract/ tests/cross-cut/ tests/integration/ adapters/macos/tests/ -q`
  (must stay green) and, at the end, `bash bin/validate-macos.sh --quick`.
- Do NOT touch Fedora/Arch/dnf/pacman — that's deferred to the next version.
- Do NOT re-introduce the silent auto-uninstall. Keep `_confirm_uninstall`
  fail-safe (non-TTY → False unless `ASCENDO_DEDUP_AUTO_UNINSTALL=1`).

## MUST-DO (P1 — pre-push blockers)

### 1. Deduplicator: report-only by default + consent surface
`core/ascendo/orchestrator/deduplicator.py` runs after every CHECK (`runner.py:270`),
including read-only "Quick check". The fail-safe already prevents silent uninstall.
Finish it per the audit recommendation:
- **Stop mutating CHECK sidecars in report-only mode.** When `_confirm_uninstall`
  returns False (the dashboard default), do NOT flip `item.status`/`item.action`
  or rewrite the sidecar — only generate `DEDUPLICATION_REPORT.md` with the
  recommended source + manual uninstall commands. Mutation + `DEDUPLICATION_TASKS.json`
  should happen ONLY in the opt-in (auto-uninstall) path.
- **Add a dashboard consent surface** so a user can approve a dedup uninstall
  explicitly: a `GET /dedup/pending` (reads the latest run's recommended fixes)
  and a `POST /dedup/apply` that writes `DEDUPLICATION_TASKS.json` for a
  user-selected set and triggers the apply — i.e. consent is an explicit click,
  never an implicit non-TTY default. Surface it as an "Action required → resolve
  duplicate" card (reuse the Sesja-79 action-required pattern).
- Tests: report-only run generates the report but leaves sidecars unmutated;
  opt-in path still queues tasks (the existing `monkeypatch.setenv(...)` tests).

### 2. Honest-status reaches the UI (pills)
The backend now emits honest statuses (`failed`, `triggered_pending`) but
`app/frontend/components.js:32-35` `STATUS()` collapses unknown statuses to
`"neutral"`, and `style.css`/`components.css` lack `st-failed`/`asc-pill--err`
for these. Add a status→variant translation in the Apps/Categories render path so:
- `failed` → red/err pill, `triggered_pending` → amber/warn ("pending vendor"),
  `outdated`/`planned` → amber/info, `up_to_date` → green/ok, `missing` → err,
  `skipped` → neutral.
- Add the missing CSS variants. Verify live in both light + dark themes that a
  failed apply shows a clearly-red pill (not neutral grey, never green).

## SHOULD-DO (P2 — strongly recommended before push)

### 3. Stream-log race (real fix)
`run_async.py:582-615` stores `stream_log_path` on `RunState` but **still mutates
the process-global `os.environ[ASCENDO_STREAM_LOG]`** with save/restore — two
concurrent read-only / cross-category applies race it. Thread the per-run path to
the subprocess via the `env=` argument at spawn time (in the manager/runner that
launches the bash scripts) and stop mutating `os.environ`. Test:
`test_concurrent_runs_do_not_clobber_stream_log`.

### 4. A5 — kill the core→adapter coupling
`dashboard/routes/web_config.py` imports `ascendo_macos.web_registry` (lines
38/64/130/145/240) and `service.py` imports `ascendo_windows`. Add optional
`IAdapter.web_registry()` / `service_manager()` methods (return `None` when
unsupported) and route the dashboard through the resolved adapter instead of
importing adapter packages in core. Document in ADR-0005. Test:
`test_dashboard_uses_adapter_web_registry`.

### 5. LAN safety on the dashboard
`app/dashboard/app.py`: if the bind host is non-loopback and CORS is wildcard,
refuse to start (or require an explicit `--allow-remote`/env flag) and log a
warning; add an Origin/CSRF or localhost capability-token check on the mutating
endpoints (`/runs/async`, `/elevation/auth`, `/dedup/apply`). Test the refusal +
the token gate.

### 6. macOS web-handler honesty (W10 + W2)
- **W10:** make `adapters/macos/lib/web_discovery.sh` emit an explicit
  `DISCOVERY_OK` / `DISCOVERY_FAILED` line and have `scripts/web/check.sh`
  distinguish "0 apps" from "discovery crashed" (assert ≥1 emitted line or the
  ok flag). Today a discovery crash reads as "all current". Add a bats test.
- **W2:** in `lib/handlers/release_feed.sh`, when `version_regex` is configured
  but does NOT match, return a `probe_broken` exit code — do not silently fall
  back to the raw value. Add a bats test with a non-matching feed.

## NICE-TO-DO (P3 — cleanup; skip if time-boxed)
- **W11:** replace the remaining `sort -V` in `adapters/macos/scripts/{npm,pip}/{check,plan,apply}.sh`
  with the Python version comparator already used elsewhere.
- **Dead code:** either wire or delete `InventoryDB.reconcile()` +
  `scan_meta`/`set_scan_complete`/`get_scan_meta` (currently zero call sites —
  orphan eviction already works via `_replace_buckets_in_db` clear+replace) and
  `core/ascendo/plugins_loader/` (unwired). Record the decision in an ADR + fix
  the now-misleading CHANGELOG "Added — Inventory hardening" entry.

## Finish
- Update `CHANGELOG.md` ([Unreleased]) + add a one-line `PLAN.md` note +
  append a short macOS section to `HANDOFF.md` Sesja 84.
- Final: full pytest green + `bash bin/validate-macos.sh --quick` green.
- `git push origin main`. Tell the operator which MUST-DO/SHOULD-DO items landed.
