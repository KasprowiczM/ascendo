# Known flaky / pre-existing test failures

> Last updated: 2026-05-24 (Sesja 12, M5.7.6 closeout)
>
> These three failures are documented as carry-forward in HANDOFF Sesja 81
> and confirmed pre-existing on the pristine baseline (stash-verified).
> They predate the M5.7.6 macOS coverage closeout and were not introduced
> by Phase A/B/C/D work. Triage is **document, don't fix in this pass**:
> each fix touches a different code area and deserves its own session.

## 1. `tests/contract/test_dashboard_real::test_runs_active_stop_running_run`

* **First seen:** Sesja 79 (cooperative-stop landing).
* **Symptom:** intermittent timeout when the run process does not honour
  the cooperative SIGINT within the 5 s test window. Reproduces ~30 % of
  the time; flaky, not deterministic.
* **Why we don't fix here:** the actual cooperative-stop code is correct;
  the test fixture races the orchestrator's async startup. Fix is to
  await `runs_active_stream()` returning the first heartbeat before
  asserting state, not the orchestrator code.
* **Tracker:** carry-forward in HANDOFF Sesja 81.

## 2. `adapters/macos/tests/test_apply_squirrel_invokes_open` (Sesja-73 stale)

* **First seen:** Sesja 73 (web safe-mode landing).
* **Symptom:** test sets `ASCENDO_SUDO_WARM_DISABLE=1` to short-circuit
  the warm-helper. Sesja 81 sudo-warm rewrite changed the gate — the
  test fixture has not been updated to match.
* **Why we don't fix here:** the production code is correct (Touch-ID-
  first sudo verified live in Sesja 81 4-scenario matrix). Test needs
  the new `ASCENDO_SUDO_WARM_DISABLE` semantics applied to its mock.
* **Tracker:** Sesja 73 stale-test cluster.

## 3. `tests/contract/test_apply_report::test_generate_apply_report_groups_categories`

* **First seen:** Sesja 43 (apply_report grouping).
* **Symptom:** assertion on grouping order is too tight; the underlying
  dict-iteration order changed between Python 3.11 and 3.13.
* **Why we don't fix here:** correctness is unaffected (the report
  groups correctly; only the test assertion is brittle). Fix is to
  switch the assertion to a `set`-comparison or sort the groups.
* **Tracker:** Sesja 43 grouping-test cluster.

## Operating procedure

* New work in this repo MUST NOT modify the 3 documented tests above
  unless the change is explicitly fixing them in its own session.
* CI mark them as `xfail(strict=False)` or use `pytest --rerunfailures=2`
  is acceptable, but **never `@pytest.mark.skip`** — we want them green
  the moment they're fixed.
* When a fix lands, delete the corresponding entry here.
