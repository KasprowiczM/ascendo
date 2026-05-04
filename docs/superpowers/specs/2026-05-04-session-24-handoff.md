# Sesja 24 handoff — M5.3 implementation paused after Task 4 implementation (reviews NOT yet dispatched)

> **Status:** paused at user request (approaching session usage limit).
> Resume by dispatching the spec-compliance + code-quality reviewers for
> Task 4 (commit 9babbf1) using the prompts embedded below, then continue
> with Task 5.

---

## Where we are

Branch `claude/musing-herschel-b52e7e` (current worktree).
33 commits ahead of `origin/main` (M5.2 already pushed; M5.3 is local-only so far).

M5.3 commits (newest first):

```
9babbf1 feat(macos): MacOSInventory Python wrapper (M5.3.4)        ← Task 4 implemented, NO REVIEWS YET
8e13fd9 fix(macos): inventory list.sh test review findings (M5.3.3 follow-up)
f029c9c feat(macos): scripts/inventory/list.sh — LaunchServices enumeration (M5.3.3)
c419d4f test(macos): add system_profiler fixture for inventory tests (M5.3.2)
3b5f65d feat(core): add SourceType.SYSTEM for macOS adapter (M5.3.1)
9dc2f36 docs(plan): M5.3 macOS adapter — LaunchServices inventory impl plan
4eac123 docs(spec): M5.3 macOS adapter — LaunchServices inventory design
```

Plus M5.2 above that (commit `1e01a64` and earlier) already pushed + tagged `v0.0.9-alpha`.

Working tree clean.

## Tasks status

| # | Task | Status |
|---|---|---|
| 1 | `SourceType.SYSTEM` enum + schema regen | ✅ committed `3b5f65d` (mechanical, no review cycle per plan) |
| 2 | system_profiler test fixture | ✅ committed `c419d4f` (mechanical) |
| 3 | `scripts/inventory/list.sh` + 6 tests | ✅ committed `f029c9c`+`8e13fd9`, spec ✅, code-quality ✅ (after fix) |
| 4 | `MacOSInventory` Python wrapper + 8 tests | ✅ **committed `9babbf1`, spec ⏳ NOT YET, code-quality ⏳ NOT YET** (8/8 new + 123/123 full macOS adapter suite green) |
| 5 | `MacOSAdapter` wire-up (capability flip + cached inventory + `_system_profiler_status`) | pending |
| 6 | `bin/validate-macos.sh` Stage 9 (real-hardware probe) | pending |
| 7 | `bin/run-tag-release-macos.sh` tag bump v0.0.9 → v0.0.10 | pending |
| 8 | Real-hardware validation + tag `v0.0.10-alpha` + HANDOFF Sesja 25 + push (operator) | pending |

## Plan adaptations beyond the plan doc (CRITICAL — next session must know)

The plan at [docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md](../plans/2026-05-04-macos-inventory-launchservices.md) was followed but with these in-flight adaptations:

### Task 1 — no adaptations (planned)

### Task 2 — no adaptations (fixture: 14 total entries = 13 valid + 1 malformed; this matches the corrected Task 3 test assertions)

### Task 3 (commit f029c9c + 8e13fd9)

1. **Added `SourceType.INVENTORY = "inventory"`** to `core/ascendo/models/package.py` (NOT in plan). The sidecar's `category` field is Pydantic-typed against `SourceType`, and "inventory" wasn't in the enum — every test sidecar would fail Pydantic validation otherwise. Schema regenerated. Pattern matches BREW/MAS — manager-level enum value used as sidecar category, not as per-item source.
2. **Test assertion corrections vs plan** (plan had off-by-ones):
   - `test_emits_one_item_per_app`: `len(sc.items) == 13` (NOT 14)
   - `test_classification_distribution`: `web == 3` (NOT 4)
   - `test_no_brew_falls_through_to_web`: `web == 6` (NOT 7)
