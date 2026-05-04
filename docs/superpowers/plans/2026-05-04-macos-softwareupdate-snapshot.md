# macOS adapter — M5.4 softwareupdate + Time Machine read-only implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `python -m ascendo run --category softwareupdate --phase {check,plan,apply,verify,cleanup}` end-to-end on macOS + `MacOSAdapter.snapshot()` returning `TimeMachineSnapshot` (read-only ISnapshot listing local APFS snapshots). Tag `v0.0.11-alpha`.

**Architecture:** Mirrors M5.2 (mas) + M5.3 (inventory) patterns. SoftwareUpdateManager inherits MasManager's structure (5-phase contract, MacElevation injection on APPLY for SUDO_ASKPASS). Bash scripts use the canonical `mas/check.sh` template (13-arg `json_init`, single-arg `json_save_on_exit`, `error` message level, jq-only parsing). TimeMachineSnapshot implements ISnapshot; `list()` shells to `tmutil listlocalsnapshots /`; `create()` raises `SnapshotError` (APFS auto-managed). Capability flag flips to `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS`.

**Tech Stack:** Python 3.11+ (Pydantic v2), Bash 3.2+ (macOS system shell), `softwareupdate` (built-in macOS), `tmutil` (built-in macOS), `jq`, `sudo` via `MacElevation` from M5.2. Tests: pytest (mock-based unit + bash integration via fake binaries).

**Branch:** `claude/musing-herschel-b52e7e` (current worktree, continuing on top of v0.0.10-alpha). Push deferred to Task 13.

**Spec reference:** [docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md](../specs/2026-05-04-macos-softwareupdate-snapshot-design.md)

**Working directory:** `/Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e`. All commands assume this CWD.

---

## Critical lessons from M5.2 + M5.3 (DO NOT repeat)

When dispatching subagents for any bash-touching task:
- **`json_init` API**: 13 positional args, NO schema URI. Canonical: `json_init <phase> <category> <run_id> <trigger> <profile_name> <tool_name> <tool_version> <host_name> <host_os> <host_os_version> <host_arch> <host_user> <host_is_elevated>`. The plan code blocks below show the correct shape; trust the file on disk over any inline sample.
- **`json_save_on_exit`**: SINGLE arg — `json_save_on_exit "$OUTPUT_DIR"`. Captures `$?` internally.
- **`json_add_message` level**: `error` (4 letters), NOT `err`. Other valid: `info`, `warn`.
- **Helper return values**: any helper that returns a status string consumed by `json_add_item` MUST return only valid `ItemStatus` enum members (success, up_to_date, planned, partial, failed, skipped). Reviewers caught `failed-not-signed-in` in M5.2 Task 8.
- **JSON parsing tooling**: `jq` for ALL JSON parsing in bash scripts. NEVER inline `python3 - <<PYEOF`. Reviewers caught this in M5.2 Task 7.
- **Text parsing**: `softwareupdate -l` is text, not JSON. Use `sed`/`awk`. Bash 3.2-safe (no `[[`).
- **bash 3.2 only**: no `[[`, no `declare -A`, no `mapfile`, no `readarray`, no `<()` process substitution where `$()` works. Use `[ ... ]`, parallel space-separated strings + `awk -F'|'`.
- **Shell-string injection**: when interpolating into commands, use argv arrays (`cmd "$var"`) — never bash string concatenation into `eval` or `sh -c`. M5.2 Task 10 caught this in curl JSON bodies.
- **Error vs empty distinction**: never `command 2>/dev/null || true` if you need to distinguish "crash" from "no output". Capture `$?` separately. M5.2 Task 11 caught this in `mas outdated`.
- **Canonical bash templates**: `adapters/macos/scripts/mas/check.sh` (read-only template), `adapters/macos/scripts/mas/apply.sh` (mutating template with sudo -A pattern). Read these BEFORE writing any new bash script.
- **Plan-vs-reality drift**: trust live source code (Pydantic models, ABC contracts) over the plan's inline samples. Adapt assertions to actual model field names.
- **Pre-existing test adaptations**: legitimate when state evolves (e.g. asserting `inventory() is None` becomes `is not None` after Task 5 wires it). NEVER silent-delete assertions.
- **MasManager template note**: `SoftwareUpdateManager` mirrors MasManager exactly except for SCRIPT_BY_PHASE values, SOURCE_TYPE, display_name, and is_available probe. The IElevation injection pattern, _build_env(APPLY) SUDO_ASKPASS, _run_streaming, _resolve_bash all carry over verbatim.

---

## File structure

| New file | Responsibility | LOC |
|---|---|---|
| `adapters/macos/tests/fixtures/softwareupdate/no-updates.txt` | "No new software available" output | ~5 |
| `adapters/macos/tests/fixtures/softwareupdate/incremental-updates.txt` | Safari + XProtect, no restart | ~10 |
| `adapters/macos/tests/fixtures/softwareupdate/restart-required.txt` | macOS point release with `Action: restart` | ~10 |
| `adapters/macos/tests/fixtures/softwareupdate/README.md` | Format-drift docs | ~30 |
| `adapters/macos/scripts/softwareupdate/check.sh` | Bash: `softwareupdate -l` + parse + emit planned/up_to_date items | ~150 |
| `adapters/macos/scripts/softwareupdate/plan.sh` | Bash: like check.sh but planned items only | ~120 |
| `adapters/macos/scripts/softwareupdate/verify.sh` | Bash: re-run `softwareupdate -l`, mark each apply item success/failed | ~140 |
| `adapters/macos/scripts/softwareupdate/cleanup.sh` | Bash: no-op | ~80 |
| `adapters/macos/scripts/softwareupdate/apply.sh` | Bash: `sudo -A softwareupdate -ir -R --verbose` (default) or `-ia` with --all | ~180 |
| `adapters/macos/scripts/snapshot/list.sh` | Bash: `tmutil listlocalsnapshots /` + parse + emit | ~80 |
| `adapters/macos/ascendo_macos/managers/softwareupdate.py` | `SoftwareUpdateManager(IPackageManager)` mirroring MasManager | ~250 |
| `adapters/macos/ascendo_macos/snapshot.py` | `TimeMachineSnapshot(ISnapshot)` read-only | ~180 |
| `adapters/macos/tests/test_softwareupdate_check_script.py` | 6 fake-softwareupdate integration tests | ~250 |
| `adapters/macos/tests/test_softwareupdate_triplet.py` | 6 plan/verify/cleanup tests | ~280 |
| `adapters/macos/tests/test_apply_softwareupdate_script.py` | 6 fake-sudo + fake-softwareupdate tests | ~280 |
| `adapters/macos/tests/test_softwareupdate_manager_smoke.py` | 14 mock-based tests | ~280 |
| `adapters/macos/tests/test_snapshot_list_script.py` | 4 fake-tmutil tests | ~180 |
| `adapters/macos/tests/test_macos_snapshot_smoke.py` | 6 mock-based tests | ~220 |

| Modified file | Change |
|---|---|
| `core/ascendo/models/package.py` | +1 enum line: `SOFTWAREUPDATE = "softwareupdate"` |
| `docs/architecture/schemas/sidecar.v1.schema.json` | regenerated |
| `tests/contract/test_sidecar_v1.py` | +1 test |
| `adapters/macos/ascendo_macos/adapter.py` | capabilities + 3rd manager + snapshot() + 2 health helpers |
| `adapters/macos/tests/test_adapter_smoke.py` | +4 wiring tests; pre-existing M5.3-era tests adapted |
| `bin/validate-macos.sh` | Stage 10 + Stage 11 appended |
| `bin/run-tag-release-macos.sh` | Tag v0.0.10-alpha → v0.0.11-alpha + tag message |
| `HANDOFF.md` | Sesja 26 entry prepended |
| `PLAN.md` | M5.4 row → ✅ done |

---

## Task 1: Add `SourceType.SOFTWAREUPDATE` enum + regenerate schema

**Files:**
- Modify: `core/ascendo/models/package.py`
- Modify: `docs/architecture/schemas/sidecar.v1.schema.json`
- Test: `tests/contract/test_sidecar_v1.py`

- [ ] **Step 1: Append failing test**

