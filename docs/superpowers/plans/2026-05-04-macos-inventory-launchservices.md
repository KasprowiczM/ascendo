# macOS adapter — M5.3 LaunchServices inventory implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `MacOSAdapter.inventory()` returning a working `MacOSInventory` instance — the dashboard Categories tab on macOS populates with the real installed-apps list, classified into SourceType.{SYSTEM, MAS, BREW, WEB}. Tag `v0.0.10-alpha`.

**Architecture:** Mirrors `WindowsInventory` exactly — bash list script (`scripts/inventory/list.sh`) invokes `system_profiler -json -detailLevel mini SPApplicationsDataType`, classifies each app post-hoc, emits an `ascendo/v1` sidecar via the existing `lib/ascendo_json.sh` helpers. Python wrapper (`MacOSInventory`) spawns the script with a per-call uuid4 + private tempdir, parses the sidecar, returns `list[Package]`. Adapter wires it as a cached singleton + flips `INVENTORY` capability on. Dashboard consumes via the pre-existing `/inventory*` routes + 60s `InventoryCache` — zero new dashboard work.

**Tech Stack:** Python 3.11+ (Pydantic v2), Bash 3.2+ (macOS system shell), `system_profiler` (built-in macOS), `jq`, optional `mas` + `brew` for classification refinement. Tests: pytest (mock-based unit + bash integration via fake `system_profiler` binary).

**Branch:** `claude/musing-herschel-b52e7e` (current worktree, continuing on top of v0.0.9-alpha). Push deferred to Task 8.

**Spec reference:** [docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md](../specs/2026-05-04-macos-inventory-launchservices-design.md)

**Working directory:** `/Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e`. All commands assume this CWD.

---

## Critical lessons from M5.2 (DO NOT repeat)

When dispatching subagents for any bash-touching task:
- **`json_init` API**: 13 positional args, NO schema URI as first arg. Canonical: `json_init <phase> <category> <run_id> <trigger> <profile_name> <tool_name> <tool_version> <host_name> <host_os> <host_os_version> <host_arch> <host_user> <host_is_elevated>`. The plan code blocks below show the correct shape; don't paste from M5.2's plan §Task 4 etc. which had the old wrong form.
- **`json_save_on_exit`**: SINGLE arg — `json_save_on_exit "$OUTPUT_DIR"`. Captures `$?` internally.
- **`json_add_message` level**: `error` (4 letters), NOT `err`. Other valid levels: `info`, `warn`.
- **JSON parsing tooling**: `jq` for ALL JSON parsing in bash scripts. NEVER inline `python3 - <<PYEOF`. Reviewers caught this in M5.2 Task 7.
- **Helper return values**: any helper that returns a status string consumed by `json_add_item` MUST return only valid `ItemStatus` enum members (success, up_to_date, planned, partial, failed, skipped). Reviewers caught `failed-not-signed-in` in M5.2 Task 8.
- **bash 3.2 only**: no `[[`, no `declare -A`, no `mapfile`, no `readarray`, no `<()` process substitution where `$()` works. Use `[ ... ]`, parallel space-separated strings + `awk -F'|'`.
- **Shell-string injection**: when interpolating into JSON bodies via `curl -d`, use `jq -n --arg key "$value" '{...}'` — never bash string concatenation. M5.2 Task 10 caught this.
- **Error vs empty distinction**: never `command 2>/dev/null || true` if you need to distinguish "crash" from "no output". Capture `$?` separately (see Task 5b in `bin/run-tag-release-macos.sh` for the pattern).
- **Canonical bash template**: `adapters/macos/scripts/mas/check.sh` (shipped in M5.2). Read it before writing any new bash script. The plan's code samples in this file follow that shape; trust the file on disk over any inline sample.

---

## File structure

| New file | Responsibility | LOC |
|---|---|---|
| `adapters/macos/scripts/inventory/list.sh` | Bash: spawn `system_profiler`, classify each app, emit one `Item` per app | ~200 |
| `adapters/macos/ascendo_macos/inventory.py` | Python: `MacOSInventory(IInventory)` — spawn script, parse sidecar, return `list[Package]` | ~250 |
| `adapters/macos/tests/test_inventory_list_script.py` | 6 bash-integration tests via fake `system_profiler` | ~250 |
| `adapters/macos/tests/test_macos_inventory_smoke.py` | 8 mock-based Python tests | ~280 |
| `adapters/macos/tests/fixtures/system_profiler_apps.json` | Sample fixture: ~15 apps spanning SYSTEM/MAS/BREW/WEB | ~150 |
| `tests/contract/test_sidecar_v1.py` | +1 test for `SourceType.SYSTEM` enum | +10 |
| `docs/superpowers/specs/2026-05-04-session-22-handoff.md` | Sesja 22 handoff (written in Task 8) | ~120 |

| Modified file | Change |
|---|---|
| `core/ascendo/models/package.py` | +1 enum line: `SYSTEM = "system"` |
| `docs/architecture/schemas/sidecar.v1.schema.json` | regenerated |
| `adapters/macos/ascendo_macos/adapter.py` | capability flag + `inventory()` cached singleton + `_system_profiler_status()` |
| `adapters/macos/tests/test_adapter_smoke.py` | +3 wiring tests |
| `bin/validate-macos.sh` | Stage 9 appended |
| `bin/run-tag-release-macos.sh` | Tag v0.0.9-alpha → v0.0.10-alpha (every occurrence) + tag message |
| `HANDOFF.md` | Sesja 22 entry prepended |
| `PLAN.md` | M5.3 row → ✅ done |

---

## Task 1: Add `SourceType.SYSTEM` enum + regenerate schema

The classification rule "path startswith /System/Applications/" needs a target enum value. Mirrors how M5.2 Task 1 added `MAS`.

**Files:**
- Modify: `core/ascendo/models/package.py` (one enum line)
- Modify: `docs/architecture/schemas/sidecar.v1.schema.json` (regenerated)
- Test: `tests/contract/test_sidecar_v1.py` (one new test)

- [ ] **Step 1: Append failing test to `tests/contract/test_sidecar_v1.py`**

```python
def test_source_type_has_system_value() -> None:
    """MacOSInventory tags Apple-bundled apps as SourceType.SYSTEM. Required by M5.3."""
    from ascendo.models.package import SourceType
    assert SourceType.SYSTEM.value == "system"
```

