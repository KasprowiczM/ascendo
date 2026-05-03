# Sesja 23 handoff — M5.2 implementation paused after Task 5

> **Status:** paused at user request (approaching session usage limit).
> Resume by dispatching the spec-compliance review for Task 5, then continuing.

---

## Where we are

Branch `claude/musing-herschel-b52e7e` (current worktree).
11 commits ahead of `origin/main`:

```
6d35d2b feat(macos): MasManager Python adapter (M5.2.5)              ← Task 5 implemented
c259517 fix(macos): check.sh review nits — DRY_RUN comment + dry-run test asserts items
2532b21 feat(macos): scripts/mas/check.sh — sign-in + inventory phase (M5.2.4)
7a3c7ac fix(macos): ascendo_mas.sh review findings (M5.2.3 follow-up)
9ddfbdb feat(macos): lib/ascendo_mas.sh helpers (M5.2.3)
7d81385 fix(macos): MacElevation review findings (M5.2.2 follow-up)
e35d3ce docs(handoff): Sesja 22 — M5.2 paused at Task 2 review gate
8810e6e feat(macos): MacElevation impl for M5.2.2
13209f1 feat(core): add SourceType.MAS for macOS adapter (M5.2.1)
4324e3c docs(plan): M5.2 macOS adapter — mas + MacElevation impl plan
336a725 docs(spec): M5.2 macOS adapter — mas + MacElevation design
```

Working tree clean.

## Tasks status

| # | Task | Status |
|---|---|---|
| 1 | `SourceType.MAS` enum + schema regen | ✅ committed `13209f1`, spec ✅, code quality ✅ |
| 2 | `MacElevation` impl + 10 unit tests | ✅ committed `8810e6e`+`7d81385`, spec ✅, code quality ✅ (12 tests) |
| 3 | `lib/ascendo_mas.sh` helpers | ✅ committed `9ddfbdb`+`7a3c7ac`, spec ✅, code quality ✅ (7 tests) |
| 4 | `scripts/mas/check.sh` + integration tests | ✅ committed `2532b21`+`c259517`, spec ✅, code quality ✅ (5 tests) |
| 5 | `MasManager` Python adapter + 14 unit tests | ✅ **committed `6d35d2b`, spec ⏳ NOT YET, code quality ⏳ NOT YET** (15 tests pass; 85/85 full suite pass) |
| 6 | `MacOSAdapter` wire-up | pending |
| 7 | `scripts/mas/{plan,verify,cleanup}.sh` + 6 triplet tests | pending |
| 8 | `scripts/mas/apply.sh` + 5 tests | pending |
| 9 | Dashboard `/elevation/*` endpoints + 6 contract tests | pending |
| 10 | `bin/validate-macos.sh` Stage 8 | pending |
| 11 | `bin/run-tag-release-macos.sh --mas` + tag bump | pending |
| 12 | Real-hardware validation + tag `v0.0.9-alpha` | pending |
| 13 | HANDOFF + PLAN docs + push | pending |

## Test counts so far

| Test file | Count |
|---|---|
| `test_elevation_smoke.py` | 12 |
| `test_ascendo_mas_helpers.py` | 7 |
| `test_check_mas_script.py` | 5 |
| `test_mas_manager_smoke.py` | 15 (5-phase parametrized counted as 5) |
| Pre-existing M5.1 + Task 1 contract | ~46 |
| **Full macOS suite** | **85/85 green** |

## Resume protocol

### 1) First: finish Task 5 reviews

**Spec compliance review** — dispatch `general-purpose`, model `haiku`:

```
Spec-compliance review of commit 6d35d2b — Task 5 of M5.2 macOS adapter.

Working directory: /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
Base: c259517   Head: 6d35d2b

What was requested:
- Create adapters/macos/ascendo_macos/managers/mas.py (~280 LOC)
- Create adapters/macos/tests/test_mas_manager_smoke.py (~14-15 tests)

Required: MasManager mirrors BrewManager exactly with these additions:
- __init__ takes IElevation; stored as self._elevation
- _last_env_for_test test seam dict
- is_available enforces mas major >= 4 via _mas_major_at_least
- _build_env(phase): for Phase.APPLY only, when has_password_registered()
  AND askpass_path() not None, set SUDO_ASKPASS=<helper-path>; otherwise
  return parent env unchanged
- run_phase sets self._last_env_for_test = self._build_env(phase) before
  _run_streaming, so tests can read it after the call
- _run_streaming passes env to subprocess.Popen
- SCRIPT_BY_PHASE: mas/check.sh, mas/plan.sh, mas/apply.sh, mas/verify.sh, mas/cleanup.sh
- SOURCE_TYPE = SourceType.MAS, display_name = "Mac App Store (mas CLI)"

Required tests: identity, OS gate (Linux/Win/macOS), mas-missing, jq-missing,
mas<4, mas>=4, parametrized 5-phase argv dispatch, SUDO_ASKPASS injected
on Phase.APPLY when password registered, NOT injected when not, ManagerError
on missing-sidecar.

Implementer claims DONE: 15/15 new tests + 85/85 full suite. Two test stubs
needed `started_at` field added to RunInfo / _minimal_sidecar (test code only,
not implementation).

Verify by reading code + running:
  PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_mas_manager_smoke.py -v
  git diff --name-only c259517..6d35d2b   # should show only the 2 files
  git show 6d35d2b -- adapters/macos/ascendo_macos/managers/mas.py

Report: ✅ Spec compliant or ❌ Issues found with file:line references.
```