```python
def test_source_type_has_softwareupdate_value() -> None:
    """SoftwareUpdateManager.category == SourceType.SOFTWAREUPDATE. Required by M5.4."""
    from ascendo.models.package import SourceType
    assert SourceType.SOFTWAREUPDATE.value == "softwareupdate"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_softwareupdate_value -v
```

Expected: `FAILED` with `AttributeError: SOFTWAREUPDATE`.

- [ ] **Step 3: Add the enum value**

In `core/ascendo/models/package.py`, locate `SourceType` class. Insert after `INVENTORY = "inventory"`:

```python
    INVENTORY = "inventory"  # macOS LaunchServices inventory category (M5.3)
    SOFTWAREUPDATE = "softwareupdate"  # macOS softwareupdate CLI (M5.4 OS patches)
    PLUGIN = "plugin"
```

- [ ] **Step 4: Re-run test, expect PASS + regenerate schema**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_softwareupdate_value -v
PYTHONPATH=$(pwd)/core python3 scripts/export-sidecar-schema.py
```

- [ ] **Step 5: Run full contract suite**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/ -q
```

- [ ] **Step 6: Commit**

```bash
git add core/ascendo/models/package.py
git add docs/architecture/schemas/sidecar.v1.schema.json
git add tests/contract/test_sidecar_v1.py
git commit -m "$(cat <<'EOF'
feat(core): add SourceType.SOFTWAREUPDATE for macOS adapter (M5.4.1)

The macOS softwareupdate manager needs a first-class enum value for
OS-update items. Mirrors how M5.2 added SourceType.MAS and M5.3 added
SourceType.SYSTEM + SourceType.INVENTORY.

Sidecar JSON Schema regenerated.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §1
EOF
)"
```

---

## Task 2: softwareupdate test fixtures

Three text fixtures sourced from real `softwareupdate -l` output, plus a README documenting format-drift risk.

**Files:**
- Create: `adapters/macos/tests/fixtures/softwareupdate/no-updates.txt`
- Create: `adapters/macos/tests/fixtures/softwareupdate/incremental-updates.txt`
- Create: `adapters/macos/tests/fixtures/softwareupdate/restart-required.txt`
- Create: `adapters/macos/tests/fixtures/softwareupdate/README.md`

- [ ] **Step 1: Write `no-updates.txt`**

```
Software Update Tool

Finding available software
No new software available.
```

- [ ] **Step 2: Write `incremental-updates.txt`**

```
Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: Safari17.4-17.4
	Title: Safari, Version: 17.4, Size: 87651K, Recommended: YES,
* Label: XProtectPlistConfigData_10_15-2174
	Title: XProtectPlistConfigData, Version: 2174, Size: 50K, Recommended: YES,
```

(The whitespace before `Title:` is a literal TAB, not spaces.)

- [ ] **Step 3: Write `restart-required.txt`**

```
Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: macOS Sonoma 14.7.1-23H311
	Title: macOS Sonoma 14.7.1, Version: 14.7.1, Size: 5.2G, Recommended: YES, Action: restart,
* Label: Safari17.5-17.5
	Title: Safari, Version: 17.5, Size: 87651K, Recommended: YES,
```

- [ ] **Step 4: Write `README.md`**

```markdown
# softwareupdate -l fixtures

Real `softwareupdate -l` output captured from macOS 14.x.

## Format-drift risk

Apple does not document the `softwareupdate -l` text format as stable. Major
macOS releases historically tweaked spacing, key names, and ordering.
Re-capture fixtures and update the parser when:

- Tests start failing on a fresh CI Mac after a macOS upgrade.
- The script's check phase emits items missing a `Title` or `Version` field.
- New `Action: <value>` entries appear (currently we recognize only `restart`).

## Capture command

```bash
softwareupdate -l > /tmp/sample.txt 2>&1
```

Then trim the leading `Software Update Tool` banner if your captured shell
emits extra noise. Whitespace before `Title:` lines is a literal TAB.
```

- [ ] **Step 5: Verify**

```bash
ls adapters/macos/tests/fixtures/softwareupdate/
# Expect: README.md, incremental-updates.txt, no-updates.txt, restart-required.txt
```

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/tests/fixtures/softwareupdate/
git commit -m "$(cat <<'EOF'
test(macos): add softwareupdate -l fixtures (M5.4.2)

Three text fixtures + README:
  - no-updates.txt: "No new software available."
  - incremental-updates.txt: Safari + XProtect, no restart
  - restart-required.txt: macOS point release with Action: restart