- [ ] **Step 2: Run failing test**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_system_value -v
```

Expected: `FAILED` with `AttributeError: SYSTEM`.

- [ ] **Step 3: Add the enum value**

In `core/ascendo/models/package.py`, locate `SourceType`. Insert after `WEB = "web"`:

```python
    WEB = "web"
    SYSTEM = "system"          # macOS Apple-bundled apps in /System/Applications/
    PLUGIN = "plugin"
```

- [ ] **Step 4: Re-run the test, expect PASS**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_system_value -v
```

- [ ] **Step 5: Regenerate the JSON Schema**

```bash
PYTHONPATH=$(pwd)/core python3 scripts/export-sidecar-schema.py
```

Should produce a diff in `docs/architecture/schemas/sidecar.v1.schema.json` adding `"system"` to the `SourceType` enum array.

- [ ] **Step 6: Run full contract suite — expect no regressions**

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/ -q
```

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/models/package.py
git add docs/architecture/schemas/sidecar.v1.schema.json
git add tests/contract/test_sidecar_v1.py
git commit -m "$(cat <<'EOF'
feat(core): add SourceType.SYSTEM for macOS adapter (M5.3.1)

The macOS LaunchServices inventory needs a first-class enum value
for Apple-bundled apps under /System/Applications/. Mirrors how
M5.2 added SourceType.MAS.

Sidecar JSON Schema regenerated to include the new enum value.

Refs docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §1
EOF
)"
```

---

## Task 2: Inventory test fixtures

Create the canned `system_profiler` JSON output that subsequent bash + Python tests will replay against.

**Files:**
- Create: `adapters/macos/tests/fixtures/system_profiler_apps.json`

- [ ] **Step 1: Write the fixture**

Cover at least 15 apps spanning every classification rule. Real shape per `system_profiler -json -detailLevel mini SPApplicationsDataType`:

```json
{
  "SPApplicationsDataType": [
    {
      "_name": "Safari",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-12-01T10:00:00Z",
      "obtained_from": "apple",
      "path": "/System/Applications/Safari.app",
      "signed_by": ["Software Signing", "Apple Code Signing Certification Authority"],
      "version": "18.2"
    },
    {
      "_name": "Mail",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-12-01T10:00:00Z",
      "obtained_from": "apple",
      "path": "/System/Applications/Mail.app",
      "signed_by": ["Software Signing"],
      "version": "16.0"
    },
    {
      "_name": "Amphetamine",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2024-08-12T14:23:11Z",
      "obtained_from": "mac_app_store",
      "path": "/Applications/Amphetamine.app",
      "signed_by": ["Apple Mac OS Application Signing"],
      "version": "5.3.5"
    },
    {
      "_name": "iMovie",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2024-09-10T08:00:00Z",
      "obtained_from": "mac_app_store",
      "path": "/Applications/iMovie.app",
      "signed_by": ["Apple Mac OS Application Signing"],
      "version": "10.4.4"
    },
    {
      "_name": "Inkscape",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-04-01T12:00:00Z",
      "obtained_from": "identified_developer",
      "path": "/Applications/Inkscape.app",
      "signed_by": ["Developer ID Application: Inkscape"],
      "version": "1.4"
    },
    {
      "_name": "MacWhisper",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-03-15T18:00:00Z",
      "obtained_from": "identified_developer",
      "path": "/Applications/MacWhisper.app",
      "signed_by": ["Developer ID Application: Jordi Bruin"],
      "version": "9.0.1"
    },
    {
      "_name": "BlackHole 2ch",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2024-11-20T09:00:00Z",
      "obtained_from": "identified_developer",
      "path": "/Applications/BlackHole 2ch.app",
      "signed_by": ["Developer ID Application: Existential Audio"],
      "version": "0.6.0"
    },
    {
      "_name": "Custom Internal Tool",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-01-10T11:00:00Z",
      "obtained_from": "unknown",
      "path": "/Applications/Custom Internal Tool.app",
      "signed_by": [],
      "version": "0.1.0"
    },
    {
      "_name": "Calculator",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-12-01T10:00:00Z",
      "obtained_from": "apple",
      "path": "/System/Applications/Calculator.app",
      "signed_by": ["Software Signing"],
      "version": "10.16"
    },
    {
      "_name": "Notes",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-12-01T10:00:00Z",
      "obtained_from": "apple",
      "path": "/System/Applications/Notes.app",
      "signed_by": ["Software Signing"],
      "version": "4.10"
    },
    {
      "_name": "KeePassium",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2024-06-01T10:00:00Z",
      "obtained_from": "mac_app_store",
      "path": "/Applications/KeePassium.app",
      "signed_by": ["Apple Mac OS Application Signing"],
      "version": "2.5"
    },
    {
      "_name": "Firefox",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-04-15T10:00:00Z",
      "obtained_from": "identified_developer",
      "path": "/Applications/Firefox.app",
      "signed_by": ["Developer ID Application: Mozilla Corporation"],
      "version": "125.0"
    },
    {
      "_name": "VLC",
      "arch_kind": "arch_arm_i64",
      "lastModified": "2025-02-10T10:00:00Z",
      "obtained_from": "identified_developer",
      "path": "/Applications/VLC.app",
      "signed_by": ["Developer ID Application: VideoLAN"],
      "version": "3.0.20"
    },
    {
      "_name": "Empty Path App",
      "arch_kind": "arch_arm_i64",
      "lastModified": "",
      "obtained_from": "unknown",
      "path": "",
      "signed_by": [],
      "version": ""
    }
  ]
}
```

The "Empty Path App" entry is intentional — exercises the script's defensive skip for malformed entries.

- [ ] **Step 2: Commit**

```bash
git add adapters/macos/tests/fixtures/system_profiler_apps.json
git commit -m "$(cat <<'EOF'
test(macos): add system_profiler fixture for inventory tests (M5.3.2)

14 apps + 1 malformed entry covering every classification rule of
the M5.3 LaunchServices inventory:
  - SYSTEM: Safari, Mail, Calculator, Notes
  - MAS:    Amphetamine, iMovie, KeePassium
  - BREW:   Inkscape, MacWhisper, BlackHole 2ch (matches `brew list --cask`)
  - WEB:    Firefox, VLC, Custom Internal Tool
  - skip:   Empty Path App (defensive case)

Fixture mirrors the real shape of
`system_profiler -json -detailLevel mini SPApplicationsDataType`
on macOS 14+.
EOF
)"
```

---

## Task 3: `scripts/inventory/list.sh` — bash list script + 6 integration tests

The bash script is the heart of M5.3. It runs `system_profiler` once, classifies each app, emits one `Item` per app via `lib/ascendo_json.sh`. Reads optional `MAS_BIN`, `BREW_BIN`, `SP_BIN` env vars to allow test fakes.