**If spec ✅:** Code-quality review — dispatch `superpowers:code-reviewer`, model `sonnet`:

```
Review code quality of commit 6d35d2b (Task 5 of M5.2).

Working dir: /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e

WHAT_WAS_IMPLEMENTED: MasManager Python adapter mirroring BrewManager,
plus IElevation dependency injection + Phase.APPLY-only SUDO_ASKPASS env
injection. is_available enforces mas major >= 4 via subprocess `mas version`
parse. 15 mock-based tests at adapters/macos/tests/test_mas_manager_smoke.py.

PLAN_OR_REQUIREMENTS: Task 5 from docs/superpowers/plans/2026-05-03-macos-mas-elevation.md.
BASE_SHA: c259517
HEAD_SHA: 6d35d2b

Specifically check:
- _build_env() is purely additive (parent env preserved)
- SUDO_ASKPASS injection conditional on BOTH has_password_registered() AND
  non-None askpass_path()
- _mas_major_at_least handles parse errors / TimeoutExpired / OSError gracefully
- Tests independent of each other
- _run_streaming env propagation (Popen kwarg)
- _last_env_for_test test seam: cleared/reset between phase invocations?
- Bash discovery (_resolve_bash) reused from brew pattern

Report: Strengths, Issues (Critical/Important/Minor), Assessment.
```

If reviewer flags issues, dispatch a fix subagent (`general-purpose`, sonnet) with the findings, then re-review.

### 2) Then: continue with Task 6 — MacOSAdapter wire-up

Use `general-purpose`, model `sonnet`. Full task in plan §Task 6. Quick summary:

- Modify `adapters/macos/ascendo_macos/adapter.py`:
  - `capabilities` flips to `PACKAGE_MANAGEMENT | ELEVATION`
  - `package_managers(host)` returns `[BrewManager(...), MasManager(..., elevation=self.elevation())]` in that order (brew first because mas itself is brew-installed)
  - `elevation()` returns a cached `MacElevation` singleton (lazy init in `_cached_elevation`)
  - `health_check()` adds `mas` component via new `_mas_status()` method
- Add 4 wiring tests to `adapters/macos/tests/test_adapter_smoke.py`:
  - `test_capabilities_includes_elevation`
  - `test_package_managers_includes_brew_and_mas`
  - `test_elevation_returns_macelevation` (asserts singleton via `is`)
  - `test_health_check_includes_mas_component`

Add `from .managers.elevation import MacElevation` and `from .managers.mas import MasManager` imports.

### 3) Tasks 7-13

Same pattern. Plan has full text. Model selection from Sesja 22 handoff still applies.

## Working directory + branch

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
git status              # should be clean
git log --oneline -12
```

## Plan + spec references

- Spec: `docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md`
- Plan: `docs/superpowers/plans/2026-05-03-macos-mas-elevation.md`
- Sesja 22 handoff: `docs/superpowers/specs/2026-05-03-session-22-handoff.md`
- This handoff: `docs/superpowers/specs/2026-05-03-session-23-handoff.md`

## Notes / lessons from this session

1. **Plan vs reality:** The plan's Task 4 prompt referenced an old `json_init` signature with a `"ascendo/v1"` first arg. The real `lib/ascendo_json.sh` API (shipped in M5.1) doesn't take that — it's `json_init <phase> <category> <run_id> ...`. Same with `json_save_on_exit` taking only `<output_dir>`. Future tasks reusing this pattern (Tasks 7, 8) should follow the brew template at `adapters/macos/scripts/brew/check.sh`, NOT the plan's outdated prompt verbatim.

2. **`error` not `err`:** The `json_add_message` level for failures is `error` (4 letters). The plan's prompt used `err` in a few spots — corrected on the way through.

3. **Pydantic `Sidecar.run` requires `started_at`:** The plan's `_minimal_sidecar` stub for Task 5's tests was missing `run.started_at` — implementer fixed in test code only.

4. **`_last_env_for_test` test seam:** MasManager exposes `self._last_env_for_test` as an introspection point for the SUDO_ASKPASS env-injection tests. Two of the 15 tests depend on it.

5. **CVE-2025-43411 enforcement:** Task 8 (`apply.sh`) MUST always invoke `sudo -A mas upgrade`, never bare `mas upgrade`. The Python `MasManager` doesn't enforce this — the bash script does. `sudo -A` falls back to TTY prompt when `SUDO_ASKPASS` env unset.