Sourced from real softwareupdate -l output on macOS 14.x. README
documents format-drift risk + the canonical capture command.
EOF
)"
```

---

## Task 3: `scripts/softwareupdate/check.sh` + 6 integration tests

**Files:**
- Create: `adapters/macos/scripts/softwareupdate/check.sh` (~150 LOC)
- Create: `adapters/macos/tests/test_softwareupdate_check_script.py` (~250 LOC)

### Step 1: Write the test file

Create `adapters/macos/tests/test_softwareupdate_check_script.py` mirroring
`adapters/macos/tests/test_check_mas_script.py` (read it first for the harness pattern).

```python
"""Tests for adapters/macos/scripts/softwareupdate/check.sh.

Six integration tests using a fake softwareupdate binary fed canned fixtures.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "softwareupdate" / "check.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_su(tmp_path: Path, *, fixture_name: str) -> Path:
    """Fake softwareupdate binary returning the canned fixture for `-l`."""
    fixture = (FIX / fixture_name).read_text()
    p = tmp_path / "fake_softwareupdate"
    body = (
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
        "    echo 'softwareupdate test-fake'\n"
        "    exit 0\n"
        "fi\n"
        "if [ \"$1\" = '-l' ] || [ \"$1\" = '--list' ]; then\n"
        f"    cat <<'EOF_SU'\n{fixture}\nEOF_SU\n"
        "    exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run(script: Path, su: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["SU_BIN"] = str(su)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_no_updates_emits_zero_items_status_success(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert sc.status.value == "success"
    assert sc.items == []


def test_incremental_updates_emits_two_planned_items(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert len(sc.items) == 2
    assert {i.id for i in sc.items} == {
        "Safari17.4-17.4",
        "XProtectPlistConfigData_10_15-2174",
    }
    for item in sc.items:
        assert item.status.value == "planned"
        assert item.source.type.value == "softwareupdate"


def test_restart_required_marks_needs_reboot(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="restart-required.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert len(sc.items) == 2
    assert sc.summary.needs_reboot is True
    macos_item = next(i for i in sc.items if i.id.startswith("macOS Sonoma"))
    assert macos_item.current_version == "14.7.1"


def test_per_item_metadata_safari(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "check__softwareupdate.json")
    safari = next(i for i in sc.items if "Safari" in i.id)
    assert safari.current_version == "17.4"
    assert safari.target_version == "17.4"
    assert safari.source.type.value == "softwareupdate"


def test_softwareupdate_failure_exits_30(tmp_path):
    su = tmp_path / "broken_su"
    su.write_text("#!/usr/bin/env bash\necho 'broken' >&2\nexit 1\n")
    os.chmod(su, 0o755)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, su, out, rid)
    assert res.returncode == 30
    sc = _parse(out / rid / "check__softwareupdate.json")
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)


def test_required_args_validation(tmp_path):
    su = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    env = dict(os.environ)
    env["SU_BIN"] = str(su)
    res = subprocess.run(
        ["bash", str(SCRIPT), "--run-id", "x"],  # missing other required args
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 2
```

### Step 2: Run tests, expect 6 FAILED (script doesn't exist)

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_softwareupdate_check_script.py -v
```

### Step 3: Read canonical templates

```bash
cat adapters/macos/scripts/mas/check.sh
cat adapters/macos/lib/ascendo_json.sh | head -200
```

### Step 4: Write `scripts/softwareupdate/check.sh`

Mirror mas/check.sh structure exactly. Header, set -o pipefail, SCRIPT_DIR resolution, lib sourcing (`ascendo_json.sh` only — no separate softwareupdate lib needed for MVP), arg parsing with required-arg validation, host info block, tool info, json_init, EXIT trap installed AFTER.

Phase-specific section after init:

```bash
SU_BIN="${SU_BIN:-/usr/sbin/softwareupdate}"

# -- run softwareupdate -l ----------------------------------------------------
SU_RC=0
SU_OUT="$("$SU_BIN" -l 2>&1)" || SU_RC=$?
if [ "$SU_RC" -ne 0 ]; then
    json_add_message "error" "softwareupdate -l failed (exit $SU_RC): $SU_OUT"
    json_add_item "softwareupdate:list-error" "" "" "failed" "softwareupdate"
    exit 30
fi

# -- empty case ---------------------------------------------------------------
if printf '%s\n' "$SU_OUT" | grep -q "No new software available\."; then
    # Zero items, success status, status heuristic in lib emits success
    exit 0
fi

# -- parse `* Label:` blocks --------------------------------------------------
# State machine: when we see "* Label: <X>", remember X. The next non-blank
# line starting with TAB carries the Title/Version/Action attributes.
NEEDS_REBOOT=0
CURRENT_LABEL=""
printf '%s\n' "$SU_OUT" | while IFS= read -r line; do
    case "$line" in
        '* Label: '*)
            CURRENT_LABEL="${line#* Label: }"
            ;;
        $'\t'*)
            [ -n "$CURRENT_LABEL" ] || continue
            # Strip leading TAB(s)
            attrs="${line#$'\t'}"
            # Extract Version
            version="$(printf '%s\n' "$attrs" | sed -n 's/.*Version: \([^,]*\),.*/\1/p' | head -1)"
            version="${version# }"   # trim leading space
            version="${version% }"   # trim trailing space
            # Detect Action: restart
            if printf '%s\n' "$attrs" | grep -q "Action: restart"; then
                NEEDS_REBOOT=1
            fi
            json_add_item "$CURRENT_LABEL" "$version" "$version" "planned" "softwareupdate"
            CURRENT_LABEL=""
            ;;
    esac
done

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    json_set_needs_reboot true
fi

exit 0
```

The `while`-pipe loop runs in a subshell so `NEEDS_REBOOT` mutation doesn't survive. **Workaround**: write `NEEDS_REBOOT` state to a temp file and re-read after the loop. See `mas/apply.sh` for the canonical Bash 3.2 parallel-array workaround pattern.

Alternative cleaner pattern: emit the items in the loop AND write needs_reboot to a temp file; after the loop reads, call `json_set_needs_reboot` based on the file contents.

### Step 5: chmod + run tests

```bash
chmod +x adapters/macos/scripts/softwareupdate/check.sh
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_softwareupdate_check_script.py -v
```

### Step 6: Run full macOS suite

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
```

Expect: previous count + 6.

### Step 7: Commit

```bash
git add adapters/macos/scripts/softwareupdate/check.sh
git add adapters/macos/tests/test_softwareupdate_check_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/softwareupdate/check.sh — read-only OS-update list (M5.4.3)

Read-only inventory of pending macOS OS updates via `softwareupdate -l`.
Parses the text output's `* Label:` + `Title:`/`Version:`/`Action:`
attribute lines via sed/awk. Emits one ascendo/v1 sidecar at
<output-dir>/<run-id>/check__softwareupdate.json on every code path
via EXIT trap.

Detects `Action: restart` and sets sidecar needs_reboot flag.
softwareupdate failure -> exit 30, sidecar.status=failed.

6 fake-softwareupdate integration tests: no-updates, incremental,
restart-required, per-item metadata, failure path, required-arg
validation.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §§4-5
EOF
)"
```

---

## Task 4: `scripts/softwareupdate/{plan,verify,cleanup}.sh` triplet + 6 tests

`plan.sh` = check.sh minus emission of zero-item case (exit 0 silently);
`verify.sh` = re-run softwareupdate -l, mark each apply item success/failed;
`cleanup.sh` = no-op.

**Files:**
- Create: `adapters/macos/scripts/softwareupdate/plan.sh`
- Create: `adapters/macos/scripts/softwareupdate/verify.sh`
- Create: `adapters/macos/scripts/softwareupdate/cleanup.sh`
- Create: `adapters/macos/tests/test_softwareupdate_triplet.py`

### Step 1: Write the test file

Create `adapters/macos/tests/test_softwareupdate_triplet.py` mirroring
`adapters/macos/tests/test_mas_triplet.py` (read it first for the apply-sidecar synthesis pattern).

Six tests:

```python
def test_plan_emits_only_planned_items(tmp_path):
    """plan.sh emits planned items, no up_to_date items."""
    # Use incremental-updates.txt fixture → expect 2 planned items
    ...

def test_plan_signed_out_or_failure_emits_failed_item(tmp_path):
    """When softwareupdate -l fails, plan emits a failed item."""
    # Use a broken_su that exits 1 → expect status=failed
    ...

def test_verify_reads_sibling_apply_sidecar(tmp_path):
    """verify.sh reads apply__softwareupdate.json and marks each apply success
    item as verify success when it's no longer in `softwareupdate -l`."""
    # Synthesise apply sidecar with 1 success item, fake_su returns no-updates,
    # expect verify success
    ...

def test_verify_softnoop_when_no_apply_sidecar(tmp_path):
    """verify can run after check-only; no apply sidecar -> success, zero items."""
    ...

def test_cleanup_emits_success_zero_items(tmp_path):
    ...

def test_cleanup_dry_run_is_identical(tmp_path):
    ...
```

(Full test bodies mirror `test_mas_triplet.py` closely; adapt for the
softwareupdate fixtures + sidecar filenames.)

### Step 2: Run tests, expect 6 FAILED

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_softwareupdate_triplet.py -v
```

### Step 3: Read canonical templates

```bash
cat adapters/macos/scripts/mas/plan.sh
cat adapters/macos/scripts/mas/verify.sh
cat adapters/macos/scripts/mas/cleanup.sh
```

### Step 4: Write plan.sh

Like check.sh but:
- Skip the "No new software available" zero-emission case (exit 0 silently with
  zero items, status=success — same as check)
- All emitted items are `planned` (no up_to_date items)

Effectively plan.sh and check.sh are the same for OS updates because softwareupdate
has no separate "installed but up-to-date" surface. Plan is just check renamed.

The simplest implementation: copy check.sh, change `json_init` phase from `check`
to `plan`, change EXIT trap output filename via OUTPUT_DIR (the trap uses bufdir;
the phase argument to json_init drives the filename via the lib helper).

Verify by inspection: the json_init `<phase>` arg drives the sidecar filename
produced by `json_save_on_exit`. Confirm against `lib/ascendo_json.sh` source.

### Step 5: Write verify.sh

Like mas/verify.sh: read sibling `apply__softwareupdate.json` via jq, extract
success-item IDs, re-run `softwareupdate -l`, for each success item check whether
the same Label still appears in the new output. If yes → verify failed (the apply
didn't take). If no → verify success.

```bash
APPLY_SIDECAR="$OUTPUT_DIR/$RUN_ID/apply__softwareupdate.json"
if [ ! -f "$APPLY_SIDECAR" ]; then
    json_add_message "info" "No sibling apply__softwareupdate.json sidecar; verify is a soft no-op."
    exit 0
fi

# Extract success-item IDs from apply sidecar
APPLY_SUCCESS_IDS="$(jq -r '.items[]? | select(.status=="success") | .id' "$APPLY_SIDECAR" 2>/dev/null)"

# Re-run softwareupdate -l
SU_OUT="$("$SU_BIN" -l 2>&1)" || true

# For each apply success ID, check if it's still in the SU output
for _id in $APPLY_SUCCESS_IDS; do
    if printf '%s\n' "$SU_OUT" | grep -qF "* Label: $_id"; then
        json_add_item "$_id" "" "" "failed" "softwareupdate"
    else
        json_add_item "$_id" "" "" "success" "softwareupdate"
    fi
done
exit 0
```

### Step 6: Write cleanup.sh

No-op. Mirror `mas/cleanup.sh` exactly with category="softwareupdate" and tool name
swapped.

### Step 7: chmod + run tests

```bash
chmod +x adapters/macos/scripts/softwareupdate/{plan,verify,cleanup}.sh
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_softwareupdate_triplet.py -v
```

### Step 8: Run full suite

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
```

### Step 9: Commit

```bash
git add adapters/macos/scripts/softwareupdate/{plan,verify,cleanup}.sh
git add adapters/macos/tests/test_softwareupdate_triplet.py
git commit -m "$(cat <<'EOF'
feat(macos): plan/verify/cleanup softwareupdate scripts (M5.4.4)

Read-only triplet completing the softwareupdate 5-phase contract:
  plan.sh    — equivalent to check.sh (no separate up_to_date surface).
  verify.sh  — reads sibling apply__softwareupdate.json, re-runs
               `softwareupdate -l`, marks each apply success item
               success (label gone) or failed (label still present).
  cleanup.sh — no-op.

All Bash 3.2-safe, mirror mas/check.sh template.

6 fake-softwareupdate integration tests.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §4
EOF
)"
```

---

## Task 5: `scripts/softwareupdate/apply.sh` + 6 tests

The mutating phase. Always invokes `sudo -A softwareupdate -ir -R --verbose` (recommended-only default) or `-ia` with --all.

**Files:**
- Create: `adapters/macos/scripts/softwareupdate/apply.sh` (~180 LOC)
- Create: `adapters/macos/tests/test_apply_softwareupdate_script.py` (~280 LOC)

### Step 1: Write the test file

Mirror `test_apply_mas_script.py` exactly. Six tests:

```python
def test_dry_run_emits_planned_items_no_sudo(tmp_path):
    """--dry-run: enumerate updates as planned, NEVER invoke sudo."""