3. **`${VAR+set}` env var pattern** for MAS_BIN/BREW_BIN test isolation (distinguishes "not set" from "set empty").
4. **Synthesized failed-item before `exit 30`** in list.sh — the sidecar status heuristic counts items not messages, so `json_add_message "error"` alone leaves status="success". Adding `json_add_item "inventory:system-profiler-error" "" "" "failed" "inventory"` drives status="failed".
5. **Manual tab-substring extraction** instead of `IFS=$'\t' read` — bash 3.2 IFS edge-case workaround for empty-first-field rows.
6. **Fake system_profiler `--version` handling** added in 8e13fd9 (test-only cosmetic).

### Task 4 (commit 9babbf1) — **NEEDS REVIEW**

1. **`Package` model extended** at `core/ascendo/models/package.py`: added `source: ItemSource | None = Field(default=None, ...)` field. Original `Package` had `extra="forbid"` and no `source` field. Made `Optional` with `default=None` so all existing Windows code that constructs `Package` without `source` continues to work unchanged. **This is a real core schema change — review reviewer needs to verify it doesn't break any existing assumption** (Windows inventory code may need to start passing source explicitly or stay unchanged depending on downstream behavior).
2. **Test helper field corrections** (plan's verbatim `_item`/`_minimal_sidecar` had drift vs actual Pydantic models):
   - `_item` requires `name` and `category` fields
   - `_item.target_version: ""` removed (empty string fails `VersionStr min_length=1`; field is Optional with None default)
   - `_minimal_sidecar.summary.needs_reboot` removed (`Summary` has `extra="forbid"` and no `needs_reboot` field)
3. **`sidecar.schema_.value` not `sidecar.schema.value`** — Sidecar field is `schema_` (alias `"schema"`) to avoid shadowing Pydantic's `.schema()` class method.

## Resume protocol

### 1) First: dispatch Task 4 reviews IN PARALLEL

**Spec-compliance review** — `general-purpose`, model `haiku`:

```
Spec-compliance review of commit 9babbf1 — Task 4 of M5.3 macOS adapter (MacOSInventory Python wrapper).

Working directory: /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
cd into it.

Base: 8e13fd9   Head: 9babbf1
Plan: docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md §Task 4
Spec: docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §2

What was requested:
- adapters/macos/ascendo_macos/inventory.py (~250 LOC) — MacOSInventory(IInventory) mirroring WindowsInventory
- adapters/macos/tests/test_macos_inventory_smoke.py — 8 mock-based tests covering: identity, list_installed sidecar parsing, categories filter, missing sidecar → ManagerError, script-exit-30 → ManagerError, timeout → ManagerError, non-macOS host returns [], emit_sidecar shape

Hard requirements:
- Public surface mirrors WindowsInventory (SCRIPT_REL, DEFAULT_TIMEOUT_SEC, __init__, list_installed, emit_sidecar, _build_argv, _sidecar_to_packages, _format_missing_sidecar_error, _resolve_bash)
- Host gate: list_installed on non-macOS host returns [] cleanly
- Per-call uuid4 + private tempdir (no sidecar collision)
- read_sidecar via M2.4 sidecar_io
- Categories filter post-hoc

Implementer reported these adaptations:
1. Package model extended with `source: ItemSource | None = Field(default=None)` — needed for test code to read `p.source.type`. Backward-compatible. Verify it doesn't break Windows inventory or other Package consumers.
2. Test helper field corrections (Item needs name+category; target_version="" fails min_length=1; Summary has no needs_reboot).
3. sidecar.schema_.value (Pydantic field alias).

Verify by:
  git diff --stat 8e13fd9..9babbf1
  git show 9babbf1 -- adapters/macos/ascendo_macos/inventory.py
  git show 9babbf1 -- adapters/macos/tests/test_macos_inventory_smoke.py
  git show 9babbf1 -- core/ascendo/models/package.py
  PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_macos_inventory_smoke.py -v
  PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/ adapters/windows/tests/ -q   # CRITICAL: verify Package change doesn't break other adapters

Reported: 8/8 new + 123/123 macOS adapter suite green.

Specifically check:
- Package.source addition: backward-compatible? Does the field default=None break any existing test that asserts Package shape?
- list_installed: host gate returns [] on non-macOS; uuid4-per-call + tempdir; categories filter applied post-parse
- _build_argv structure: [bash_path, script_path, --run-id ..., --trigger cli, --profile default, --output-dir ...]
- emit_sidecar: returns Sidecar with phase=check, category=inventory, items[] from packages, ToolInfo(name=system_profiler)

Report: ✅ Spec compliant or ❌ Issues found. Specifically rule on the Package.source addition. Under 300 words.
```

**Code-quality review** — `superpowers:code-reviewer`, model `sonnet`:

```
Review code quality of commit 9babbf1 (Task 4 of M5.3 — MacOSInventory Python wrapper).

Working dir: /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
cd into it.

WHAT_WAS_IMPLEMENTED: MacOSInventory(IInventory) mirroring WindowsInventory.
Spawns the bash list script via subprocess, parses ascendo/v1 sidecar via
read_sidecar, returns list[Package]. Per-call uuid4 + private tempdir to
avoid sidecar filename collisions. categories filter post-hoc on
SourceType.value. Plus a non-trivial extension: `Package.source: ItemSource | None`
field added to core/ascendo/models/package.py to support inventory's
need to round-trip the source classification through Package objects.

PLAN_OR_REQUIREMENTS: Task 4 from docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md
BASE_SHA: 8e13fd9
HEAD_SHA: 9babbf1

Files to review:
- adapters/macos/ascendo_macos/inventory.py
- adapters/macos/tests/test_macos_inventory_smoke.py
- core/ascendo/models/package.py (the Package.source addition)

Reference templates:
- adapters/windows/ascendo_windows/inventory.py (canonical Python template)
- adapters/macos/ascendo_macos/managers/brew.py (bash discovery pattern)
- core/ascendo/orchestrator/sidecar_io.py (read_sidecar contract)
- core/ascendo/interfaces/inventory.py (IInventory ABC)

Specifically check:
- Public surface matches IInventory ABC + WindowsInventory pattern
- Host gate: returns [] on non-macOS (no shell-out attempted)
- Subprocess invocation: timeout, capture_output, env handling
- Error mapping: TimeoutExpired/OSError/SidecarReadError/SidecarIOError → ManagerError
- _resolve_bash: handles missing /bin/bash gracefully? Falls back to PATH bash?
- Categories filter: case-sensitivity match against SourceType.value
- emit_sidecar: matches the Sidecar contract (started_at < finished_at, summary consistent with items, etc.)
- _sidecar_to_packages: Item → Package mapping (does it correctly carry source through? The new Package.source field is the receiver)
- Test isolation: each test patches subprocess.run independently, no shared state
- Test for non-macOS host returning []: actually exercises the gate
- Test mock: subprocess.run patch produces canned sidecar at the right path
- The Package.source addition: backward-compatible? Are there other places (Windows inventory, brew/mas managers) that build Package objects without source? Do they still work?
- Import order, type hints, docstrings — consistent with WindowsInventory

Report: Strengths, Issues (Critical/Important/Minor with file:line), Assessment. Under 300 words.
```

Run BOTH in parallel via single message with two Agent tool calls.

### 2) If reviews ✅: proceed to Task 5

Use `general-purpose`, model `sonnet`. Plan §Task 5 is self-contained. Quick summary:

- `adapters/macos/ascendo_macos/adapter.py`:
  - `capabilities` flips to `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY`
  - `inventory()` returns lazy-init cached `MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)` (singleton)
  - `health_check()` adds `out["system_profiler"] = self._system_profiler_status()`
  - New `_system_profiler_status()` mirrors `_jq_status` pattern (try `system_profiler -listDataTypes`, check SPApplicationsDataType in stdout)
  - Update class docstring to reflect M5.3 scope (currently still says M5.2)
- 3 wiring tests in `adapters/macos/tests/test_adapter_smoke.py`:
  - `test_capabilities_includes_inventory`
  - `test_inventory_returns_macosinventory_singleton` (asserts `is` for cached singleton)
  - `test_health_check_includes_system_profiler_component`

Pre-existing M5.2-era test asserting `inventory() is None` will need adapting — treat as legitimate state evolution, not silent regression masking. Reviewer must verify.

### 3) Tasks 6-7

Plan has full text. Task 6 = validate-macos.sh Stage 9 (real-hardware probe). Task 7 = tag bump (mechanical, single subagent + eyeball, no review cycle).

### 4) Task 8 — operator-driven (same pattern as M5.2 Task 12)

Operator runs:
```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
bash bin/validate-macos.sh                  # expect ALL CHECKS PASSED with new Stage 9
bash bin/run-tag-release-macos.sh           # NO --mas needed (M5.3 has no apply phase)
                                            # confirm 'apply' at brew gate
git tag -l v0.0.10-alpha                    # confirm tag
git show v0.0.10-alpha --stat
```

**No `$SUDO_PW` needed for M5.3** — inventory has no sudo invocation. brew apply at Stage 4 still gates on `apply` confirmation but doesn't sudo. Stage 9 is read-only.

After tag verified, dispatch the docs subagent: prepend Sesja 25 entry to HANDOFF.md, flip M5.3 row in PLAN.md to ✅ done, commit + push branch + push tag.

## Working directory + branch

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
git status              # should be clean
git log --oneline main..HEAD | head -10
```

## Plan + spec references

- Spec: [docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md](2026-05-04-macos-inventory-launchservices-design.md)
- Plan: [docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md](../plans/2026-05-04-macos-inventory-launchservices.md)
- M5.2 Sesja 23 handoff (review-cadence reference): [2026-05-03-session-23-handoff.md](2026-05-03-session-23-handoff.md)
- This handoff: [2026-05-04-session-24-handoff.md](2026-05-04-session-24-handoff.md)

## Notes / lessons from this session

1. **Plan vs reality:** This M5.3 plan also had drift — the verbatim test code in §Task 4 referenced `Item` and `Summary` shapes that have evolved. The implementer caught and fixed during TDD. Pattern: trust the live source code (Pydantic models, ABC contracts) over the plan's inline samples.

2. **Off-by-one in plan §Task 3 test assertions:** Plan said `web == 4` and `len(items) == 14` and `web == 7` (no-brew). Real fixture has 13 valid + 1 malformed (Empty Path App), classifying as 4 SYSTEM + 3 MAS + 3 BREW + 3 WEB. Corrections: `web == 3`, `len == 13`, `no-brew web == 6`. Pre-applied when dispatching Task 3.

3. **`SourceType.INVENTORY` was an unplanned core addition:** sidecar.category field is Pydantic-typed; "inventory" wasn't in the enum. Added as a manager-level enum (matches BREW/MAS pattern). The schema regen captured it.

4. **`Package.source` is a NEW core field:** `Optional[ItemSource]`, `default=None`. Backward-compatible by construction but **the spec reviewer should rule on whether it's the right design.** Alternative would have been to attach the source as a separate dict alongside the Package list, but that fights the IInventory ABC return type `list[Package]`.

5. **Review cadence held:** Tasks 1, 2 mechanical (skipped reviews per plan). Task 3 got full spec+code-quality review + 1 fix commit. Task 4 implemented but reviews PENDING — that's the immediate next action on resume.

6. **Mac.r12.home environment:** mas 6.0.1, brew 5.1.8, jq 1.8.1, bash 3.2.57, ~13 mas apps, 0 outdated, App Store signed in. Real-hardware validation in Task 8 will use this baseline.