**Files:**
- Create: `adapters/macos/scripts/inventory/list.sh` (~200 LOC)
- Create: `adapters/macos/tests/test_inventory_list_script.py` (~250 LOC)

### Step 1: Write the test file

Create `adapters/macos/tests/test_inventory_list_script.py`:

```python
"""Tests for adapters/macos/scripts/inventory/list.sh.

Six integration tests using a fake system_profiler binary that returns
the canned fixture JSON. Bash-only execution path (no Python under test).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "inventory" / "list.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_sp(tmp_path: Path) -> Path:
    """Fake system_profiler binary returning the canned fixture JSON."""
    fixture = (FIX / "system_profiler_apps.json").read_text()
    p = tmp_path / "fake_system_profiler"
    body = (
        "#!/usr/bin/env bash\n"
        f"cat <<'EOF_SP'\n{fixture}\nEOF_SP\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_fake_mas(tmp_path: Path, *, app_names: list[str] | None = None) -> Path:
    """Fake mas binary returning a `mas list` style table for the given names."""
    p = tmp_path / "fake_mas"
    if app_names is None:
        app_names = ["Amphetamine", "iMovie", "KeePassium"]
    rows = "\n".join(
        f" {1000000000 + i:>10}  {name:<28}({i}.0)"
        for i, name in enumerate(app_names, start=1)
    )
    body = (
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        f"  list)    cat <<'EOF_LIST'\n{rows}\nEOF_LIST\n           ;;\n"
        "  version) echo '6.0.1' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_fake_brew(tmp_path: Path, *, casks: list[str] | None = None) -> Path:
    """Fake brew binary returning `brew list --cask` for the given tokens."""
    p = tmp_path / "fake_brew"
    if casks is None:
        casks = ["inkscape", "macwhisper", "blackhole-2ch"]
    body = (
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'list' ] && [ \"$2\" = '--cask' ]; then\n"
        f"  printf '%s\\n' {' '.join(repr(c) for c in casks)}\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
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


def _run(script: Path, sp: Path, mas: Path | None, brew: Path | None,
         output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["SP_BIN"] = str(sp)
    if mas is not None:
        env["MAS_BIN"] = str(mas)
    else:
        env.pop("MAS_BIN", None)
    if brew is not None:
        env["BREW_BIN"] = str(brew)
    else:
        env.pop("BREW_BIN", None)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_emits_one_item_per_app(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    # 14 valid apps in fixture; the empty-path entry is skipped.
    assert len(sc.items) == 14
    assert sc.status.value == "success"


def test_classification_distribution(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        st = item.source.type.value
        by_source[st] = by_source.get(st, 0) + 1
    # SYSTEM: Safari, Mail, Calculator, Notes
    assert by_source.get("system", 0) == 4
    # MAS: Amphetamine, iMovie, KeePassium (rule 2 via _name match) + nothing
    # extra from rule 3 (all three are also obtained_from=mac_app_store, but
    # rule 2 fires first)
    assert by_source.get("mas", 0) == 3
    # BREW: Inkscape, MacWhisper, BlackHole 2ch (cask token match)
    assert by_source.get("brew", 0) == 3
    # WEB: Firefox, VLC, Custom Internal Tool
    assert by_source.get("web", 0) == 4


def test_no_mas_falls_through_to_obtained_from(tmp_path):
    """When mas is not on PATH, rule 2 doesn't fire; rule 3 still catches MAS apps via obtained_from."""
    sp = _make_fake_sp(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    # Pass MAS_BIN as an empty/missing path so the script's check fails
    res = _run(SCRIPT, sp, None, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        by_source[item.source.type.value] = by_source.get(item.source.type.value, 0) + 1
    # Same MAS count via rule 3 fallback
    assert by_source.get("mas", 0) == 3


def test_no_brew_falls_through_to_web(tmp_path):
    """When brew is not on PATH, would-be-BREW apps classify as WEB."""
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, None, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        by_source[item.source.type.value] = by_source.get(item.source.type.value, 0) + 1
    assert by_source.get("brew", 0) == 0
    # Inkscape + MacWhisper + BlackHole 2ch now classify as WEB,
    # plus the original 4 WEB apps -> 7 total
    assert by_source.get("web", 0) == 7


def test_per_item_metadata(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    safari = next(i for i in sc.items if i.id == "Safari")
    assert safari.source.type.value == "system"
    assert safari.current_version == "18.2"
    assert safari.target_version == ""
    assert safari.status.value == "up_to_date"
    # source.feed carries the bundle path
    assert safari.source.feed == "/System/Applications/Safari.app"


def test_system_profiler_failure_exits_30(tmp_path):
    """When system_profiler exits non-zero, script aborts with exit 30 + sidecar."""
    sp = tmp_path / "broken_sp"
    sp.write_text("#!/usr/bin/env bash\necho 'system_profiler crashed' >&2\nexit 1\n")
    os.chmod(sp, 0o755)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, None, None, out, rid)
    # Expect exit 30 (apply-fail-unknown per docs/agents/contract.md)
    assert res.returncode == 30
    # Sidecar still emitted via EXIT trap
    sc = _parse(out / rid / "check__inventory.json")
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)
```

### Step 2: Run tests, expect 6 FAILED (script doesn't exist)

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_inventory_list_script.py -v
```

### Step 3: Read the canonical bash template

```bash
cat adapters/macos/scripts/mas/check.sh
cat adapters/macos/lib/ascendo_json.sh | head -200
```

Note exactly: `json_init` 13-arg form, `json_save_on_exit "$OUTPUT_DIR"`, `json_add_message "error" ...`, EXIT trap installed AFTER `json_init`, full host info collection block.

### Step 4: Write `scripts/inventory/list.sh`

Mirror mas/check.sh structure exactly. The phase-specific section:

```bash
# -- resolve binaries (env-overridable for tests) ------------------------------
SP_BIN="${SP_BIN:-/usr/sbin/system_profiler}"
MAS_BIN_RESOLVED=""
if [ -n "${MAS_BIN:-}" ] && [ -x "${MAS_BIN}" ]; then
    MAS_BIN_RESOLVED="$MAS_BIN"
elif command -v mas >/dev/null 2>&1; then
    MAS_BIN_RESOLVED="$(command -v mas)"
fi
BREW_BIN_RESOLVED=""
if [ -n "${BREW_BIN:-}" ] && [ -x "${BREW_BIN}" ]; then
    BREW_BIN_RESOLVED="$BREW_BIN"