def test_real_apply_invokes_sudo_a_softwareupdate(tmp_path):
    """Default invocation: `sudo -A softwareupdate -ir -R --verbose`."""
    # fake_sudo logs "$@" — assert log line starts with "-A " and contains "-ir" + "-R"

def test_all_flag_invokes_dash_a_not_dash_r(tmp_path):
    """--all → `sudo -A softwareupdate -ia -R --verbose` (note -i*a* not -i*r*)."""

def test_filter_passes_label_to_softwareupdate(tmp_path):
    """--filter 'Safari17.4-17.4' → `sudo -A softwareupdate -i 'Safari17.4-17.4' -R`."""

def test_no_updates_exit_0_no_sudo(tmp_path):
    """When `softwareupdate -l` returns empty, exit 0, no sudo invocation."""

def test_softwareupdate_l_failure_exit_30_no_sudo(tmp_path):
    """When `softwareupdate -l` crashes, abort before sudo invocation."""
```

### Step 2: Run tests, expect 6 FAILED

### Step 3: Read canonical template

```bash
cat adapters/macos/scripts/mas/apply.sh
```

Note the `_sudo_mas_upgrade()` helper centralizing the `sudo -A` pattern. M5.4
mirrors with `_sudo_softwareupdate()`.

### Step 4: Write apply.sh

Mirror mas/apply.sh structure. Key differences:

- New flag: `--all` (default OFF). When set, pass `-a` (all) instead of `-r` (recommended).
- New flag: `--filter <label>` (single label, NOT comma-separated like mas — softwareupdate
  takes one label per `-i` arg; multiple `-i Label1 -i Label2` works but for MVP
  support single-label filter and document in script header).
- Dry-run path: re-uses check.sh's parser to emit planned items, no sudo.
- Real apply path: build sudo argv, invoke via `_sudo_softwareupdate()`.

```bash
_sudo_softwareupdate() {
    sudo -A "$SU_BIN" "$@" 2>&1
}

# After sign-in / --dry-run gates...

# Build sudo argv
SU_ARGV="-i"   # install
if [ "$ALL" -eq 1 ]; then
    SU_ARGV="$SU_ARGV -a"   # all (including non-recommended)
else
    SU_ARGV="$SU_ARGV -r"   # recommended only (default)
fi
SU_ARGV="$SU_ARGV -R --verbose"   # restart-on-success + verbose

if [ -n "$FILTER_LABEL" ]; then
    # Single-label filter: replace -ir/-ia with -i <label>
    SU_ARGV="-i $FILTER_LABEL -R --verbose"
fi

# Pre-emit success sidecar BEFORE sudo invocation (sidecar survives reboot)
for _id in $TARGET_IDS; do
    json_add_item "$_id" "" "" "success" "softwareupdate"
done

# Invoke sudo
RC=0
_sudo_softwareupdate $SU_ARGV | tee -a "$OUTPUT_DIR/$RUN_ID/apply__softwareupdate.log" || RC=$?

if [ "$NEEDS_REBOOT" -eq 1 ] && [ "$RC" -eq 0 ]; then
    json_set_needs_reboot true
    exit 75   # NEEDS_REBOOT exit code per docs/agents/contract.md
fi

if [ "$RC" -ne 0 ]; then
    json_add_message "error" "softwareupdate exited $RC"
    # Update items to failed status — the success items pre-emitted above need fixing
    # SIMPLIFICATION: rely on bash 3.2 not being able to mutate previously-emitted
    # items. Instead, the orchestrator + verify phase catches the discrepancy.
    # For M5.4 MVP: pre-emit success only when DRY_RUN=0 AND we expect success;
    # re-run via verify gives the operator the actual post-apply state.
    exit 20   # apply-fail-known
fi

exit 0
```

(The "pre-emit success sidecar BEFORE sudo invocation" logic is the spec's
"reboot-survival" requirement. The cost: if sudo fails, the sidecar still
shows success items. Verify phase catches it. Document this trade-off in
the script header comment.)

### Step 5: chmod + run tests

```bash
chmod +x adapters/macos/scripts/softwareupdate/apply.sh
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_apply_softwareupdate_script.py -v
```

### Step 6: Run full suite + commit

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
git add adapters/macos/scripts/softwareupdate/apply.sh
git add adapters/macos/tests/test_apply_softwareupdate_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/softwareupdate/apply.sh — first mutating phase (M5.4.5)

The mutating softwareupdate phase. Always invokes `sudo -A softwareupdate
-ir -R --verbose` (recommended-only default). The -R flag is mandatory:
sets boot metadata that triggers the update on restart. Without -R,
updates download but never apply (battle-tested wisdom from legacy
update_system.sh).

Pattern:
  --dry-run       enumerate updates as planned, NO sudo invocation
  default         sudo -A softwareupdate -ir -R --verbose
  --all           sudo -A softwareupdate -ia -R --verbose
                  (includes non-recommended, e.g. major-version offers)
  --filter LABEL  sudo -A softwareupdate -i <LABEL> -R --verbose
                  (per-label apply, single label only for MVP)

Reboot-survival: success sidecar emitted BEFORE sudo invocation so it
persists across the mid-run reboot. Verify phase reconciles by re-running
softwareupdate -l. Apply exit code 75 on needs-reboot, 20 on apply-fail-known.

6 fake-sudo + fake-softwareupdate integration tests.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §4
EOF
)"
```

---

## Task 6: `SoftwareUpdateManager` Python adapter + 14 mock-based tests

Mirrors MasManager structure. Takes `MacElevation` for SUDO_ASKPASS injection on Phase.APPLY.

**Files:**
- Create: `adapters/macos/ascendo_macos/managers/softwareupdate.py` (~250 LOC)
- Create: `adapters/macos/tests/test_softwareupdate_manager_smoke.py` (~280 LOC)

### Step 1: Read canonical template

```bash
cat adapters/macos/ascendo_macos/managers/mas.py
```

Note the public surface to mirror exactly:
- `class SoftwareUpdateManager(IPackageManager)`
- `SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]]` mapping all 5 phases to softwareupdate/<phase>.sh
- `__init__(scripts_dir, lib_dir, *, elevation: MacElevation, bash_path=None, timeout_sec=...)`
- `category` property → `SourceType.SOFTWAREUPDATE`
- `display_name` → `"macOS softwareupdate (OS patches)"`
- `is_available(host)` → True if macOS host AND `softwareupdate` on PATH (built-in macOS — should always be true)
- `_build_env(phase)` → APPLY-only SUDO_ASKPASS injection (mirror MasManager exactly)
- `_last_env_for_test` test seam
- `run_phase`, `_build_argv`, `_run_streaming(env=)`, `_missing_sidecar_error`, `_resolve_bash` — all mirror MasManager

### Step 2: Write 14 mock-based tests

