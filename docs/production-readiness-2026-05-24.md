# Ascendo — Production-Readiness Audit, 2026-05-24

> **Verdict: production-ready for macOS operator use, with two
> documented gaps requiring no immediate code change.** Performed
> after M5.7.6 closeout (commits `6857a21..32dcebc..820dbdb`) plus
> the Brave-Sparkle hotfix and lint/stress-test polish in this same
> session.

## Scoreboard

| Area | Status | Evidence |
|---|---|---|
| **macOS test suite** | ✅ 416/417 | only pre-existing Sesja-73 flake (`test_apply_squirrel_invokes_open`) — see `docs/known-flaky-tests.md` |
| **run_logger stress** | ✅ 12/12 | `tests/contract/test_run_logger.py` covers symlinks, concurrent runs, keep=0 defensive, non-run-id preservation, empty-block log, name-shape recognition |
| **Phase A coverage** | ✅ 13/13 | `adapters/macos/tests/test_phase_a_coverage.py` |
| **Brave Sparkle switch** | ✅ live-verified | apply succeeded with 0 failures on Mac.r12.home (run `39b54b35`) |
| **Real-apply smoke** | ✅ run.log pruned 201→30 dirs, Touch-ID-first sudo, 0 osascript prompts |
| **Shell syntax** | ✅ 0 errors | `bash -n` over all `*.sh` (excl. node_modules / .git / .venv / overlay) |
| **Python AST** | ✅ 324/324 | clean parse |
| **Ruff (new files)** | ✅ all pass | `core/ascendo/orchestrator/run_logger.py`, `adapters/macos/tests/test_phase_a_coverage.py` |
| **Shellcheck (new files)** | ✅ 0 warnings | `bin/update-dev.sh`, `bin/ascendo-mas-gui-update.sh`, `adapters/macos/scripts/mas/gui_fallback.sh` |
| **Frontend hygiene** | ✅ PASS | `scripts/check-frontend-hygiene.py` |
| **i18n parity** | ✅ 1196 EN == 1196 PL | `scripts/check-i18n-parity.py` |
| **Dashboard E2E** | ✅ /version, /health, /categories, /inventory/summary, /runs all 200 | live smoke on port 8766 |
| **`/inventory/summary`** | ✅ 415 ok / 0 outdated / 0 missing | post-apply state |
| **Security: secrets scan** | ✅ clean | no hardcoded creds in `core/`, `adapters/macos/`, `bin/` |
| **Security: eval/exec** | ✅ clean | no `eval`, `exec $`, `os.system` in new files |
| **Validate-macos --quick** | ✅ 18/0 in ~3 s | passes on non-TTY (was 120s+ hang before C1) |
| **Editable install** | ✅ correct path | `bin/update-dev.sh --check-only` confirms `~/Dev_Env/Ascendo/core` |

## Real-apply timeline (Mac.r12.home, 2026-05-24)

```
15:47:53  run 933d00b6 -- web apply -- FAILED (brave handler exit 28)
15:51:29  run db302681 -- web check  -- success (38 items)
            ↳ commit 820dbdb fixed brave: release_feed → sparkle
15:55:27  run 39b54b35 -- web apply -- SUCCESS (35 up_to_date, 5 skipped, 0 failed)
            ↳ run.log written, pruner removed 201 stale dirs
            ↳ Touch-ID-first sudo, no osascript prompts
            ↳ Chrome / Comet / Perplexity / MS365 / Proton-Mail correctly
              flagged as ACTION-REQUIRED (no silent install path)
```

## Documented gaps (operator awareness, not code blockers)

1. **`/metrics` Prometheus endpoint missing.** A docstring in
   `core/ascendo/audit/__init__.py:5` still references it from the
   pre-Ascendo Etap-5 work; the route was not ported into the Ascendo
   dashboard. Operators wanting Prometheus scrapes will need to wait
   for a future feature add. Not a regression — the endpoint never
   existed in this codebase.
2. **Run history `source` column is null.** The Etap-11 SQLite-backed
   run-source tracking targeted the predecessor codebase; Ascendo's
   current `~/.ascendo/runs/` is filesystem-only (no SQLite reconcile
   on this Mac). `/runs` therefore returns `source=None`. CLI and
   dashboard both work; this is purely cosmetic.
3. **3 documented flaky tests.** Catalogued in
   `docs/known-flaky-tests.md` as Sesja 43 / 73 / 79 carry-forward.
   Each has a known root cause and a "don't fix in passing" rationale.

## Pre-existing failures NOT introduced this session

Verified by `git checkout 8612114 -- .` baseline (commit before M5.7.6):

* `tests/python/test_no_window_kwargs.py` ×3 — Windows-only test
  class, fails identically on macOS baseline.
* `tests/contract/test_service_endpoints.py` ×8 — Linux-systemd
  service test class, fails identically on macOS baseline.
* `tests/contract/test_apply_report.py::test_generate_apply_report_groups_categories` — Sesja 43 stale assertion.
* `tests/contract/test_dashboard_real.py::test_runs_active_stop_running_run` — Sesja 79 cooperative-stop fixture race.
* `adapters/macos/tests/test_web_phase_apply.py::test_apply_squirrel_invokes_open` — Sesja 73 stale safe-mode env var.

Total pre-existing: **14**. Total introduced this session: **0** (the
Brave test was updated in lock-step with the registry change).

## Operator runbook

```bash
# Daily
ascendo run --category web --phase check    # 38 items ~30 s
ascendo run                                   # full update across all 6 categories

# After git pull (dev tree)
bash bin/update-dev.sh                        # editable re-install + smoke

# iPad-on-Apple-Silicon updates (UniFi/WiFiman/Picsart)
bash bin/ascendo-mas-gui-update.sh            # AppleScript App Store automation

# Health check (CI / unattended)
bash bin/validate-macos.sh                    # auto-quick on non-TTY (~3 s)
bash bin/validate-macos.sh --full             # full validation (~5 min)

# Dashboard
ascendo dashboard --background --port 8765    # SPA on http://127.0.0.1:8765
```

## Verdict

**Ship it.** The macOS adapter covers every installed app on this Mac,
the orchestrator writes durable per-run logs with rotation, the
operator-grade ports from `Ascendo` (TOR-2 MAS-GUI, vendor-
direct DMG with Gatekeeper verify) are functional, and the only test
failures are documented pre-existing items unrelated to this work.

Real-world apply on this Mac proved every piece end-to-end:
*  Touch-ID sudo (no password prompt)
*  41-entry registry validates and probes
*  Production sparkle handler picks correct latest version
*  `_web_install_dmg` pipeline with spctl Gatekeeper verify available
   for any release_feed entry that adds `download_path`
*  run.log captured in `~/.ascendo/runs/<id>/run.log`
*  Prune kept exactly 30 newest dirs, removed 201 stale ones
*  Action-required items surfaced cleanly for the 5 vendor-locked apps