elif command -v brew >/dev/null 2>&1; then
    BREW_BIN_RESOLVED="$(command -v brew)"
fi

# -- warm up classification dictionaries (best-effort) -------------------------
# MAS_NAMES: pipe-separated list of app names from `mas list` (column 2..n-1).
# Empty if mas not installed; rule 2 simply never fires.
MAS_NAMES=""
if [ -n "$MAS_NAMES_RESOLVED" ] && [ -n "$MAS_BIN_RESOLVED" ]; then : ; fi  # no-op marker
if [ -n "$MAS_BIN_RESOLVED" ]; then
    # mas list output: "<id>  <name padded>  (version)"
    # Strip leading id + trailing (version), trim whitespace -> name.
    MAS_NAMES="$("$MAS_BIN_RESOLVED" list 2>/dev/null \
        | sed -E 's/^[[:space:]]*[0-9]+[[:space:]]+//; s/[[:space:]]*\([^)]*\)[[:space:]]*$//; s/[[:space:]]+$//' \
        | tr '\n' '|')"
fi

# BREW_CASK_TOKENS: newline-separated; cask tokens are kebab-case, lowercase.
BREW_CASK_TOKENS=""
if [ -n "$BREW_BIN_RESOLVED" ]; then
    BREW_CASK_TOKENS="$("$BREW_BIN_RESOLVED" list --cask 2>/dev/null || true)"
fi

# -- run system_profiler -------------------------------------------------------
SP_RC=0
SP_OUT="$("$SP_BIN" -json -detailLevel mini SPApplicationsDataType 2>&1)" || SP_RC=$?
if [ "$SP_RC" -ne 0 ]; then
    json_add_message "error" "system_profiler failed (exit $SP_RC): $SP_OUT"
    exit 30
fi