Mirror `test_mas_manager_smoke.py` (study it first). Tests:
1. `test_identity_and_paths`
2. `test_is_available_on_linux_returns_false`
3. `test_is_available_on_windows_returns_false`
4. `test_is_available_on_macos_with_softwareupdate_returns_true`
5. `test_is_available_on_macos_without_softwareupdate_returns_false`
6-10. `test_run_phase_dispatches_correct_script[check|plan|apply|verify|cleanup]` (parametrized)
11. `test_apply_exports_sudo_askpass_when_password_registered`
12. `test_apply_does_not_export_sudo_askpass_when_no_password`
13. `test_apply_does_not_export_sudo_askpass_when_helper_path_is_none`
14. `test_run_phase_raises_managererror_on_missing_sidecar`
15. (parametrized) `test_non_apply_phase_does_not_export_sudo_askpass_even_when_password_registered`

### Step 3: Run tests, expect 14 FAILED

### Step 4: Write `SoftwareUpdateManager`

Mirror `MasManager` byte-for-byte where possible. Differences:
- Class name + docstring + module
- SCRIPT_BY_PHASE values (`softwareupdate/<phase>.sh`)
- SOURCE_TYPE = SourceType.SOFTWAREUPDATE
- display_name
- is_available probes `softwareupdate --help` (not `mas version`); no minimum version check (softwareupdate ships with macOS, version-bound to OS release)
- No `mas_signed_in` equivalent (softwareupdate doesn't have a sign-in concept — it just queries Apple's update server)

### Step 5: Run tests + commit

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_softwareupdate_manager_smoke.py -v
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
git add adapters/macos/ascendo_macos/managers/softwareupdate.py
git add adapters/macos/tests/test_softwareupdate_manager_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): SoftwareUpdateManager Python adapter (M5.4.6)

Mirrors MasManager structure exactly. Implements IPackageManager
5-phase contract dispatching to scripts/softwareupdate/{phase}.sh.

Takes MacElevation for SUDO_ASKPASS injection on Phase.APPLY only
(non-apply phases inherit parent env). Uniform sudo treatment matches
mas pattern (M5.2 CVE-2025-43411 precedent — even though softwareupdate
has no documented equivalent CVE).

is_available probes softwareupdate --help (no min-version check;
softwareupdate ships with macOS, version-bound to OS release).

14 mock-based tests cover identity, OS gate, tool availability,
5-phase argv dispatch, SUDO_ASKPASS conditional injection (including
the helper-path=None edge case), error path, non-APPLY phases not
injecting.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §2
EOF
)"
```

---

## Task 7: `scripts/snapshot/list.sh` + 4 tests

Bash script for `tmutil listlocalsnapshots /` enumeration.

**Files:**
- Create: `adapters/macos/scripts/snapshot/list.sh` (~80 LOC)
- Create: `adapters/macos/tests/test_snapshot_list_script.py` (~180 LOC)

### Step 1: Write the test file

```python
"""Tests for adapters/macos/scripts/snapshot/list.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "snapshot" / "list.sh"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_tmutil(tmp_path: Path, *, snapshots: list[str]) -> Path:
    """Fake tmutil binary returning the given snapshot list."""
    p = tmp_path / "fake_tmutil"
    body_lines = ["Snapshots for disk /:"] + snapshots
    body = (
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'listlocalsnapshots' ]; then\n"
        f"    cat <<'EOF_TM'\n" + "\n".join(body_lines) + "\nEOF_TM\n"
        "    exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run(script: Path, tm: Path, output_dir: Path, run_id: str):
    env = dict(os.environ)
    env["TMUTIL_BIN"] = str(tm)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir)],
        capture_output=True, text=True, env=env, check=False,
    )


def test_list_parses_snapshot_timestamps(tmp_path):
    snapshots = [
        "com.apple.TimeMachine.2026-05-03-140425.local",
        "com.apple.TimeMachine.2026-05-04-001704.local",
    ]
    tm = _make_fake_tmutil(tmp_path, snapshots=snapshots)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, tm, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__snapshot.json")
    assert len(sc.items) == 2
    ids = {i.id for i in sc.items}
    assert ids == set(snapshots)


def test_empty_list_returns_zero_items(tmp_path):
    tm = _make_fake_tmutil(tmp_path, snapshots=[])
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, tm, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "check__snapshot.json")
    assert sc.items == []
    assert sc.status.value == "success"


def test_malformed_snapshot_name_skipped(tmp_path):
    snapshots = [
        "com.apple.TimeMachine.2026-05-03-140425.local",
        "garbage-not-a-snapshot",
        "com.apple.TimeMachine.2026-05-04-001704.local",
    ]
    tm = _make_fake_tmutil(tmp_path, snapshots=snapshots)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, tm, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "check__snapshot.json")
    assert len(sc.items) == 2  # garbage skipped


def test_tmutil_failure_exits_30(tmp_path):
    tm = tmp_path / "broken_tm"
    tm.write_text("#!/usr/bin/env bash\nexit 1\n")
    os.chmod(tm, 0o755)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, tm, out, rid)
    assert res.returncode == 30
    sc = _parse(out / rid / "check__snapshot.json")
    assert sc.status.value == "failed"
```

### Step 2: Run tests, expect 4 FAILED

### Step 3: Write list.sh

Mirror `mas/check.sh` skeleton. Phase-specific logic:

```bash
TMUTIL_BIN="${TMUTIL_BIN:-/usr/bin/tmutil}"

TM_RC=0
TM_OUT="$("$TMUTIL_BIN" listlocalsnapshots / 2>&1)" || TM_RC=$?
if [ "$TM_RC" -ne 0 ]; then
    json_add_message "error" "tmutil listlocalsnapshots / failed (exit $TM_RC): $TM_OUT"
    json_add_item "snapshot:tmutil-error" "" "" "failed" "system"
    exit 30
fi

# Skip the "Snapshots for disk /:" header
printf '%s\n' "$TM_OUT" | tail -n +2 | while IFS= read -r snap; do
    [ -n "$snap" ] || continue
    # Only accept lines matching com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.local
    case "$snap" in
        com.apple.TimeMachine.*-*-*-*.local)
            json_add_item "$snap" "" "" "success" "system"
            ;;
        *)
            # Skip malformed lines silently
            ;;
    esac
done
exit 0
```

Note: `category="snapshot"` in json_init (uses `SourceType.SYSTEM` from M5.3 for source.type since snapshots aren't packages). Actually that's awkward — the sidecar category and item source.type don't have to match. For MVP, set both to "system" since the SYSTEM enum value makes more semantic sense than introducing a SNAPSHOT enum. Document the choice.

Wait — `category` field in sidecar uses `SourceType` enum. We need an enum value. Either:
(a) Reuse `SourceType.SYSTEM` (no new enum value, semantically iffy)
(b) Add `SourceType.SNAPSHOT = "snapshot"` (clean but bumps enum)

Decision: option (b). Add to Task 1 as a 2nd enum addition. Update Task 1's commit message to mention both.

**Update Task 1 to also add `SOFTWAREUPDATE` AND `SNAPSHOT` enum values.** Both regenerated into the schema.

### Step 4: chmod + run tests + commit

```bash
chmod +x adapters/macos/scripts/snapshot/list.sh
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_snapshot_list_script.py -v
git add adapters/macos/scripts/snapshot/list.sh
git add adapters/macos/tests/test_snapshot_list_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/snapshot/list.sh — Time Machine local snapshots (M5.4.7)

Read-only enumeration of APFS local snapshots via
`tmutil listlocalsnapshots /`. Parses `com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.local`
names + emits one Item per snapshot (status=success, source.type=snapshot).

Skips malformed lines silently. Empty list → zero items, success.
tmutil failure → exit 30, sidecar.status=failed.

4 fake-tmutil integration tests.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §6
EOF
)"
```

---

## Task 8: `TimeMachineSnapshot` Python wrapper + 6 mock-based tests

**Files:**
- Create: `adapters/macos/ascendo_macos/snapshot.py` (~180 LOC)
- Create: `adapters/macos/tests/test_macos_snapshot_smoke.py` (~220 LOC)

### Step 1: Write the test file

```python
"""Mock-based smoke tests for TimeMachineSnapshot."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
sys.path.insert(0, str(ADAPTER_ROOT))

from ascendo.interfaces.snapshot import SnapshotError
from ascendo.models.host import HostInfo, OperatingSystem, ElevationMethod
from ascendo_macos.snapshot import TimeMachineSnapshot


SCRIPTS_DIR = ADAPTER_ROOT / "scripts"
LIB_DIR = ADAPTER_ROOT / "lib"


def _mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


def test_backend_slug_is_time_machine():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    assert snap.backend == "time_machine"


def test_is_available_returns_true_when_tmutil_on_path():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("shutil.which", return_value="/usr/bin/tmutil"):
        assert snap.is_available(_mac_host()) is True


def test_is_available_returns_false_when_tmutil_missing():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("shutil.which", return_value=None):
        assert snap.is_available(_mac_host()) is False


def test_list_returns_snapshotinfo_with_parsed_timestamp(tmp_path):
    items = [
        {"id": "com.apple.TimeMachine.2026-05-03-140425.local",
         "current_version": "", "target_version": "", "resolved_version": "",
         "status": "success",
         "source": {"type": "snapshot", "feed": ""}},
    ]
    sidecar_text = _minimal_sidecar(items)  # helper similar to MacOSInventory's

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__snapshot.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        snapshots = snap.list(_mac_host())
    assert len(snapshots) == 1
    assert snapshots[0].id == "com.apple.TimeMachine.2026-05-03-140425.local"
    assert snapshots[0].backend == "time_machine"
    # Timestamp parsed from the snapshot ID
    assert snapshots[0].created_at == datetime(2026, 5, 3, 14, 4, 25, tzinfo=timezone.utc)


def test_create_raises_snapshot_error_with_apfs_explainer():
    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with pytest.raises(SnapshotError) as excinfo:
        snap.create(_mac_host(), label="test")
    assert "auto-managed" in str(excinfo.value).lower() or "APFS" in str(excinfo.value)


def test_get_filters_by_id(tmp_path):
    items = [
        {"id": "com.apple.TimeMachine.2026-05-03-140425.local",
         "current_version": "", "target_version": "", "resolved_version": "",
         "status": "success",
         "source": {"type": "snapshot", "feed": ""}},
        {"id": "com.apple.TimeMachine.2026-05-04-001704.local",
         "current_version": "", "target_version": "", "resolved_version": "",
         "status": "success",
         "source": {"type": "snapshot", "feed": ""}},
    ]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__snapshot.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    snap = TimeMachineSnapshot(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        result = snap.get(_mac_host(), "com.apple.TimeMachine.2026-05-04-001704.local")
    assert result is not None
    assert result.id == "com.apple.TimeMachine.2026-05-04-001704.local"
```

(Include `_minimal_sidecar` helper similar to `test_macos_inventory_smoke.py`.)

### Step 2: Run tests, expect 6 FAILED

### Step 3: Write `TimeMachineSnapshot`

Mirror `MacOSInventory` structure (per Task 4 of M5.3). Differences:
- `class TimeMachineSnapshot(ISnapshot)`
- `SCRIPT_REL = "snapshot/list.sh"`
- `backend` property → `"time_machine"`
- `is_available(host)` → `host.os == OperatingSystem.MACOS and shutil.which("tmutil") is not None`
- `list(host)` → spawn list.sh, parse sidecar, convert each `Item` to `SnapshotInfo` (extract timestamp from ID via regex `r"com\.apple\.TimeMachine\.(\d{4}-\d{2}-\d{2})-(\d{6})\.local"`)
- `get(host, snapshot_id)` → call list(), filter
- `create(host, *, label, notes=None)` → raise `SnapshotError("macOS local snapshots are auto-managed by APFS. To configure backups: System Settings > General > Time Machine. Ascendo cannot create local snapshots on demand.")`

### Step 4: Run tests + commit

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_macos_snapshot_smoke.py -v
git add adapters/macos/ascendo_macos/snapshot.py
git add adapters/macos/tests/test_macos_snapshot_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): TimeMachineSnapshot read-only ISnapshot impl (M5.4.8)

Mirrors MacOSInventory pattern. Spawns scripts/snapshot/list.sh,
parses ascendo/v1 sidecar, converts Items to SnapshotInfo with
timestamp parsed from `com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.local`
naming convention.

is_available(): macOS host + tmutil on PATH.
list(): returns local APFS snapshots newest-first.
get(snapshot_id): filters list().
create(): raises SnapshotError with APFS auto-management explainer
  (operators configure backups via System Settings > Time Machine,
  Ascendo cannot create local snapshots on demand).

`tmutil latestbackup` intentionally NOT used (TCC permissions
required, opaque failure mode).

6 mock-based tests: backend slug, is_available true/false, list
parses timestamps, get filters by id, create raises SnapshotError.

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §6
EOF
)"
```

---

## Task 9: `MacOSAdapter` wire-up — capability flip + 3rd manager + snapshot + 2 health helpers

**Files:**
- Modify: `adapters/macos/ascendo_macos/adapter.py`
- Modify: `adapters/macos/tests/test_adapter_smoke.py`

### Step 1: Append failing tests

```python
def test_capabilities_includes_snapshots():
    """M5.4: SNAPSHOTS flag added."""
    from ascendo.interfaces import AdapterCapability
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    assert AdapterCapability.SNAPSHOTS in a.capabilities


def test_package_managers_includes_softwareupdate(mac_host):
    """M5.4: package_managers returns [Brew, Mas, SoftwareUpdate] in that order."""
    from ascendo_macos.adapter import MacOSAdapter
    from ascendo_macos.managers.softwareupdate import SoftwareUpdateManager
    a = MacOSAdapter()
    pkgs = a.package_managers(mac_host)
    types = [type(p).__name__ for p in pkgs]
    assert types == ["BrewManager", "MasManager", "SoftwareUpdateManager"]


def test_snapshot_returns_timemachine_singleton():
    """M5.4: snapshot() returns TimeMachineSnapshot, cached."""
    from ascendo_macos.adapter import MacOSAdapter
    from ascendo_macos.snapshot import TimeMachineSnapshot
    a = MacOSAdapter()
    s1 = a.snapshot()
    assert isinstance(s1, TimeMachineSnapshot)
    s2 = a.snapshot()
    assert s1 is s2


def test_health_check_includes_softwareupdate_and_tmutil():
    """M5.4: doctor reports `softwareupdate` + `tmutil` lines."""
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    h = a.health_check()
    assert "softwareupdate" in h
    assert "tmutil" in h
```

### Step 2: Run tests, expect 4 FAILED

### Step 3: Modify `adapter.py`

1. Add imports:
```python
from .managers.softwareupdate import SoftwareUpdateManager
from .snapshot import TimeMachineSnapshot
```

2. `__init__`: add `self._cached_snapshot: TimeMachineSnapshot | None = None`

3. `capabilities` → add SNAPSHOTS:
```python
return (
    AdapterCapability.PACKAGE_MANAGEMENT
    | AdapterCapability.ELEVATION
    | AdapterCapability.INVENTORY
    | AdapterCapability.SNAPSHOTS
)
```

4. `package_managers(host)` extend list:
```python
return [
    BrewManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
    MasManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR,
               elevation=self.elevation()),
    SoftwareUpdateManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR,
                          elevation=self.elevation()),
]
```

5. `snapshot()` lazy-init + cached:
```python
def snapshot(self) -> ISnapshot:  # type: ignore[override]
    """M5.4: return cached TimeMachineSnapshot singleton."""
    if self._cached_snapshot is None:
        self._cached_snapshot = TimeMachineSnapshot(
            scripts_dir=self.SCRIPTS_DIR,
            lib_dir=self.LIB_DIR,
        )
    return self._cached_snapshot
```

6. `health_check()`: add 2 lines after `out["system_profiler"] = ...`:
```python
out["softwareupdate"] = self._softwareupdate_status()
out["tmutil"] = self._tmutil_status()
```

7. New helpers (mirror `_jq_status` pattern):
```python
def _softwareupdate_status(self) -> str:
    path = shutil.which("softwareupdate") or "/usr/sbin/softwareupdate"
    if not Path(path).exists():
        return "unavailable: softwareupdate not found (macOS-only built-in)"
    try:
        res = subprocess.run([path, "--help"], capture_output=True, text=True,
                             timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    if res.returncode != 0:
        return f"error: softwareupdate --help exited {res.returncode}"
    return "ok"


def _tmutil_status(self) -> str:
    path = shutil.which("tmutil") or "/usr/bin/tmutil"
    if not Path(path).exists():
        return "unavailable: tmutil not found (macOS-only built-in)"
    try:
        res = subprocess.run([path, "version"], capture_output=True, text=True,
                             timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    if res.returncode != 0:
        return f"error: tmutil version exited {res.returncode}"
    return "ok"
```

8. **Update class docstring** to reflect M5.4 scope.

### Step 4: Identify + adapt M5.3-era tests

Tests likely needing adaptation (legitimate state evolution):
- `test_unsupported_accessors_return_none_m54_m55` (from M5.3) — `snapshot() is None` → `is not None`. Possibly rename to `test_unsupported_accessors_return_none_m55` (only scheduler remains None now).
- `test_capabilities_is_package_management_and_elevation_and_inventory` — add SNAPSHOTS assertion. Rename.
- `test_package_managers_returns_brew_and_mas` (from M5.2/M5.3) — list grows to 3, rename.
- `test_health_check_reports_required_keys` — bump key count from 7 to 9, add softwareupdate + tmutil assertions.

### Step 5: Run all macOS tests + smoke doctor

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
PYTHONPATH=$(pwd)/core:$(pwd)/adapters/macos python3 -m ascendo doctor
# Expect: capabilities includes SNAPSHOTS; softwareupdate ok; tmutil ok
```

### Step 6: Commit

```bash
git add adapters/macos/ascendo_macos/adapter.py
git add adapters/macos/tests/test_adapter_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): wire SoftwareUpdateManager + TimeMachineSnapshot (M5.4.9)