# -- per-app emit --------------------------------------------------------------
# Classification helpers (functions, bash 3.2 safe)
classify_app() {
    local _path="$1"
    local _name="$2"
    local _obtained="$3"
    case "$_path" in
        /System/Applications/*) printf 'system\n'; return ;;
    esac
    # Rule 2: name matches mas list output
    if [ -n "$MAS_NAMES" ]; then
        case "|$MAS_NAMES" in
            *"|$_name|"*) printf 'mas\n'; return ;;
        esac
    fi
    # Rule 3: system_profiler obtained_from = mac_app_store
    if [ "$_obtained" = "mac_app_store" ]; then
        printf 'mas\n'; return
    fi
    # Rule 4: brew cask token matches name (lowercased)
    if [ -n "$BREW_CASK_TOKENS" ]; then
        # Lowercase _name; replace spaces with hyphens; strip trailing .app if present.
        local _token
        _token="$(printf '%s' "$_name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"
        if printf '%s\n' "$BREW_CASK_TOKENS" | grep -Fxq "$_token"; then
            printf 'brew\n'; return
        fi
    fi
    # Default: WEB
    printf 'web\n'
}

# Walk apps via jq, one TSV row per app.
printf '%s' "$SP_OUT" | jq -r '
    .SPApplicationsDataType[]
    | [(.path // ""), (._name // ""), (.version // ""), (.obtained_from // "")]
    | @tsv
' | while IFS="$(printf '\t')" read -r app_path app_name app_ver app_obtained; do
    [ -n "$app_path" ] || continue
    [ -n "$app_name" ] || continue
    src_type="$(classify_app "$app_path" "$app_name" "$app_obtained")"
    # id = app_name (path basename without .app would also work — see spec §6).
    json_add_item "$app_name" "$app_ver" "" "up_to_date" "$src_type" "$app_path"
done

exit 0
```

(Wrap the above with the canonical header — set -o pipefail, SCRIPT_DIR resolution, lib sourcing in order ascendo_json.sh first, arg parsing with required-arg validation, host info block, tool info, json_init, EXIT trap.)

The `json_add_item` 6-arg form is `<id> <current_version> <target_version> <status> [source_type] [source_feed]` — the 6th arg sets `source.feed`. Confirm against `lib/ascendo_json.sh` before pasting.

### Step 5: chmod + run tests

```bash
chmod +x adapters/macos/scripts/inventory/list.sh
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_inventory_list_script.py -v
```

Expected: 6 PASS.

### Step 6: Run full macOS suite for regression

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
```

Expect: previous count + 6.

### Step 7: Commit

```bash
git add adapters/macos/scripts/inventory/list.sh
git add adapters/macos/tests/test_inventory_list_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/inventory/list.sh — LaunchServices enumeration (M5.3.3)

Read-only inventory of installed macOS applications via
`system_profiler -json -detailLevel mini SPApplicationsDataType`.
Classifies each app post-hoc via the 5-rule tree from the M5.3 spec
(SYSTEM > MAS bundle/name > MAS obtained_from > BREW cask > WEB).

Emits one ascendo/v1 sidecar at <output-dir>/<run-id>/check__inventory.json
on every code path via EXIT trap. Bash 3.2-safe. jq for ALL JSON
parsing; no inline python3.

system_profiler failure -> exit 30, sidecar.status=failed,
error message on the sidecar.

6 fake-system_profiler integration tests cover: per-app emission,
classification distribution, no-mas fallback to rule 3, no-brew
fallback to WEB, per-item metadata, system_profiler crash path.

Refs docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §§4-6
EOF
)"
```

---

## Task 4: `MacOSInventory` Python wrapper + 8 mock-based tests

Mirrors `WindowsInventory`. Spawns the bash script via `subprocess.run` with a per-call uuid4 + private tempdir, parses the resulting sidecar via `read_sidecar`, returns `list[Package]`.

**Files:**
- Create: `adapters/macos/ascendo_macos/inventory.py` (~250 LOC)
- Create: `adapters/macos/tests/test_macos_inventory_smoke.py` (~280 LOC)

### Step 1: Read the canonical Python template

```bash
cat adapters/windows/ascendo_windows/inventory.py
```

Match its public surface exactly: `__init__(scripts_dir, lib_dir, *, bash_path=None, timeout_sec=300)`, `list_installed(host, *, categories=None)`, `emit_sidecar(run, host, packages)`, plus private helpers `_build_argv`, `_sidecar_to_packages`, `_format_missing_sidecar_error`, `_resolve_bash` (NEW — replaces `_resolve_pwsh`).

### Step 2: Write the test file

Create `adapters/macos/tests/test_macos_inventory_smoke.py`. 8 tests:

```python
"""Mock-based smoke tests for MacOSInventory."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
sys.path.insert(0, str(ADAPTER_ROOT))

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo, OperatingSystem, ElevationMethod
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo.models.package import SourceType
from ascendo_macos.inventory import MacOSInventory


SCRIPTS_DIR = ADAPTER_ROOT / "scripts"
LIB_DIR = ADAPTER_ROOT / "lib"


def _mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def _minimal_sidecar(items_json: list[dict]) -> str:
    """Build a minimal valid ascendo/v1 sidecar JSON string with the given items."""
    rid = str(uuid.uuid4())
    return json.dumps({
        "schema": "ascendo/v1",
        "run": {"id": rid, "trigger": "cli", "profile": "default",
                "dry_run": False, "started_at": "2026-05-04T12:00:00Z"},
        "host": {"hostname": "x", "os": "macos", "os_version": "14.5",
                 "arch": "arm64", "user": "mk", "is_elevated": False,
                 "elevation_method": "none"},
        "tool": {"name": "system_profiler", "version": "1.0"},
        "phase": "check", "category": "inventory",
        "started_at": "2026-05-04T12:00:00Z",
        "finished_at": "2026-05-04T12:00:01Z",
        "status": "success",
        "summary": {"total": len(items_json), "success": 0, "failed": 0,
                    "skipped": 0, "up_to_date": len(items_json),
                    "planned": 0, "needs_reboot": False},
        "items": items_json,
        "messages": [],
    })


def _item(name: str, src: str, version: str = "1.0",
          path: str | None = None) -> dict:
    return {
        "id": name,
        "current_version": version,
        "target_version": "",
        "resolved_version": version,
        "status": "up_to_date",
        "source": {"type": src, "feed": path or f"/Applications/{name}.app"},
    }


def test_identity_and_paths():
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    assert inv.SCRIPT_REL == "inventory/list.sh"
    assert inv.scripts_dir == SCRIPTS_DIR


def test_list_installed_returns_packages_from_sidecar(tmp_path):
    items = [_item("Safari", "system"), _item("Amphetamine", "mas"),
             _item("Inkscape", "brew"), _item("Firefox", "web")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        # Simulate the bash script writing its sidecar to disk.
        # Find the run-id in argv (--run-id <uuid>)
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_mac_host())
    assert len(packages) == 4
    sources = {p.source.type for p in packages}
    assert sources == {SourceType.SYSTEM, SourceType.MAS,
                       SourceType.BREW, SourceType.WEB}


def test_list_installed_categories_filter(tmp_path):
    items = [_item("Safari", "system"), _item("Amphetamine", "mas")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(_mac_host(), categories=["mas"])
    assert len(packages) == 1
    assert packages[0].source.type == SourceType.MAS


def test_list_installed_missing_sidecar_raises_managererror():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_mac_host())
    assert "sidecar" in str(excinfo.value).lower()


def test_list_installed_script_exit_30_raises_managererror():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 30, "", "system_profiler crashed")

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError):
            inv.list_installed(_mac_host())


def test_list_installed_timeout_raises_managererror():
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)

    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, timeout_sec=300)
    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ManagerError) as excinfo:
            inv.list_installed(_mac_host())
    assert "timeout" in str(excinfo.value).lower() or "timed" in str(excinfo.value).lower()


def test_list_installed_non_macos_returns_empty():
    """Inventory on a Linux/Windows host returns [] cleanly (no shell-out)."""
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    linux_host = HostInfo(
        hostname="x", os=OperatingSystem.LINUX_OTHER, os_version="24.04",
        arch="x86_64", user="x", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )
    packages = inv.list_installed(linux_host)
    assert packages == []


def test_emit_sidecar_returns_valid_sidecar():
    inv = MacOSInventory(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR)
    run = RunInfo(id=str(uuid.uuid4()), trigger=Trigger.CLI,
                  profile="default", dry_run=False,
                  started_at="2026-05-04T12:00:00Z")
    host = _mac_host()
    # Build a single Package via list_installed using a fake run pipeline
    items = [_item("Safari", "system")]
    sidecar_text = _minimal_sidecar(items)

    def fake_run(argv, **kwargs):
        rid = argv[argv.index("--run-id") + 1]
        out = Path(argv[argv.index("--output-dir") + 1]) / rid
        out.mkdir(parents=True, exist_ok=True)
        (out / "check__inventory.json").write_text(sidecar_text)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=fake_run):
        packages = inv.list_installed(host)
    sidecar = inv.emit_sidecar(run, host, packages)
    assert sidecar.schema.value == "ascendo/v1"
    assert sidecar.phase.value == "check"
    assert sidecar.category.value == "inventory"
    assert len(sidecar.items) == 1
```

### Step 3: Write `adapters/macos/ascendo_macos/inventory.py`

Mirror `adapters/windows/ascendo_windows/inventory.py` — same class layout, same docstrings (with macOS-specific terminology subbed in), same private helpers. The adaptations:

- `_resolve_bash()` replaces `_resolve_pwsh()` — searches `/bin/bash` then `bash` on PATH (required by macOS sandbox; mac default `/bin/bash` is 3.2.57).
- `SCRIPT_REL = "inventory/list.sh"` (not `.ps1`).
- `_build_argv` produces `[bash_path, str(script_path), "--run-id", run_id, "--trigger", "cli", "--profile", "default", "--output-dir", str(out_dir)]` — no `-NoProfile`/`-File` PowerShell-isms.
- `list_installed(host, *, categories=None)`:
  - First check `host.os == OperatingSystem.MACOS` — else return `[]` (mirrors Windows pattern's host gate).
  - `categories` filter applied to the parsed `Package` list using `p.source.type.value in categories`.
- `_sidecar_to_packages` constructs `Package(name=item.id, source=item.source, evidence=ItemEvidence(binary_path=item.source.feed, binary_version=item.current_version), version=item.current_version)` (or whatever the Package model expects — check `core/ascendo/models/package.py`).
- `_format_missing_sidecar_error` returns a string explaining what the script was expected to write.

Constants:
- `DEFAULT_TIMEOUT_SEC = 300`
- `SCRIPT_REL = "inventory/list.sh"`

### Step 4: Run tests, expect 8 PASS

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_macos_inventory_smoke.py -v
```

### Step 5: Run full macOS suite for regression

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
```

### Step 6: Commit

```bash
git add adapters/macos/ascendo_macos/inventory.py
git add adapters/macos/tests/test_macos_inventory_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): MacOSInventory Python wrapper (M5.3.4)

Mirrors WindowsInventory exactly. Spawns scripts/inventory/list.sh
via subprocess, parses the ascendo/v1 sidecar, returns list[Package].
Per-call uuid4 + private tempdir so the sidecar filename never
collides with any phase-pipeline emission.

list_installed(host, *, categories=None) — host gate (returns []
on non-macOS), categories filter post-hoc on SourceType.value.
emit_sidecar(run, host, packages) — renders the canonical
phase=check / category=inventory sidecar for orchestrator-history
storage.

8 mock-based smoke tests cover identity, sidecar parsing, categories
filter, missing sidecar -> ManagerError, script-exit-30 ->
ManagerError, timeout -> ManagerError, non-macOS host returns [],
emit_sidecar shape.

Refs docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §2
EOF
)"
```

---

## Task 5: `MacOSAdapter` wire-up — capability flip + cached singleton + health

**Files:**
- Modify: `adapters/macos/ascendo_macos/adapter.py`
- Modify: `adapters/macos/tests/test_adapter_smoke.py`

### Step 1: Append failing tests to `test_adapter_smoke.py`

```python
def test_capabilities_includes_inventory():
    """M5.3: INVENTORY flag added to PACKAGE_MANAGEMENT | ELEVATION."""
    from ascendo.interfaces import AdapterCapability
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    assert AdapterCapability.PACKAGE_MANAGEMENT in a.capabilities
    assert AdapterCapability.ELEVATION in a.capabilities
    assert AdapterCapability.INVENTORY in a.capabilities


def test_inventory_returns_macosinventory_singleton():
    """M5.3: inventory() returns MacOSInventory, cached across calls."""
    from ascendo_macos.adapter import MacOSAdapter
    from ascendo_macos.inventory import MacOSInventory
    a = MacOSAdapter()
    i1 = a.inventory()
    assert isinstance(i1, MacOSInventory)
    i2 = a.inventory()
    assert i1 is i2  # cached singleton


def test_health_check_includes_system_profiler_component():
    """M5.3: doctor reports a `system_profiler` line."""
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    h = a.health_check()
    assert "system_profiler" in h
```

### Step 2: Run, expect 3 FAILED

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v -k "inventory or system_profiler"
```

### Step 3: Modify `adapter.py`

1. Add import:
```python
from .inventory import MacOSInventory
```

2. Update `__init__`:
```python
def __init__(self) -> None:
    self._cached_host: HostInfo | None = None
    self._cached_elevation: MacElevation | None = None
    self._cached_inventory: MacOSInventory | None = None  # NEW
```

3. Update `capabilities`:
```python
@property
def capabilities(self) -> AdapterCapability:
    return (
        AdapterCapability.PACKAGE_MANAGEMENT
        | AdapterCapability.ELEVATION
        | AdapterCapability.INVENTORY
    )
```

4. Replace `inventory()`:
```python
def inventory(self) -> IInventory:  # type: ignore[override]
    """M5.3: return the cached MacOSInventory singleton."""
    if self._cached_inventory is None:
        self._cached_inventory = MacOSInventory(
            scripts_dir=self.SCRIPTS_DIR,
            lib_dir=self.LIB_DIR,
        )
    return self._cached_inventory
```

5. In `health_check`, add (alongside `_jq_status` / `_mas_status`):
```python
out["system_profiler"] = self._system_profiler_status()
```

6. Add new helper modeled on `_jq_status`:
```python
def _system_profiler_status(self) -> str:
    path = shutil.which("system_profiler") or "/usr/sbin/system_profiler"
    if not Path(path).exists():
        return "unavailable: system_profiler not found (macOS-only built-in)"
    # Quick smoke — `system_profiler -listDataTypes` is fast (<1s)
    try:
        res = subprocess.run(
            [path, "-listDataTypes"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    if res.returncode != 0:
        return f"error: system_profiler -listDataTypes exited {res.returncode}"
    # Confirm SPApplicationsDataType is supported
    if "SPApplicationsDataType" not in (res.stdout or ""):
        return "degraded: SPApplicationsDataType not advertised"
    return "ok"
```

7. **Update the class docstring** to reflect M5.3 (currently says M5.2). Add INVENTORY to the capability list and mention `inventory()` returns MacOSInventory.

### Step 4: Run, expect 3 PASS + no regressions

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v
PYTHONPATH=$(pwd)/core python3 -m pytest adapters/macos/tests/ -q
```

(Some pre-existing M5.2-era tests may need adaptation — e.g. one that asserted `inventory() is None` would now need updating. Treat any such adaptation as legitimate state evolution; do NOT silent-delete assertions.)

### Step 5: Smoke `python -m ascendo doctor` on the host Mac

```bash
PYTHONPATH=$(pwd)/core python3 -m ascendo doctor
```

Expect: capabilities line includes `INVENTORY`. New `system_profiler` line shows `ok`.

### Step 6: Commit

```bash
git add adapters/macos/ascendo_macos/adapter.py
git add adapters/macos/tests/test_adapter_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): wire MacOSInventory into MacOSAdapter (M5.3.5)

Capability flag flipped: PACKAGE_MANAGEMENT | ELEVATION | INVENTORY
  (was PACKAGE_MANAGEMENT | ELEVATION).

inventory() returns a cached MacOSInventory singleton (lazy init in
_cached_inventory).

health_check() now reports a `system_profiler` component line —
`ok`, `degraded` if SPApplicationsDataType not advertised, or
`unavailable` if the binary is missing (which would mean macOS
itself is broken).

Class docstring refreshed to reflect M5.3 scope.

Tests extended with 3 new wiring assertions; pre-existing M5.2-era
tests adapted to reflect the new state.

Refs docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §3
EOF
)"
```

---

## Task 6: `bin/validate-macos.sh` Stage 9 — real-hardware inventory probe

**Files:**
- Modify: `bin/validate-macos.sh`

### Step 1: Read existing structure

```bash
grep -n "^step \|^==> \|FAIL_COUNT\|MAS_AVAILABLE\|DASHBOARD_PORT" bin/validate-macos.sh
```

Note where Stage 8 ends and where the final summary lives. Stage 9 inserts immediately before the summary.

### Step 2: Append Stage 9 block

Insert above the final `ALL CHECKS PASSED` summary:

```bash
# ============================================================
# Stage 9 — LaunchServices inventory (M5.3)
# ============================================================
section "9. LaunchServices inventory (M5.3)"

# Step 9.1 — doctor reports system_profiler component
step "9.1 doctor: system_profiler component"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo doctor \
        | grep -qE '^\s+system_profiler\s+(ok|degraded|unavailable|error)'; then
    PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -m ascendo doctor \
        | grep -E '^\s+system_profiler\s+'
    result PASS "9.1 doctor: system_profiler component"
else
    result FAIL "9.1 doctor: system_profiler component"
fi

# Step 9.2 — direct list.sh invocation (real system_profiler)
step "9.2 inventory list.sh end-to-end"
INV_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-validate-inv-XXXXXX")"
INV_RID="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
if bash "$REPO_ROOT/adapters/macos/scripts/inventory/list.sh" \
        --run-id "$INV_RID" --trigger cli --profile default \
        --output-dir "$INV_DIR" >/dev/null 2>&1; then
    SC="$INV_DIR/$INV_RID/check__inventory.json"
    if [ -f "$SC" ]; then
        ITEM_COUNT="$(jq '.items | length' "$SC")"
        if [ "$ITEM_COUNT" -ge 50 ]; then
            result PASS "9.2 inventory list.sh end-to-end ($ITEM_COUNT apps)"
        else
            result FAIL "9.2 inventory list.sh end-to-end ($ITEM_COUNT apps; expected >=50)"
        fi
    else
        result FAIL "9.2 inventory list.sh end-to-end (no sidecar produced)"
    fi
else
    result FAIL "9.2 inventory list.sh end-to-end (script exit non-zero)"
fi

# Step 9.3 — classification distribution sanity (>= 5 SYSTEM, >= 5 BREW or WEB, >= 1 MAS)
step "9.3 classification distribution"
if [ -f "$SC" ]; then
    SYS_N="$(jq '[.items[] | select(.source.type=="system")] | length' "$SC")"
    MAS_N="$(jq '[.items[] | select(.source.type=="mas")] | length' "$SC")"
    BREW_N="$(jq '[.items[] | select(.source.type=="brew")] | length' "$SC")"
    WEB_N="$(jq '[.items[] | select(.source.type=="web")] | length' "$SC")"
    if [ "$SYS_N" -ge 5 ] && [ "$MAS_N" -ge 1 ] && [ "$((BREW_N + WEB_N))" -ge 5 ]; then
        result PASS "9.3 classification distribution (system=$SYS_N mas=$MAS_N brew=$BREW_N web=$WEB_N)"
    else
        result FAIL "9.3 classification distribution (system=$SYS_N mas=$MAS_N brew=$BREW_N web=$WEB_N; want SYS>=5 MAS>=1 BREW+WEB>=5)"
    fi
else
    result FAIL "9.3 classification distribution (no sidecar)"
fi

# Step 9.4 — Python wrapper end-to-end via the adapter
step "9.4 MacOSAdapter.inventory().list_installed()"
if PYTHONPATH="$REPO_ROOT/core:$REPO_ROOT/adapters/macos" python3 -c "
from ascendo_macos.adapter import MacOSAdapter
a = MacOSAdapter()
host = a.detect_host()
inv = a.inventory()
pkgs = inv.list_installed(host)
print(f'inventory enumerated {len(pkgs)} packages')
assert len(pkgs) >= 50, f'too few packages: {len(pkgs)}'
" 2>&1 | tee /tmp/_inv_py_out; then
    result PASS "9.4 MacOSAdapter.inventory() end-to-end"
else
    cat /tmp/_inv_py_out
    result FAIL "9.4 MacOSAdapter.inventory() end-to-end"
fi
```

(Adapt `step()` / `result()` / `section()` calls to whatever helpers the script actually defines — check the existing stages 1-3 + 8 first.)

### Step 3: Run validate-macos.sh

```bash
bash bin/validate-macos.sh
```

Expect: every Stage 9 sub-step PASS; final `ALL CHECKS PASSED.` with bumped count.

### Step 4: Commit

```bash
git add bin/validate-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): validate-macos.sh Stage 9 — LaunchServices inventory (M5.3.6)

Real-hardware probes for the M5.3 inventory:

  Step 9.1   doctor reports `system_profiler` component
  Step 9.2   bash scripts/inventory/list.sh end-to-end (>=50 apps)
  Step 9.3   classification distribution sanity
             (SYS>=5, MAS>=1, BREW+WEB>=5)
  Step 9.4   MacOSAdapter.inventory().list_installed() end-to-end

No skip gating — system_profiler is built-in macOS; this stage is
expected to run on every Mac. The "expected >=50 apps" lower bound
is generous; even a fresh-install Mac has ~80 /System apps.

Refs docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md §9
EOF
)"
```

---

## Task 7: `bin/run-tag-release-macos.sh` — tag bump v0.0.9-alpha → v0.0.10-alpha

Inventory has no apply phase, so no new flag is needed. Single change: bump the tag everywhere.

**Files:**
- Modify: `bin/run-tag-release-macos.sh`

### Step 1: Find all v0.0.9-alpha occurrences

```bash
grep -n "v0\.0\.9" bin/run-tag-release-macos.sh
```

### Step 2: Replace all with v0.0.10-alpha

Bump tag string + tag message:

```bash
TAG_NAME -> v0.0.10-alpha
tag message -> "macOS adapter M5.3 — LaunchServices inventory; v0.0.10-alpha (apply RC=$APPLY_RC)"
header comments mentioning v0.0.9-alpha as exit bar
```

Use `sed -i ''` (BSD sed) or `Edit` tool with `replace_all` on the same `0.0.9-alpha` -> `0.0.10-alpha` substring.

### Step 3: Verify zero v0.0.9 references remain

```bash
grep -n "v0\.0\.9" bin/run-tag-release-macos.sh   # expect zero hits
bash -n bin/run-tag-release-macos.sh
bash bin/run-tag-release-macos.sh --what-if       # smoke-test (no mutation)
```

### Step 4: Commit

```bash
git add bin/run-tag-release-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): run-tag-release-macos.sh tag bump v0.0.10-alpha (M5.3.7)

M5.3 (LaunchServices inventory) has no apply phase — no new flag.
Single change: tag bumped from v0.0.9-alpha to v0.0.10-alpha.

Tag message now reflects M5.3 scope:
  "macOS adapter M5.3 — LaunchServices inventory; v0.0.10-alpha"
EOF
)"
```

---

## Task 8: Real-hardware validation + tag v0.0.10-alpha + HANDOFF + PLAN + push

Operator-driven. Run validate-macos.sh, run run-tag-release-macos.sh, verify the tag, then update docs and push.

**Files:**
- Modify: `HANDOFF.md` (prepend Sesja 22 entry)
- Modify: `PLAN.md` (M5.3 row → done)

### Step 1: Operator runs validate-macos.sh + run-tag-release-macos.sh

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e
bash bin/validate-macos.sh                    # expect ALL CHECKS PASSED with new Stage 9
bash bin/run-tag-release-macos.sh             # NO --mas needed (M5.3 has no apply)
                                              # confirm 'apply' at brew gate
git tag -l v0.0.10-alpha                      # confirm tag created
```

### Step 2: Prepend Sesja 22 entry to HANDOFF.md

Insert immediately AFTER the intro blockquote, BEFORE `## Sesja 21`:

```markdown
## Sesja 22 (2026-05-04) — macOS adapter M5.3: LaunchServices inventory + v0.0.10-alpha

Third milestone of the macOS adapter. The dashboard Categories tab on
macOS now populates with the real installed-apps list, classified into
SourceType.{SYSTEM, MAS, BREW, WEB}. Tag `v0.0.10-alpha` created locally.

### Architecture confirmed end-to-end

- Layer 4 core unchanged except for `SourceType.SYSTEM` enum addition.
- `MacOSAdapter.capabilities` now `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY`.
  `inventory()` returns a cached `MacOSInventory` singleton.
- `bin/validate-macos.sh` Stage 9 (LaunchServices) prints all green
  with NN apps enumerated and a sensible classification distribution.
- Dashboard `/inventory*` routes (pre-existing) start serving real
  data — no dashboard code changes required.

### Files added (per M5.3.x sub-milestone)

- `core/ascendo/models/package.py` — added `SourceType.SYSTEM` (M5.3.1)
- `docs/architecture/schemas/sidecar.v1.schema.json` — regenerated (M5.3.1)
- `adapters/macos/tests/fixtures/system_profiler_apps.json` — fixture (M5.3.2)
- `adapters/macos/scripts/inventory/list.sh` — bash list script (M5.3.3)
- `adapters/macos/ascendo_macos/inventory.py` — `MacOSInventory` (M5.3.4)
- `adapters/macos/ascendo_macos/adapter.py` — capabilities flip + inventory wire (M5.3.5)
- `bin/validate-macos.sh` — Stage 9 added (M5.3.6)
- `bin/run-tag-release-macos.sh` — tag bump (M5.3.7)

Total: ~6 list.sh tests + ~8 inventory.py tests + ~3 adapter wiring +
1 SourceType test = **~18 new tests** + Stage 9 e2e (4 sub-steps).

### Real apply trace

(Paste the actual `==> [Stage 7] tag` block from run-tag-release-macos.sh
output here so future readers can see the live exit code distribution.)

### What's next (M5.4+, separate specs)

- **M5.4** — `softwareupdate` manager (the `-R` flag rule) + Time
  Machine read-only `ISnapshot`.
- **M5.5** — `launchd` `IScheduler`. After this, tag `v0.2.0` (full M5).
- **M5.3.x follow-ups (deferred during M5.3)**:
  `ascendo inventory list` CLI subcommand; per-app upgrade-availability
  via inventory (today managers handle this); iPad-app upgrade
  automation (Track 2 from M5.2).

### Spec + plan

- `docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md`
- `docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md`
```

### Step 3: Flip M5.3 row in PLAN.md

Find the M5 milestone table row for M5.3 (currently `⏳ pending`). Replace with:

```markdown
| **M5.3** | ✅ done (2026-05-04, **v0.0.10-alpha**) | `MacOSInventory` populates dashboard Categories tab via `system_profiler -json -detailLevel mini SPApplicationsDataType` + 5-rule classification (SYSTEM/MAS/BREW/WEB). ~18 new tests + Stage 9 e2e via `validate-macos.sh`. Spec/plan: `docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md` + `docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md`. See HANDOFF.md Sesja 22. |
```

Bump the "Last updated" line at the top of PLAN.md:

```markdown
> Last updated: 2026-05-04 (sesja 22) — macOS adapter M5.3 shipped (LaunchServices inventory, v0.0.10-alpha).
```

In the per-manager scope table, flip the `launchservices.py` row to `✅ M5.3`.

### Step 4: Commit + push

```bash
git add HANDOFF.md PLAN.md
git commit -m "$(cat <<'EOF'
docs: HANDOFF Sesja 22 + PLAN M5.3 done (v0.0.10-alpha)

macOS adapter M5.3 complete on real Mac:
  - LaunchServices inventory via system_profiler -json
  - 5-rule classification (SYSTEM/MAS/BREW/WEB)
  - dashboard Categories tab populates with real installed-apps list
  - v0.0.10-alpha tagged locally
  - validate-macos.sh Stage 9 green
  - ~18 new tests + ~109+ macOS adapter tests green

Next: M5.4 — softwareupdate manager + Time Machine read-only ISnapshot.
EOF
)"

git push origin claude/musing-herschel-b52e7e
git push origin v0.0.10-alpha
```

### Step 5: Final regression check

```bash
PYTHONPATH=$(pwd)/core python3 -m pytest tests/contract/ adapters/macos/tests/ -q
```

Expect: all green (modulo 9 pre-existing test_service_endpoints.py failures).

The branch is now ready for review + merge to `main`.

---

## Self-review notes (addressed inline)

1. **Spec coverage** — every spec section maps:
   - §1 Goal → Tasks 1-7 collectively
   - §2 Architecture → Tasks 3 (bash), 4 (Python), 5 (adapter wire)
   - §3 Capability flag → Task 5
   - §4 Enumeration → Task 3
   - §5 Source classification → Task 3 + Task 2 fixture
   - §6 Per-app metadata → Task 3 + Task 4 sidecar parse
   - §7 Caching → no implementation (deliberate; spec says "Python wrapper does NOT cache")
   - §8 CLI surface → no work (spec defers `ascendo inventory list`)
   - §9 Tests → Tasks 3-5 (test files)
   - §10 Threat model → Task 3 (no shell injection, argv-only)
   - §11 Deferred → noted in HANDOFF Sesja 22 (Task 8)
   - §12 Tag exit bar → Tasks 6, 7, 8

2. **Type consistency**:
   - `MacOSInventory.list_installed(host, *, categories=None)` — matches IInventory ABC.
   - `Package` constructor in Task 4 follows `core/ascendo/models/package.py` (engineer must verify exact field names before pasting).
   - `SCRIPT_REL` constant used identically across Task 4 + Task 5.

3. **Placeholder scan** — no TBD/TODO; every step has executable commands or code.

4. **Task 4 step 3 vagueness**: the engineer is told to "match WindowsInventory" rather than given the full code. This is intentional because the file is ~250 LOC and rotting it inline would be more error-prone than reading the live reference. The test file is concrete enough that any structural drift will fail.