Capability flag flipped: PACKAGE_MANAGEMENT | ELEVATION | INVENTORY |
SNAPSHOTS (was ... | INVENTORY).

package_managers() returns [BrewManager, MasManager, SoftwareUpdateManager]
in that order — softwareupdate last because apply may reboot the Mac.

snapshot() returns a cached TimeMachineSnapshot singleton.

health_check() now reports `softwareupdate` + `tmutil` components in
addition to the existing brew/mas/jq/system_profiler lines (was 7
components, now 9).

4 new wiring tests; pre-existing M5.3-era tests adapted (legitimate
state evolution).

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §3
EOF
)"
```

---

## Task 10: `bin/validate-macos.sh` Stage 10 + Stage 11

**Files:**
- Modify: `bin/validate-macos.sh`

### Step 1: Read existing structure

```bash
grep -n "^step \|FAIL_COUNT\|REPO_ROOT\|^==> " bin/validate-macos.sh | head -30
```

Stage 10 + Stage 11 insert immediately before the final summary, after Stage 9.

### Step 2: Append Stage 10 + Stage 11 blocks

```bash
# ============================================================
# Stage 10 — softwareupdate (M5.4)
# ============================================================
step "10. softwareupdate (M5.4)"

# Step 10.1 — doctor reports softwareupdate component
step "10.1 doctor: softwareupdate component"
DOCTOR_OUT="$(PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo doctor 2>&1)"
if printf '%s\n' "$DOCTOR_OUT" | grep -qE '^\s+softwareupdate\s+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT" | grep -E '^\s+softwareupdate\s+'
    result "10.1 doctor: softwareupdate component" 1
else
    result "10.1 doctor: softwareupdate component" 0
fi

# Step 10.2 — softwareupdate check phase end-to-end
step "10.2 softwareupdate check"
SU_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-validate-su-XXXXXX")"
SU_RID="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo run \
        --category softwareupdate --phase check \
        --runs-dir "$SU_DIR" >/dev/null 2>&1; then
    result "10.2 softwareupdate check" 1
else
    result "10.2 softwareupdate check" 0
fi

# Step 10.3 — softwareupdate plan phase
step "10.3 softwareupdate plan"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo run \
        --category softwareupdate --phase plan \
        --runs-dir "$SU_DIR" >/dev/null 2>&1; then
    result "10.3 softwareupdate plan" 1
else
    result "10.3 softwareupdate plan" 0
fi

# Step 10.4 — softwareupdate verify (soft-no-op without apply)
step "10.4 softwareupdate verify (soft-no-op)"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo run \
        --category softwareupdate --phase verify \
        --runs-dir "$SU_DIR" >/dev/null 2>&1; then
    result "10.4 softwareupdate verify" 1
else
    result "10.4 softwareupdate verify" 0
fi

# Step 10.5 — softwareupdate cleanup (no-op)
step "10.5 softwareupdate cleanup"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo run \
        --category softwareupdate --phase cleanup \
        --runs-dir "$SU_DIR" >/dev/null 2>&1; then
    result "10.5 softwareupdate cleanup" 1
else
    result "10.5 softwareupdate cleanup" 0
fi

# Step 10.6 — softwareupdate apply --dry-run (NEVER invokes sudo on real Mac)
step "10.6 softwareupdate apply --dry-run"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo run \
        --category softwareupdate --phase apply --dry-run \
        --runs-dir "$SU_DIR" >/dev/null 2>&1; then
    result "10.6 softwareupdate apply --dry-run" 1
else
    result "10.6 softwareupdate apply --dry-run" 0
fi

# Cleanup Stage 10 temp dir
[ -n "${SU_DIR:-}" ] && [ -d "$SU_DIR" ] && rm -rf "$SU_DIR"

# ============================================================
# Stage 11 — Time Machine read-only (M5.4)
# ============================================================
step "11. Time Machine read-only (M5.4)"

# Step 11.1 — doctor reports tmutil component
step "11.1 doctor: tmutil component"
if printf '%s\n' "$DOCTOR_OUT" | grep -qE '^\s+tmutil\s+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT" | grep -E '^\s+tmutil\s+'
    result "11.1 doctor: tmutil component" 1
else
    result "11.1 doctor: tmutil component" 0
fi

# Step 11.2 — TimeMachineSnapshot.list() end-to-end
step "11.2 TimeMachineSnapshot.list() end-to-end"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -c "
from ascendo_macos.adapter import MacOSAdapter
a = MacOSAdapter()
host = a.detect_host()
snap = a.snapshot()
snapshots = snap.list(host)
print(f'time machine: {len(snapshots)} local snapshots')
# Don't enforce >=1 — fresh-install Mac may have none yet.
" 2>&1; then
    result "11.2 TimeMachineSnapshot.list() end-to-end" 1
else
    result "11.2 TimeMachineSnapshot.list() end-to-end" 0
fi
```

Match the existing `step` / `result` / `section` helpers. Update the top-of-file
header comment to include Stages 10 + 11.

### Step 3: Run validate-macos.sh

```bash
bash bin/validate-macos.sh
```

Expect: every Stage 10 sub-step PASS (10.1-10.6), every Stage 11 sub-step PASS
(11.1-11.2). Final count bumped by 8 sub-steps.

### Step 4: Commit

```bash
git add bin/validate-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): validate-macos.sh Stage 10 + Stage 11 (M5.4.10)

Stage 10 — softwareupdate (M5.4):
  10.1 doctor reports `softwareupdate` component
  10.2 softwareupdate check phase end-to-end
  10.3 softwareupdate plan phase end-to-end
  10.4 softwareupdate verify phase soft-no-op
  10.5 softwareupdate cleanup phase no-op
  10.6 softwareupdate apply --dry-run (NEVER invokes sudo)

NOTE: real apply during validate is FORBIDDEN — softwareupdate apply
reboots the Mac, breaking the validate flow. Operator runs real apply
manually after tag, separately.

Stage 11 — Time Machine read-only (M5.4):
  11.1 doctor reports `tmutil` component
  11.2 TimeMachineSnapshot.list() end-to-end (>=0 snapshots; no
       lower bound — fresh-install Macs may have none yet)

Refs docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md §10
EOF
)"
```

---

## Task 11: `bin/run-tag-release-macos.sh` tag bump v0.0.10-alpha → v0.0.11-alpha

Mechanical bump.

**Files:**
- Modify: `bin/run-tag-release-macos.sh`

### Step 1: Find every v0.0.10-alpha occurrence

```bash
grep -n "v0\.0\.10" bin/run-tag-release-macos.sh
```

### Step 2: Replace all with v0.0.11-alpha

```bash
sed -i '' 's/v0\.0\.10-alpha/v0.0.11-alpha/g' bin/run-tag-release-macos.sh
```

### Step 3: Update the tag message text

Find the `git tag -a v0.0.11-alpha -m "..."` line. Replace the message body with:

```
"macOS adapter M5.4 — softwareupdate + Time Machine read-only; v0.0.11-alpha (apply RC=$APPLY_RC)"
```

### Step 4: Verify

```bash
grep -n "v0\.0\.10" bin/run-tag-release-macos.sh   # expect zero hits
grep -n "M5\.3" bin/run-tag-release-macos.sh       # update any prose if needed
bash -n bin/run-tag-release-macos.sh
bash bin/run-tag-release-macos.sh --what-if        # smoke
```

### Step 5: Commit

```bash
git add bin/run-tag-release-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): run-tag-release-macos.sh tag bump v0.0.11-alpha (M5.4.11)

M5.4 (softwareupdate + Time Machine read-only) ships under
v0.0.11-alpha. Single mechanical change: tag bumped from
v0.0.10-alpha to v0.0.11-alpha across every occurrence.

Tag message:
  "macOS adapter M5.4 — softwareupdate + Time Machine read-only;
   v0.0.11-alpha (apply RC=$APPLY_RC)"
EOF
)"
```

---

## Task 12: Real-hardware validation + tag v0.0.11-alpha (operator-driven)

Operator runs validate-macos.sh + run-tag-release-macos.sh, verifies tag.

### Step 1: Operator runs

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
bash bin/validate-macos.sh                  # expect ALL CHECKS PASSED with new Stages 10+11
bash bin/run-tag-release-macos.sh           # NO --softwareupdate flag — would reboot Mac
                                            # confirm 'apply' at brew gate
git tag -l v0.0.11-alpha                    # confirm tag
git show v0.0.11-alpha --stat
```

**No `$SUDO_PW` needed for the tag run** — softwareupdate apply is excluded
from validate (Stage 10.6 is dry-run only). Tag run only does brew apply,
which is the existing pattern.

If operator wants to actually apply softwareupdate updates separately, they
run (POST-TAG, separate session):

```bash
read -s -p "sudo password: " SUDO_PW; export SUDO_PW; echo
PYTHONPATH=$(pwd)/core:$(pwd)/adapters/macos python3 -m ascendo run \
    --category softwareupdate --phase apply
# Mac may reboot mid-run if any update has Action: restart
unset SUDO_PW
```

### Step 2: No commit — tag IS the artifact

Tag created by harness. Push deferred to Task 13.

---

## Task 13: HANDOFF Sesja 26 + PLAN M5.4 done + push branch + tag

**Files:**
- Modify: `HANDOFF.md` (prepend Sesja 26)
- Modify: `PLAN.md` (M5.4 row → done)

### Step 1: Prepend Sesja 26 to HANDOFF.md

Insert after the intro blockquote, BEFORE existing `## Sesja 25` section. Use the
M5.3 Sesja 25 entry as template. Adapt for M5.4 specifics:

- Architecture additions: SoftwareUpdateManager (uses MacElevation), TimeMachineSnapshot, capability SNAPSHOTS
- Files added (per M5.4.x sub-milestone)
- Real apply trace (Stage 10 dry-run output + tag output)
- Test count delta
- Heuristic limitation (if any caught during real-hardware run)
- What's next (M5.5 — launchd IScheduler, then v0.2.0 full M5)

### Step 2: Flip M5.4 row in PLAN.md

Find the M5 milestone table row for M5.4 (currently `⏳ pending`). Replace with:

```markdown
| **M5.4** | ✅ done (2026-05-04, **v0.0.11-alpha**) | `SoftwareUpdateManager` (sudo softwareupdate -ir -R, MacElevation injection) + `TimeMachineSnapshot` read-only (tmutil listlocalsnapshots /). Capability SNAPSHOTS added. ~35 new tests + Stage 10 + Stage 11 e2e via `validate-macos.sh`. Spec/plan: `docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md` + `docs/superpowers/plans/2026-05-04-macos-softwareupdate-snapshot.md`. See HANDOFF.md Sesja 26. |
```

Bump "Last updated" line. In per-manager scope table, flip rows for
`softwareupdate.py` and `snapshot.py` to `✅ M5.4`.

### Step 3: Commit + push branch + push tag

```bash
git add HANDOFF.md PLAN.md
git commit -m "$(cat <<'EOF'
docs: HANDOFF Sesja 26 + PLAN M5.4 done (v0.0.11-alpha)

macOS adapter M5.4 complete on real Mac (Mac.r12.home):
  - softwareupdate manager via sudo -A softwareupdate -ir -R
  - TimeMachineSnapshot read-only via tmutil listlocalsnapshots /
  - capability SNAPSHOTS added
  - v0.0.11-alpha tagged
  - validate-macos.sh Stages 10 + 11 green
  - ~35 new tests + 100+ macOS adapter tests green

Next: M5.5 — launchd IScheduler. After that, tag v0.2.0 (full M5).
EOF
)"

git push origin claude/musing-herschel-b52e7e
git push origin v0.0.11-alpha
```

### Step 4: Final regression check

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/ adapters/macos/tests/ -q
```

Expect: green (modulo 9 pre-existing test_service_endpoints.py failures).

The branch is now ready for review + merge to `main`.

---

## Self-review notes (addressed inline)

1. **Spec coverage**:
   - §1 Goal → Tasks 1-12 collectively
   - §2 Architecture → Tasks 3 (check), 4 (triplet), 5 (apply), 6 (Python manager), 7 (snapshot script), 8 (Python snapshot), 9 (adapter wire)
   - §3 Capability flag → Task 9
   - §4 SoftwareUpdateManager flag semantics → Task 5 (default `-ir`, `--all` for `-ia`, `--filter` for `-i <label>`)
   - §5 `softwareupdate -l` parser → Task 3 + Task 2 fixtures
   - §6 TimeMachineSnapshot scope → Tasks 7 + 8
   - §7 Health check additions → Task 9
   - §8 Per-app metadata → Task 3 + Task 6 sidecar parse
   - §9 Tests target → Tasks 3-9 (test files: 6+6+6+14+4+6+4 = 46 — note higher than spec's ~35 because tests grew during plan refinement; OK)
   - §10 Stage 10 + Stage 11 → Task 10
   - §11 Threat model → Task 5 (sudo -A, no shell injection)
   - §12 Deferred → noted in Sesja 26 entry (Task 13)
   - §13 Tag exit bar → Tasks 10, 11, 12

2. **Type consistency**:
   - `SoftwareUpdateManager` `__init__(scripts_dir, lib_dir, *, elevation, ...)` matches MasManager exactly.
   - `TimeMachineSnapshot` `__init__(scripts_dir, lib_dir, *, bash_path=None, timeout_sec=...)` matches MacOSInventory shape.
   - Both `SCRIPT_REL` constants are `softwareupdate/<phase>.sh` and `snapshot/list.sh` consistently.
   - SourceType enum values used: SOFTWAREUPDATE (Task 1) and SNAPSHOT (Task 1, also added). Update Task 1 commit message to mention BOTH.

3. **Placeholder scan**: no TBD/TODO; every step has executable commands or code.

4. **Task 1 expansion**: Task 1 originally said "add SOFTWAREUPDATE only" but Task 7 needs SNAPSHOT enum value too. Task 1 must add BOTH: SOFTWAREUPDATE and SNAPSHOT enum values, both regenerated into the schema, with 2 contract tests.

   **Updated Task 1 instruction**: in step 3, add TWO enum lines after `INVENTORY = "inventory"`:
   ```python
       INVENTORY = "inventory"  # macOS LaunchServices inventory category (M5.3)
       SOFTWAREUPDATE = "softwareupdate"  # macOS softwareupdate CLI (M5.4 OS patches)
       SNAPSHOT = "snapshot"  # macOS Time Machine local snapshots (M5.4)
       PLUGIN = "plugin"
   ```
   Add a 2nd test:
   ```python
   def test_source_type_has_snapshot_value() -> None:
       from ascendo.models.package import SourceType
       assert SourceType.SNAPSHOT.value == "snapshot"
   ```
   Update commit message to "feat(core): add SourceType.SOFTWAREUPDATE + SourceType.SNAPSHOT for macOS adapter (M5.4.1)".

5. **Task 5 reboot-survival caveat**: pre-emit success items BEFORE sudo invocation. If sudo fails, items still show success — verify catches this. Document trade-off in script header. Consider this a known M5.4 limitation; M5.x follow-up: post-apply sidecar reconciliation (parse softwareupdate output + update items in-place).
