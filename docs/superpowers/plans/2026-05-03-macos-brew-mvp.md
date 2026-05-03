# macOS adapter — M5.1 brew MVP implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `python -m ascendo run --category brew --phase {check|plan|apply|verify|cleanup}` working end-to-end on macOS, with a real `brew upgrade` performed and `v0.0.8-alpha` tagged.

**Architecture:** Mirrors the Windows v0.0.7-alpha pattern. Layer 4 core is unchanged (already OS-agnostic). New Layer 5 (`MacOSAdapter` + `BrewManager` Python) wraps new Layer 6 (5 bash phase scripts + `ascendo_json.sh` + `ascendo_brew.sh` + `_json_emit.py` Python helper). Sidecar emitter is a hybrid Bash + Python helper pattern matching the existing `lib/json.sh` + `lib/_json_emit.py` already in the repo at root level. Schema flipped from legacy `ubuntu-aktualizacje/v1` to canonical `ascendo/v1`.

**Tech Stack:** Python 3.11+ (Pydantic v2), Bash 3.2+ (macOS system shell), Homebrew 4.x (`brew outdated --json=v2`), `jq`. No new core dependencies. Tests: pytest (mock-based unit) + bash on macOS (real-hardware via `bin/validate-macos.sh`).

**Branch:** `claude/quizzical-sanderson-6a5664` (current worktree). Merge to `main` is the last task.

**Spec reference:** [docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md](../specs/2026-05-03-macos-brew-mvp-design.md)

---

## Task 1: Add `SourceType.BREW` enum value + verify scaffolding

The existing `SourceType` enum has `BREW_FORMULA` and `BREW_CASK` but no manager-level `BREW`. Per spec §10, Windows pattern is `manager.category == item.source.type` (one value per manager); adding `BREW` is the one-line core change M5.1 needs.

**Files:**
- Modify: `core/ascendo/models/package.py` (add one enum line)
- Test: `tests/contract/test_sidecar_v1.py` (add fixture for BREW source)
- Verify (no edit): `core/ascendo/adapter_factory/__init__.py` already has `OperatingSystem.MACOS: "macos"` (line 68) and `"macos": ("ascendo_macos", "MacOSAdapter")` (line 77).

- [ ] **Step 1: Write failing test asserting `SourceType.BREW` exists**

Append to `tests/contract/test_sidecar_v1.py`:

```python
def test_source_type_has_brew_value() -> None:
    """BrewManager.category == SourceType.BREW. Required by M5.1."""
    from ascendo.models.package import SourceType
    assert SourceType.BREW.value == "brew"
    # BREW_FORMULA / BREW_CASK retained for item-level namespace tagging.
    assert SourceType.BREW_FORMULA.value == "brew_formula"
    assert SourceType.BREW_CASK.value == "brew_cask"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/quizzical-sanderson-6a5664
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_brew_value -v
```

Expected: `FAILED` with `AttributeError: BREW`.

- [ ] **Step 3: Add the enum value**

In `core/ascendo/models/package.py`, locate the `SourceType` class (line 22). Insert one line in the brew block:

```python
    BREW_FORMULA = "brew_formula"
    BREW_CASK = "brew_cask"
    BREW = "brew"                 # manager-level category for BrewManager (covers both)
```

- [ ] **Step 4: Run the test to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_brew_value -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run full contract suite to confirm no regressions**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/ -q
```

Expected: all green (regression check — adding an enum value should be additive only).

- [ ] **Step 6: Commit**

```bash
git add core/ascendo/models/package.py tests/contract/test_sidecar_v1.py
git commit -m "$(cat <<'EOF'
feat(core): add SourceType.BREW for macOS adapter

M5.1 prerequisite. BrewManager.category will be SourceType.BREW
(covers both formulae + casks in one manager, mirroring how
WingetManager covers all winget-feed sources). BREW_FORMULA and
BREW_CASK retained for item-level namespace tagging within a
brew sidecar.

Refs docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md
EOF
)"
```

---

## Task 2: Port `lib/_json_emit.py` to `adapters/macos/lib/_json_emit.py` (schema flip)

The legacy `lib/_json_emit.py` (289 LOC, repo root) emits `ubuntu-aktualizacje/v1` sidecars with legacy field names (`kind`, bare `host` string, `summary.{ok,warn,err}`, `items[].{from,to,result}`). The macOS adapter needs the same Python helper but emitting `ascendo/v1` with canonical field names. Atomic write + buffered subcommand model retained.

**Files:**
- Read (reference): `lib/_json_emit.py` (root, 289 LOC), `core/ascendo/models/sidecar.py` (canonical schema)
- Create: `adapters/macos/lib/_json_emit.py` (~330 LOC ported + adjusted)
- Test: `adapters/macos/tests/test_json_emit_smoke.py` (new file)

- [ ] **Step 1: Write failing tests for the helper's CLI surface**

Create `adapters/macos/tests/test_json_emit_smoke.py`:

```python
"""Smoke tests for adapters/macos/lib/_json_emit.py.

The helper is invoked as `python3 _json_emit.py <subcommand> ...` from
ascendo_json.sh. Tests cover the round-trip: init → add-item → finalize
produces a sidecar that parse_sidecar() accepts as ascendo/v1.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
HELPER = ADAPTER_ROOT / "lib" / "_json_emit.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def test_helper_exists() -> None:
    assert HELPER.is_file(), f"missing helper at {HELPER}"


def test_init_creates_buffer(tmp_path: Path) -> None:
    bufdir = tmp_path / "buf"
    res = _run(
        [
            "init",
            "--bufdir", str(bufdir),
            "--phase", "check",
            "--category", "brew",
            "--run-id", "00000000-0000-0000-0000-000000000001",
            "--trigger", "cli",
            "--profile-name", "default",
            "--tool-name", "brew",
            "--tool-version", "4.4.0",
            "--started-at", "2026-05-03T12:00:00Z",
        ],
        cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    assert (bufdir / "meta.json").is_file()
    meta = json.loads((bufdir / "meta.json").read_text())
    assert meta["schema"] == "ascendo/v1"
    assert meta["phase"] == "check"
    assert meta["category"] == "brew"


def test_add_item_appends(tmp_path: Path) -> None:
    bufdir = tmp_path / "buf"
    _run([
        "init", "--bufdir", str(bufdir),
        "--phase", "check", "--category", "brew",
        "--run-id", "00000000-0000-0000-0000-000000000001",
        "--trigger", "cli", "--profile-name", "default",
        "--tool-name", "brew", "--tool-version", "4.4.0",
        "--started-at", "2026-05-03T12:00:00Z",
    ], cwd=tmp_path)

    res = _run([
        "add-item", "--bufdir", str(bufdir),
        "--id", "node",
        "--current-version", "20.10.0",
        "--target-version", "21.0.0",
        "--status", "planned",
        "--source-type", "brew",
        "--source-feed", "formula",
    ], cwd=tmp_path)
    assert res.returncode == 0, res.stderr

    items_jsonl = (bufdir / "items.jsonl").read_text().strip().splitlines()
    assert len(items_jsonl) == 1
    item = json.loads(items_jsonl[0])
    assert item["id"] == "node"
    assert item["current_version"] == "20.10.0"
    assert item["target_version"] == "21.0.0"
    assert item["status"] == "planned"
    assert item["source"]["type"] == "brew"
    assert item["source"]["feed"] == "formula"


def test_finalize_round_trips_through_pydantic(tmp_path: Path) -> None:
    """Finalized sidecar is accepted by parse_sidecar() as ascendo/v1."""
    bufdir = tmp_path / "buf"
    out = tmp_path / "check__brew.json"

    _run([
        "init", "--bufdir", str(bufdir),
        "--phase", "check", "--category", "brew",
        "--run-id", "00000000-0000-0000-0000-000000000001",
        "--trigger", "cli", "--profile-name", "default",
        "--tool-name", "brew", "--tool-version", "4.4.0",
        "--started-at", "2026-05-03T12:00:00Z",
        "--host-name", "macbook.local",
        "--host-os", "macos",
        "--host-os-version", "14.5",
        "--host-arch", "arm64",
        "--host-user", "mk",
        "--host-is-elevated", "false",
    ], cwd=tmp_path)

    _run([
        "add-item", "--bufdir", str(bufdir),
        "--id", "node",
        "--current-version", "20.10.0",
        "--target-version", "21.0.0",
        "--status", "planned",
        "--source-type", "brew",
        "--source-feed", "formula",
    ], cwd=tmp_path)

    res = _run([
        "finalize", "--bufdir", str(bufdir),
        "--out", str(out),
        "--exit-code", "0",
        "--ended-at", "2026-05-03T12:00:01Z",
    ], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert out.is_file()

    # Round-trip through parse_sidecar
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(out.read_text())
    finally:
        sys.path.pop(0)

    assert sc.schema_id.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_json_emit_smoke.py -v
```

Expected: all FAIL with `missing helper at adapters/macos/lib/_json_emit.py`.

- [ ] **Step 3: Implement the helper**

Create `adapters/macos/lib/_json_emit.py`. Use the legacy `lib/_json_emit.py` (root) as the structural template — keep the buffer-directory pattern (meta.json + items.jsonl + counters.env) but rename fields to `ascendo/v1`:

```python
#!/usr/bin/env python3
"""Helper for adapters/macos/lib/ascendo_json.sh — phase-result sidecar buffer.

Subcommands:
  init        --bufdir DIR --phase PHASE --category CAT --run-id ID
              --trigger TRIG --profile-name NAME --tool-name TOOL
              --tool-version VER --started-at ISO
              [--host-name STR] [--host-os STR] [--host-os-version STR]
              [--host-arch STR] [--host-user STR]
              [--host-is-elevated true|false]
              [--host-elevation-method STR] [--host-locale STR]
  add-item    --bufdir DIR --id ID --status STATUS
              [--current-version STR] [--target-version STR]
              [--resolved-version STR] [--source-type STR]
              [--source-feed STR] [--source-url STR]
              [--exit-code N] [--duration-ms N] [--note STR]
  add-message --bufdir DIR --level LEVEL --text STR [--code STR]
  set-flag    --bufdir DIR --key needs_reboot --value true|false
  count       --bufdir DIR --bucket {success,skipped,failed} [--n N]
  finalize    --bufdir DIR --out PATH --exit-code N --ended-at ISO

Buffer layout (compatible with the legacy emitter):
  meta.json    -- header set by init
  items.jsonl  -- one JSON object per line
  msgs.jsonl   -- one JSON object per line
  counters.env -- KEY=VAL lines for success/skipped/failed/needs_reboot

Atomic finalize writes via tempfile + os.replace.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCHEMA_ID = "ascendo/v1"
VALID_PHASES = {"check", "plan", "apply", "verify", "cleanup"}
VALID_STATUSES = {"success", "skipped", "failed", "planned", "up_to_date", "partial"}
VALID_LEVELS = {"info", "warn", "error"}
COUNTER_BUCKETS = {"success", "skipped", "failed"}


def _bufdir(arg: str) -> Path:
    p = Path(arg)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _read_counters(path: Path) -> dict[str, int | bool]:
    out: dict[str, int | bool] = {
        "success": 0, "skipped": 0, "failed": 0, "needs_reboot": False,
    }
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in {"success", "skipped", "failed"}:
            out[k] = int(v)
        elif k == "needs_reboot":
            out[k] = v.lower() in {"true", "1", "yes"}
    return out


def _write_counters(path: Path, counters: dict[str, int | bool]) -> None:
    lines = [
        f"success={counters['success']}",
        f"skipped={counters['skipped']}",
        f"failed={counters['failed']}",
        f"needs_reboot={'true' if counters['needs_reboot'] else 'false'}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    if args.phase not in VALID_PHASES:
        print(f"init: invalid phase {args.phase!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)

    host = {
        "hostname": args.host_name or "unknown",
        "os": args.host_os or "macos",
        "os_version": args.host_os_version or "unknown",
        "arch": args.host_arch or "unknown",
        "user": args.host_user or "unknown",
        "is_elevated": (args.host_is_elevated or "false").lower() == "true",
    }
    if args.host_elevation_method:
        host["elevation_method"] = args.host_elevation_method
    if args.host_locale:
        host["locale"] = args.host_locale

    meta = {
        "schema": SCHEMA_ID,
        "phase": args.phase,
        "category": args.category,
        "run": {
            "id": args.run_id,
            "trigger": args.trigger,
            "profile": args.profile_name,
            "dry_run": (args.dry_run or "false").lower() == "true",
            "started_at": args.started_at,
        },
        "host": host,
        "tool": {
            "name": args.tool_name,
            "version": args.tool_version,
        },
    }
    (bufdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (bufdir / "items.jsonl").write_text("", encoding="utf-8")
    (bufdir / "msgs.jsonl").write_text("", encoding="utf-8")
    _write_counters(bufdir / "counters.env",
                    {"success": 0, "skipped": 0, "failed": 0, "needs_reboot": False})
    return 0


def cmd_add_item(args: argparse.Namespace) -> int:
    if args.status not in VALID_STATUSES:
        print(f"add-item: invalid status {args.status!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)

    item: dict = {
        "id": args.id,
        "status": args.status,
    }
    if args.current_version: item["current_version"] = args.current_version
    if args.target_version:  item["target_version"]  = args.target_version
    if args.resolved_version: item["resolved_version"] = args.resolved_version
    if args.source_type:
        src: dict = {"type": args.source_type}
        if args.source_feed: src["feed"] = args.source_feed
        if args.source_url:  src["url"]  = args.source_url
        item["source"] = src
    if args.exit_code is not None:    item["exit_code"]    = args.exit_code
    if args.duration_ms is not None:  item["duration_ms"]  = args.duration_ms
    if args.note:                     item["note"]         = args.note

    with (bufdir / "items.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Auto-tally counters for success/skipped/failed (mirrors legacy)
    counters = _read_counters(bufdir / "counters.env")
    if args.status in COUNTER_BUCKETS:
        counters[args.status] += 1
        _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_add_message(args: argparse.Namespace) -> int:
    if args.level not in VALID_LEVELS:
        print(f"add-message: invalid level {args.level!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)
    msg: dict = {"level": args.level, "text": args.text}
    if args.code: msg["code"] = args.code
    with (bufdir / "msgs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return 0


def cmd_set_flag(args: argparse.Namespace) -> int:
    bufdir = _bufdir(args.bufdir)
    counters = _read_counters(bufdir / "counters.env")
    if args.key == "needs_reboot":
        counters["needs_reboot"] = args.value.lower() in {"true", "1", "yes"}
    else:
        print(f"set-flag: unknown key {args.key!r}", file=sys.stderr)
        return 2
    _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    if args.bucket not in COUNTER_BUCKETS:
        print(f"count: invalid bucket {args.bucket!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)
    counters = _read_counters(bufdir / "counters.env")
    counters[args.bucket] += args.n
    _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    bufdir = _bufdir(args.bufdir)
    meta_path = bufdir / "meta.json"
    if not meta_path.exists():
        print("finalize: no meta.json — call init first", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    items = _read_jsonl(bufdir / "items.jsonl")
    msgs = _read_jsonl(bufdir / "msgs.jsonl")
    counters = _read_counters(bufdir / "counters.env")

    total = len(items)
    success = int(counters["success"])
    skipped = int(counters["skipped"])
    failed = int(counters["failed"])
    # Items not in success/skipped/failed (e.g. status=planned) count toward total
    # but not toward the result buckets.

    # Status heuristic mirrors AscendoJson.psm1 Save-Sidecar:
    # - any failed → "failed"
    # - all skipped → "skipped"
    # - else → "success"
    if failed > 0:
        status = "failed"
    elif total > 0 and skipped == total:
        status = "skipped"
    else:
        status = "success"

    sidecar = {
        **meta,
        "finished_at": args.ended_at,
        "exit_code": args.exit_code,
        "status": status,
        "summary": {
            "total": total,
            "success": success,
            "skipped": skipped,
            "failed": failed,
        },
        "items": items,
        "messages": msgs,
        "needs_reboot": bool(counters["needs_reboot"]),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp file in same dir → os.replace
    fd, tmp = tempfile.mkstemp(prefix=".sidecar_", dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, out_path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="_json_emit", description="ascendo/v1 sidecar emitter helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--bufdir", required=True)
    pi.add_argument("--phase", required=True)
    pi.add_argument("--category", required=True)
    pi.add_argument("--run-id", required=True)
    pi.add_argument("--trigger", required=True)
    pi.add_argument("--profile-name", required=True)
    pi.add_argument("--tool-name", required=True)
    pi.add_argument("--tool-version", required=True)
    pi.add_argument("--started-at", required=True)
    pi.add_argument("--dry-run", default="false")
    pi.add_argument("--host-name")
    pi.add_argument("--host-os")
    pi.add_argument("--host-os-version")
    pi.add_argument("--host-arch")
    pi.add_argument("--host-user")
    pi.add_argument("--host-is-elevated")
    pi.add_argument("--host-elevation-method")
    pi.add_argument("--host-locale")
    pi.set_defaults(func=cmd_init)

    pa = sub.add_parser("add-item")
    pa.add_argument("--bufdir", required=True)
    pa.add_argument("--id", required=True)
    pa.add_argument("--status", required=True)
    pa.add_argument("--current-version")
    pa.add_argument("--target-version")
    pa.add_argument("--resolved-version")
    pa.add_argument("--source-type")
    pa.add_argument("--source-feed")
    pa.add_argument("--source-url")
    pa.add_argument("--exit-code", type=int)
    pa.add_argument("--duration-ms", type=int)
    pa.add_argument("--note")
    pa.set_defaults(func=cmd_add_item)

    pm = sub.add_parser("add-message")
    pm.add_argument("--bufdir", required=True)
    pm.add_argument("--level", required=True)
    pm.add_argument("--text", required=True)
    pm.add_argument("--code")
    pm.set_defaults(func=cmd_add_message)

    pf = sub.add_parser("set-flag")
    pf.add_argument("--bufdir", required=True)
    pf.add_argument("--key", required=True)
    pf.add_argument("--value", required=True)
    pf.set_defaults(func=cmd_set_flag)

    pc = sub.add_parser("count")
    pc.add_argument("--bufdir", required=True)
    pc.add_argument("--bucket", required=True)
    pc.add_argument("--n", type=int, default=1)
    pc.set_defaults(func=cmd_count)

    pe = sub.add_parser("finalize")
    pe.add_argument("--bufdir", required=True)
    pe.add_argument("--out", required=True)
    pe.add_argument("--exit-code", type=int, required=True)
    pe.add_argument("--ended-at", required=True)
    pe.set_defaults(func=cmd_finalize)

    return p


def main(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
```

Make it executable:

```bash
chmod +x adapters/macos/lib/_json_emit.py
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_json_emit_smoke.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/lib/_json_emit.py adapters/macos/tests/test_json_emit_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos/lib): _json_emit.py — ascendo/v1 sidecar emitter helper (M5.1.1)

Ports the buffer-directory subcommand pattern from lib/_json_emit.py
(repo root, ubuntu-aktualizacje/v1) into adapters/macos/lib/ with the
schema flipped to ascendo/v1 and field names canonicalised:

  kind                       → phase
  bare host string           → HostInfo object (hostname/os/os_version/
                               arch/user/is_elevated/elevation_method/locale)
  summary.{ok,warn,err}      → summary.{success,skipped,failed}
  items[].{from,to,result}   → items[].{current_version,target_version,status}

Subcommands: init / add-item / add-message / set-flag / count / finalize.
Atomic write on finalize via tempfile + os.replace. 4 round-trip tests
through parse_sidecar() verify ascendo/v1 schema compliance.

Bash 3.2-safe — invoked from ascendo_json.sh as a subprocess (per-call
state persisted to bufdir; no Python long-running daemon).

Refs spec §5.
EOF
)"
```

---

## Task 3: Bash JSON wrapper — `adapters/macos/lib/ascendo_json.sh`

The bash side of the emitter. Mirrors `lib/json.sh` (root) but renamed function names and points at the new helper. Each function is a thin shim around `python3 _json_emit.py`.

**Files:**
- Read (reference): `lib/json.sh` (root, 150 LOC)
- Create: `adapters/macos/lib/ascendo_json.sh` (~180 LOC)
- Test: `adapters/macos/tests/test_ascendo_json_smoke.sh` (bash-side smoke; pytest wrapper invokes it)
- Test: `adapters/macos/tests/test_ascendo_json_wrapper.py` (pytest harness)

- [ ] **Step 1: Write failing test (pytest harness for bash smoke)**

Create `adapters/macos/tests/test_ascendo_json_wrapper.py`:

```python
"""Smoke test that ascendo_json.sh round-trips through parse_sidecar()."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ADAPTER_ROOT / "lib" / "ascendo_json.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_wrapper_exists() -> None:
    assert WRAPPER.is_file(), f"missing {WRAPPER}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_wrapper_round_trip(tmp_path: Path) -> None:
    """Source the wrapper, init/add/save, parse the result through Pydantic."""
    out_dir = tmp_path / "runs"
    run_id = "00000000-0000-0000-0000-000000000042"
    script = f'''
        set -o pipefail
        export TMPDIR="{tmp_path}"
        . "{WRAPPER}"
        json_init "check" "brew" "{run_id}" "cli" "default" \
                  "brew" "4.4.0" \
                  "macbook.local" "macos" "14.5" "arm64" "mk" "false"
        json_add_item "node" "20.10.0" "21.0.0" "planned" "brew" "formula"
        json_add_message "info" "test message"
        json_save "{out_dir}"
    '''
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, f"wrapper failed: {res.stderr}\n{res.stdout}"

    sidecar_path = out_dir / run_id / "check__brew.json"
    assert sidecar_path.is_file(), f"missing {sidecar_path}"

    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)

    assert sc.schema_id.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_json_wrapper.py -v
```

Expected: FAIL — `missing lib/ascendo_json.sh`.

- [ ] **Step 3: Implement the wrapper**

Create `adapters/macos/lib/ascendo_json.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_json.sh — bash wrapper for ascendo/v1 emitter
# =============================================================================
# Sourced by phase scripts:
#     . "$ADAPTER_LIB/ascendo_json.sh"
#
# API (mirrors AscendoJson.psm1 on Windows where possible):
#     json_init    <phase> <category> <run_id> <trigger> <profile> \
#                  <tool_name> <tool_version> \
#                  <host_name> <host_os> <host_os_version> <host_arch> \
#                  <host_user> <host_is_elevated>
#     json_add_item    <id> <current_version> <target_version> <status> \
#                      [source_type] [source_feed]
#     json_add_message <level> <text> [code]
#     json_set_needs_reboot <true|false>
#     json_count       <bucket: success|skipped|failed> [n]
#     json_save        <output_dir>
#         → writes <output_dir>/<run_id>/<phase>__<category>.json
#         → also exits the trap-cleanup of the bufdir
#
# Bash 3.2-safe. State held in bufdir (tempdir) between calls; no globals
# beyond JSON_BUFDIR / JSON_PHASE / JSON_CATEGORY / JSON_RUN_ID.
# =============================================================================

# shellcheck disable=SC2155
[[ -z "${ASCENDO_JSON_DIR:-}" ]] && ASCENDO_JSON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ASCENDO_JSON_EMIT="${ASCENDO_JSON_DIR}/_json_emit.py"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ascendo_json.sh: python3 is required" >&2
    return 1 2>/dev/null || exit 1
fi
if [[ ! -f "$_ASCENDO_JSON_EMIT" ]]; then
    echo "ascendo_json.sh: missing helper $_ASCENDO_JSON_EMIT" >&2
    return 1 2>/dev/null || exit 1
fi

JSON_BUFDIR=""
JSON_PHASE=""
JSON_CATEGORY=""
JSON_RUN_ID=""
JSON_FINALIZED=0

_json_now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

_json_emit() { python3 "$_ASCENDO_JSON_EMIT" "$@"; }

json_init() {
    # $1=phase $2=category $3=run_id $4=trigger $5=profile_name
    # $6=tool_name $7=tool_version
    # $8=host_name $9=host_os $10=host_os_version $11=host_arch
    # $12=host_user $13=host_is_elevated
    JSON_PHASE="$1"
    JSON_CATEGORY="$2"
    JSON_RUN_ID="$3"
    JSON_BUFDIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo_json_XXXXXX")"
    JSON_FINALIZED=0
    _json_emit init \
        --bufdir "$JSON_BUFDIR" \
        --phase "$1" --category "$2" \
        --run-id "$3" --trigger "$4" --profile-name "$5" \
        --tool-name "$6" --tool-version "$7" \
        --started-at "$(_json_now_utc)" \
        --host-name "${8:-unknown}" \
        --host-os "${9:-macos}" \
        --host-os-version "${10:-unknown}" \
        --host-arch "${11:-unknown}" \
        --host-user "${12:-unknown}" \
        --host-is-elevated "${13:-false}"
}

json_add_item() {
    # $1=id $2=current_version $3=target_version $4=status
    # $5=source_type (optional, default "brew")
    # $6=source_feed (optional, e.g. "formula" or "cask")
    local args=(add-item --bufdir "$JSON_BUFDIR" --id "$1" --status "$4")
    [[ -n "$2" ]] && args+=(--current-version "$2")
    [[ -n "$3" ]] && args+=(--target-version "$3")
    [[ -n "${5:-}" ]] && args+=(--source-type "$5")
    [[ -n "${6:-}" ]] && args+=(--source-feed "$6")
    _json_emit "${args[@]}"
}

json_add_message() {
    # $1=level (info|warn|error) $2=text [$3=code]
    local args=(add-message --bufdir "$JSON_BUFDIR" --level "$1" --text "$2")
    [[ -n "${3:-}" ]] && args+=(--code "$3")
    _json_emit "${args[@]}"
}

json_set_needs_reboot() {
    # $1=true|false
    _json_emit set-flag --bufdir "$JSON_BUFDIR" --key needs_reboot --value "$1"
}

json_count() {
    # $1=bucket (success|skipped|failed) [$2=n]
    local n="${2:-1}"
    _json_emit count --bufdir "$JSON_BUFDIR" --bucket "$1" --n "$n"
}

json_save() {
    # $1=output_dir; final path is <output_dir>/<run_id>/<phase>__<category>.json
    local output_dir="$1"
    local run_dir="$output_dir/$JSON_RUN_ID"
    mkdir -p "$run_dir"
    local out="$run_dir/${JSON_PHASE}__${JSON_CATEGORY}.json"
    local exit_code="${JSON_LAST_EXIT_CODE:-0}"
    _json_emit finalize \
        --bufdir "$JSON_BUFDIR" \
        --out "$out" \
        --exit-code "$exit_code" \
        --ended-at "$(_json_now_utc)"
    JSON_FINALIZED=1
    rm -rf "$JSON_BUFDIR"
}

# Trap helper. Phase scripts can install:
#   trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT
# Saves only if not yet finalized; respects $? as the exit code.
json_save_on_exit() {
    JSON_LAST_EXIT_CODE="$?"
    if [[ "$JSON_FINALIZED" -eq 0 && -n "$JSON_BUFDIR" && -d "$JSON_BUFDIR" ]]; then
        json_save "$1" || true
    fi
    return "$JSON_LAST_EXIT_CODE"
}
```

- [ ] **Step 4: Run test to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_json_wrapper.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/lib/ascendo_json.sh adapters/macos/tests/test_ascendo_json_wrapper.py
git commit -m "$(cat <<'EOF'
feat(macos/lib): ascendo_json.sh — bash wrapper for sidecar emitter (M5.1.1)

Bash 3.2-safe wrapper around adapters/macos/lib/_json_emit.py. Mirrors
the legacy lib/json.sh API but renamed function prefix (json_*) and
positional-arg signatures aligned with the AscendoJson.psm1 surface
on Windows where possible. State held in a per-init bufdir tempdir;
trap-driven save_on_exit handles the orchestrator's expected sidecar-
on-every-code-path contract.

Round-trip pytest test (test_ascendo_json_wrapper.py) sources the
wrapper from a subshell, builds a sidecar, and parses it through
parse_sidecar() to confirm ascendo/v1 schema compliance.

Refs spec §5.
EOF
)"
```

---

## Task 4: Brew helpers — `adapters/macos/lib/ascendo_brew.sh`

Brew-specific helper functions: detect brew binary, run `brew outdated --json=v2`, parse formulae + casks, kill running cask apps, map exit codes. Mirrors `AscendoWinget.psm1` on Windows.

**Files:**
- Create: `adapters/macos/lib/ascendo_brew.sh` (~250 LOC)
- Test: `adapters/macos/tests/test_ascendo_brew_helpers.py`
- Fixture: `adapters/macos/tests/fixtures/brew-outdated.json`

- [ ] **Step 1: Capture a real `brew outdated --json=v2` fixture from this Mac**

```bash
brew update >/dev/null 2>&1 || true
brew outdated --json=v2 > adapters/macos/tests/fixtures/brew-outdated.json
ls -la adapters/macos/tests/fixtures/brew-outdated.json
head -40 adapters/macos/tests/fixtures/brew-outdated.json
```

Expected: a JSON object with `formulae[]` and `casks[]` arrays. (If both empty, run `brew outdated --json=v2 --greedy` for a richer fixture, or hand-craft a minimal one with one formula + one cask.)

If empty even with `--greedy`, write the fixture by hand:

```bash
cat > adapters/macos/tests/fixtures/brew-outdated.json <<'EOF'
{
  "formulae": [
    {
      "name": "node",
      "installed_versions": ["20.10.0"],
      "current_version": "21.6.1",
      "pinned": false,
      "pinned_version": null
    }
  ],
  "casks": [
    {
      "name": ["visual-studio-code"],
      "installed_versions": "1.85.0",
      "current_version": "1.86.2"
    }
  ]
}
EOF
```

- [ ] **Step 2: Write failing test**

Create `adapters/macos/tests/test_ascendo_brew_helpers.py`:

```python
"""Tests for adapters/macos/lib/ascendo_brew.sh helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_brew.sh"
FIXTURE = ADAPTER_ROOT / "tests" / "fixtures" / "brew-outdated.json"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_lib_exists() -> None:
    assert LIB.is_file()


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="bash + jq required",
)
def test_parse_outdated_formulae(tmp_path: Path) -> None:
    """ascendo_brew_parse_outdated emits one CSV row per outdated formula."""
    script = f'''
        set -o pipefail
        . "{LIB}"
        ascendo_brew_parse_outdated "{FIXTURE}" formula
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) >= 1
    # CSV format: id,current_version,target_version
    cols = lines[0].split(",")
    assert len(cols) == 3
    assert cols[0]  # id non-empty


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_resolve_brew_prefix_returns_path(tmp_path: Path) -> None:
    """ascendo_brew_prefix prints a path string (or empty if brew missing)."""
    script = f'. "{LIB}" && ascendo_brew_prefix'
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    # On systems with brew, must print a path; without, must exit 0 + empty.
    assert res.returncode == 0


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_cask_app_name_mapping(tmp_path: Path) -> None:
    """Known casks map to /Applications bundle names."""
    script = f'''
        . "{LIB}"
        ascendo_brew_cask_app_name "visual-studio-code"
        ascendo_brew_cask_app_name "google-chrome"
        ascendo_brew_cask_app_name "totally-unknown-cask"
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0
    lines = res.stdout.splitlines()
    assert lines[0] == "Visual Studio Code"
    assert lines[1] == "Google Chrome"
    assert lines[2] == ""  # unknown → empty
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_brew_helpers.py -v
```

Expected: FAIL — `missing lib/ascendo_brew.sh`.

- [ ] **Step 4: Implement the brew helpers**

Create `adapters/macos/lib/ascendo_brew.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/lib/ascendo_brew.sh — Homebrew helpers for ascendo phase scripts
# =============================================================================
# Sourced by phase scripts:
#     . "$ADAPTER_LIB/ascendo_brew.sh"
#
# Public API:
#     ascendo_brew_prefix              — print "$(brew --prefix)" or empty
#     ascendo_brew_version             — print first line of `brew --version`
#     ascendo_brew_outdated_json [--greedy]
#                                      — print `brew outdated --json=v2 [--greedy]`
#     ascendo_brew_parse_outdated <json_file> <formula|cask>
#                                      — emit CSV rows: id,current_version,target_version
#     ascendo_brew_cask_app_name <cask_token>
#                                      — print app bundle name (e.g. "Slack") or empty
#     ascendo_brew_kill_cask_apps <cask_token>
#                                      — graceful quit + force-kill if needed
#     ascendo_brew_exit_code <brew_exit>
#                                      — translate brew exit to ascendo phase exit
# Bash 3.2-safe. Requires `jq` for parse_outdated.
# =============================================================================

ascendo_brew_prefix() {
    if command -v brew >/dev/null 2>&1; then
        brew --prefix 2>/dev/null
    fi
}

ascendo_brew_version() {
    if command -v brew >/dev/null 2>&1; then
        brew --version 2>/dev/null | head -n1
    fi
}

ascendo_brew_outdated_json() {
    if ! command -v brew >/dev/null 2>&1; then
        echo '{"formulae":[],"casks":[]}'
        return 0
    fi
    local greedy=""
    [[ "${1:-}" == "--greedy" ]] && greedy="--greedy"
    brew outdated --json=v2 $greedy 2>/dev/null
}

# Parse a saved brew-outdated json file (or stdin if "-") and emit CSV rows
# of the form: id,current_version,target_version
# Bucket: "formula" or "cask" (single).
ascendo_brew_parse_outdated() {
    local source="$1" bucket="$2"
    if ! command -v jq >/dev/null 2>&1; then
        echo "ascendo_brew_parse_outdated: jq required" >&2
        return 2
    fi
    local plural
    case "$bucket" in
        formula) plural="formulae" ;;
        cask)    plural="casks" ;;
        *) echo "ascendo_brew_parse_outdated: bucket must be formula|cask" >&2; return 2 ;;
    esac
    local jq_filter='
        .'$plural'[]
        | {
            id:      (if (.name | type == "array") then .name[0] else .name end),
            current: ( .installed_versions
                       | (if type == "array" then .[0] else . end)
                       // "unknown"),
            target:  (.current_version // "unknown")
          }
        | "\(.id),\(.current),\(.target)"
    '
    if [[ "$source" == "-" ]]; then
        jq -r "$jq_filter"
    else
        jq -r "$jq_filter" "$source"
    fi
}

# Cask token → /Applications bundle name (without ".app" suffix).
# Bash 3.2: case statement, NOT associative array.
ascendo_brew_cask_app_name() {
    case "$1" in
        slack)                echo "Slack" ;;
        visual-studio-code)   echo "Visual Studio Code" ;;
        google-chrome)        echo "Google Chrome" ;;
        firefox)              echo "Firefox" ;;
        spotify)              echo "Spotify" ;;
        notion)               echo "Notion" ;;
        zoom)                 echo "zoom.us" ;;
        iterm2)               echo "iTerm" ;;
        docker)               echo "Docker" ;;
        rectangle)            echo "Rectangle" ;;
        *)                    echo "" ;;
    esac
}

# Graceful quit via osascript, then force-kill if still running after 5 s.
# Returns 0 on success (app quit or wasn't running), 1 on hard failure.
ascendo_brew_kill_cask_apps() {
    local cask="$1"
    local app
    app="$(ascendo_brew_cask_app_name "$cask")"
    [[ -z "$app" ]] && return 0   # no mapping → assume nothing to quit

    if ! pgrep -x "$app" >/dev/null 2>&1; then
        return 0
    fi

    osascript -e "tell application \"$app\" to quit" >/dev/null 2>&1 || true

    local i=0
    while [[ $i -lt 5 ]]; do
        if ! pgrep -x "$app" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done

    pkill -f "/Applications/$app.app/" >/dev/null 2>&1 || true
    sleep 1
    if pgrep -x "$app" >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

# Translate brew's exit code into the ascendo phase exit-code conventions.
# brew is generally polite — 0 success, non-zero failure. Casks that need a
# reboot are uncommon but signaled via stderr text, not exit code.
ascendo_brew_exit_code() {
    local code="${1:-0}"
    case "$code" in
        0) echo 0 ;;       # success
        *) echo 30 ;;      # apply-fail-unknown (per docs/agents/contract.md)
    esac
}
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_brew_helpers.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/lib/ascendo_brew.sh adapters/macos/tests/test_ascendo_brew_helpers.py adapters/macos/tests/fixtures/brew-outdated.json
git commit -m "$(cat <<'EOF'
feat(macos/lib): ascendo_brew.sh — brew helpers (M5.1.2)

Bash 3.2-safe Homebrew helper module. Functions:

  ascendo_brew_prefix         — `brew --prefix` or empty
  ascendo_brew_version        — first line of `brew --version`
  ascendo_brew_outdated_json  — `brew outdated --json=v2`
  ascendo_brew_parse_outdated — jq-based JSON → CSV rows
                                (handles cask name=array vs string,
                                 installed_versions array vs string)
  ascendo_brew_cask_app_name  — token → /Applications bundle name
                                (Bash 3.2 case statement; 10 entries)
  ascendo_brew_kill_cask_apps — graceful osascript quit + force pkill
                                fallback after 5s
  ascendo_brew_exit_code      — brew exit → ascendo phase exit map

The kill_cask_apps helper is NEW (legacy update_brew.sh did not have
process-kill — it relied on brew 4.x's built-in app quit). The Windows
process-kill pattern (Stop-PackageProcesses) is the model here.

3 pytest smoke tests cover the formula/cask parser, prefix detection,
and the cask-name mapping.

Refs spec §4.
EOF
)"
```

---

## Task 5: `check.sh` — read-only inventory phase

The first phase script. Read-only: enumerates outdated formulae + casks via `brew outdated --json=v2`, emits one item per outdated package with `status=planned`. Mirrors Windows `winget/check.ps1`.

**Files:**
- Create: `adapters/macos/scripts/brew/check.sh` (~120 LOC)
- Test: `adapters/macos/tests/test_check_script.py`

- [ ] **Step 1: Write failing test (real brew on this Mac)**

Create `adapters/macos/tests/test_check_script.py`:

```python
"""Real-brew test for adapters/macos/scripts/brew/check.sh.

Runs the phase script directly (not through Python adapter), verifies the
sidecar lands at the expected path with the right shape.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "brew" / "check.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "check.sh not executable"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("brew") is None or shutil.which("jq") is None,
    reason="real brew + jq on macOS required",
)
def test_check_emits_valid_sidecar(tmp_path: Path) -> None:
    run_id = str(uuid.uuid4())
    out_dir = tmp_path / "runs"
    res = subprocess.run(
        [
            "bash", str(SCRIPT),
            "--run-id", run_id,
            "--trigger", "cli",
            "--profile", "default",
            "--output-dir", str(out_dir),
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode in (0, 1), f"unexpected exit: {res.returncode}\n{res.stderr}\n{res.stdout}"

    sidecar_path = out_dir / run_id / "check__brew.json"
    assert sidecar_path.is_file(), f"missing {sidecar_path}\n{res.stdout}\n{res.stderr}"

    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)

    assert sc.schema_id.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    # summary.total may be 0 if nothing outdated; phase still success.
    assert sc.summary.total >= 0
    assert sc.tool.name == "brew"
```

- [ ] **Step 2: Run test to confirm fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py -v
```

Expected: FAIL on `test_script_exists_and_executable`.

- [ ] **Step 3: Implement `check.sh`**

Create `adapters/macos/scripts/brew/check.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/check.sh — read-only brew inventory phase
# =============================================================================
# Lists outdated formulae + casks via `brew outdated --json=v2`. Side-effect
# free (no `brew update`, no upgrades, no cleanups). Emits one ascendo/v1
# sidecar at <output-dir>/<run-id>/check__brew.json.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]   (no-op for check; accepted for parity)
#   [--filter id1,id2,...]
#
# Exit codes (per docs/agents/contract.md):
#   0  success
#   1  warn (e.g. brew not on PATH but everything else fine)
#   2  bad usage
#   30 apply-fail-unknown (covers brew unexpected exit on the read paths)
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_brew.sh"

# ── arg parsing ──────────────────────────────────────────────────────────────
RUN_ID=""
TRIGGER=""
PROFILE_NAME=""
OUTPUT_DIR=""
DRY_RUN="false"
FILTER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN="true"; shift ;;
        --filter)     FILTER="$2"; shift 2 ;;
        *) echo "check.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done
if [[ -z "$RUN_ID" || -z "$TRIGGER" || -z "$PROFILE_NAME" || -z "$OUTPUT_DIR" ]]; then
    echo "check.sh: missing required args (run-id/trigger/profile/output-dir)" >&2
    exit 2
fi

# ── host info ────────────────────────────────────────────────────────────────
HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS="macos"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="$([[ $EUID -eq 0 ]] && echo true || echo false)"

# ── tool info ────────────────────────────────────────────────────────────────
TOOL_VERSION="$(ascendo_brew_version || echo unknown)"
[[ -z "$TOOL_VERSION" ]] && TOOL_VERSION="unknown"

# ── init sidecar + EXIT trap ────────────────────────────────────────────────
json_init "check" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "$HOST_OS" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

# ── preconditions ────────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
    json_add_message warn "brew not on PATH"
    exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
    json_add_message error "jq required for ascendo brew adapter (install: brew install jq)" "JQ_MISSING"
    exit 30
fi

# ── enumerate outdated ───────────────────────────────────────────────────────
TMP_OUT="$(mktemp -t ascendo_brew_outdated_XXXXXX)"
trap "rm -f \"$TMP_OUT\"; json_save_on_exit \"$OUTPUT_DIR\"" EXIT

if ! ascendo_brew_outdated_json > "$TMP_OUT"; then
    json_add_message error "brew outdated --json=v2 failed" "BREW_OUTDATED_FAIL"
    exit 30
fi

# Filter set (CSV → space-separated for grep)
filter_match() {
    local id="$1"
    [[ -z "$FILTER" ]] && return 0
    local IFS=','
    for f in $FILTER; do
        [[ "$f" == "$id" ]] && return 0
    done
    return 1
}

emit_outdated() {
    local bucket="$1"   # formula | cask
    local feed="$bucket"
    local count=0
    while IFS=',' read -r id current target; do
        [[ -z "$id" ]] && continue
        if filter_match "$id"; then
            json_add_item "$id" "$current" "$target" "planned" "brew" "$feed"
            count=$((count + 1))
        fi
    done < <(ascendo_brew_parse_outdated "$TMP_OUT" "$bucket" 2>/dev/null)
    json_add_message info "outdated $bucket: $count"
}

emit_outdated formula
emit_outdated cask

# Up-to-date pass: list installed packages NOT in the outdated set, mark up_to_date.
# Cheap implementation: emit one summary message instead of a per-item loop
# (the per-item up_to_date loop can land in a follow-up; for MVP, outdated-only
# items keep the sidecar small and the dashboard signal-to-noise high).
INSTALLED_F="$(brew list --formula 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
INSTALLED_C="$(brew list --cask 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
json_add_message info "installed formulae: $INSTALLED_F, casks: $INSTALLED_C"

exit 0
```

Make it executable:

```bash
chmod +x adapters/macos/scripts/brew/check.sh
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py -v
```

Expected: 2 passed (or 1 + skip if not on macOS / no brew, but on this Mac both should pass).

- [ ] **Step 5: Run the script directly for manual smoke**

```bash
RID=$(uuidgen)
OUT=$(mktemp -d)
bash adapters/macos/scripts/brew/check.sh --run-id "$RID" --trigger cli --profile default --output-dir "$OUT"
echo "exit=$?"
cat "$OUT/$RID/check__brew.json" | python3 -m json.tool | head -40
```

Expected: exit 0, JSON sidecar with `schema=ascendo/v1`, `phase=check`, `category=brew`, items[] with the outdated packages on this Mac.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/brew/check.sh adapters/macos/tests/test_check_script.py
git commit -m "$(cat <<'EOF'
feat(macos/scripts): brew/check.sh — read-only inventory phase (M5.1.3)

First macOS phase script. Read-only: lists outdated formulae + casks
via `brew outdated --json=v2` and emits one ascendo/v1 sidecar item
per outdated package with status=planned.

Side-effect free (NO `brew update`, NO upgrades, NO cleanups). Mirrors
the Windows winget/check.ps1 contract: side-effect-free, accepts the
same arg surface (--run-id/--trigger/--profile/--output-dir/--dry-run/
--filter), drops a sidecar at <output-dir>/<run-id>/check__brew.json
on every code path via EXIT trap.

Honors Aktualizacje_MAC critical rules:
  - set -o pipefail (NOT set -e)
  - Bash 3.2 only (no associative arrays, no mapfile)
  - SCRIPT_DIR via cd-dirname idiom (no hardcoded paths)
  - mktemp -t with TMPDIR fallback baked into ascendo_json.sh

Real-brew pytest smoke verifies the sidecar parses through Pydantic.

Refs spec §4.
EOF
)"
```

---

## Task 6: `BrewManager` Python adapter + smoke tests

The Python wrapper that orchestrator calls. Mirrors `WingetManager` line-for-line, with bash invocation instead of pwsh.

**Files:**
- Create: `adapters/macos/ascendo_macos/managers/__init__.py` (empty)
- Create: `adapters/macos/ascendo_macos/managers/brew.py` (~280 LOC)
- Create: `adapters/macos/tests/conftest.py`
- Create: `adapters/macos/tests/test_brew_manager_smoke.py` (~250 LOC, ~13 tests)
- Fixture: `adapters/macos/tests/fixtures/check__brew.json` (a valid sidecar for parse round-trip)

- [ ] **Step 1: Capture a valid sidecar fixture for round-trip tests**

```bash
RID=00000000-0000-0000-0000-000000000099
OUT=$(mktemp -d)
bash adapters/macos/scripts/brew/check.sh --run-id "$RID" --trigger cli --profile default --output-dir "$OUT"
mkdir -p adapters/macos/tests/fixtures
cp "$OUT/$RID/check__brew.json" adapters/macos/tests/fixtures/check__brew.json
ls -la adapters/macos/tests/fixtures/check__brew.json
```

If brew has no outdated packages, hand-craft a minimal fixture:

```bash
cat > adapters/macos/tests/fixtures/check__brew.json <<'EOF'
{
  "schema": "ascendo/v1",
  "phase": "check",
  "category": "brew",
  "run": {
    "id": "00000000-0000-0000-0000-000000000099",
    "trigger": "cli",
    "profile": "default",
    "dry_run": false,
    "started_at": "2026-05-03T12:00:00Z"
  },
  "host": {
    "hostname": "macbook.local",
    "os": "macos",
    "os_version": "14.5",
    "arch": "arm64",
    "user": "mk",
    "is_elevated": false
  },
  "tool": {"name": "brew", "version": "4.4.0"},
  "finished_at": "2026-05-03T12:00:01Z",
  "exit_code": 0,
  "status": "success",
  "summary": {"total": 1, "success": 0, "skipped": 0, "failed": 0},
  "items": [
    {
      "id": "node",
      "current_version": "20.10.0",
      "target_version": "21.6.1",
      "status": "planned",
      "source": {"type": "brew", "feed": "formula"}
    }
  ],
  "messages": [{"level": "info", "text": "outdated formula: 1"}],
  "needs_reboot": false
}
EOF
```

- [ ] **Step 2: Write failing test file**

Create `adapters/macos/tests/conftest.py`:

```python
"""Shared fixtures for adapters/macos/tests/."""
from __future__ import annotations

import sys
from pathlib import Path

# Make ascendo (core) importable in tests without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_SRC = _REPO_ROOT / "core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

# Make ascendo_macos importable.
_ADAPTER_SRC = Path(__file__).resolve().parents[1]
if str(_ADAPTER_SRC) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_SRC))
```

Create `adapters/macos/tests/test_brew_manager_smoke.py`:

```python
"""BrewManager smoke tests — mock-based, runs on any OS."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger

from ascendo_macos.managers.brew import BrewManager

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SIDECAR = ADAPTER_ROOT / "tests" / "fixtures" / "check__brew.json"


def _mac_host(elevated: bool = False) -> HostInfo:
    return HostInfo(
        hostname="macbook.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=elevated,
    )


def _linux_host() -> HostInfo:
    return HostInfo(
        hostname="ubuntu",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _win_host() -> HostInfo:
    return HostInfo(
        hostname="winbox",
        os=OperatingSystem.WINDOWS,
        os_version="11",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _run() -> RunInfo:
    return RunInfo(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        trigger=Trigger.CLI,
        profile="default",
        dry_run=False,
    )


def _mgr(tmp_path: Path) -> BrewManager:
    return BrewManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )


# ── Identity ─────────────────────────────────────────────────────

def test_category_is_brew(tmp_path: Path) -> None:
    assert _mgr(tmp_path).category is SourceType.BREW


def test_display_name_is_homebrew(tmp_path: Path) -> None:
    assert "Homebrew" in _mgr(tmp_path).display_name


# ── Availability matrix ──────────────────────────────────────────

@patch("shutil.which")
def test_is_available_false_on_linux(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/usr/bin/" + x
    assert _mgr(tmp_path).is_available(_linux_host()) is False


@patch("shutil.which")
def test_is_available_false_on_windows(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/usr/bin/" + x
    assert _mgr(tmp_path).is_available(_win_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_false_when_brew_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: None if x == "brew" else "/usr/bin/jq"
    assert _mgr(tmp_path).is_available(_mac_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_false_when_jq_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: "/opt/homebrew/bin/brew" if x == "brew" else None
    assert _mgr(tmp_path).is_available(_mac_host()) is False


@patch("ascendo_macos.managers.brew.shutil.which")
def test_is_available_true_with_brew_and_jq(mock_which: MagicMock, tmp_path: Path) -> None:
    mock_which.side_effect = lambda x: f"/opt/homebrew/bin/{x}"
    assert _mgr(tmp_path).is_available(_mac_host()) is True


# ── argv shape ───────────────────────────────────────────────────

@pytest.mark.parametrize("phase,script_name", [
    (Phase.CHECK, "check.sh"),
    (Phase.PLAN, "plan.sh"),
    (Phase.APPLY, "apply.sh"),
    (Phase.VERIFY, "verify.sh"),
    (Phase.CLEANUP, "cleanup.sh"),
])
def test_build_argv_per_phase(phase: Phase, script_name: str, tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / script_name,
        run=_run(),
        output_dir=tmp_path,
        item_filter=None,
    )
    assert argv[0] == "/bin/bash"
    assert argv[1].endswith(script_name)
    assert "--run-id" in argv
    assert "--output-dir" in argv


def test_build_argv_omits_dry_run_when_false(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "check.sh",
        run=_run(),  # dry_run=False
        output_dir=tmp_path,
        item_filter=None,
    )
    assert "--dry-run" not in argv


def test_build_argv_includes_dry_run_when_true(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    run = _run()
    run = run.model_copy(update={"dry_run": True})
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=run,
        output_dir=tmp_path,
        item_filter=None,
    )
    assert "--dry-run" in argv


def test_build_argv_passes_filter_csv(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=_run(),
        output_dir=tmp_path,
        item_filter=["node", "git", "jq"],
    )
    idx = argv.index("--filter")
    assert argv[idx + 1] == "node,git,jq"


def test_build_argv_omits_empty_filter(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr._build_argv(
        bash="/bin/bash",
        script_path=mgr._scripts_dir / "brew" / "apply.sh",
        run=_run(),
        output_dir=tmp_path,
        item_filter=["", "  ", None],
    )
    assert "--filter" not in argv


# ── run_phase end-to-end with mocked subprocess ─────────────────

def _populate_fake_sidecar(output_dir: Path, run_id: UUID, phase: Phase) -> None:
    """Helper: drop the fixture sidecar at the path BrewManager will read."""
    target = output_dir / str(run_id) / f"{phase.value}__brew.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(FIXTURE_SIDECAR.read_bytes())


@patch("ascendo_macos.managers.brew.subprocess.Popen")
def test_run_phase_returns_parsed_sidecar(mock_popen: MagicMock, tmp_path: Path) -> None:
    """When the script exits 0 and produces a sidecar, run_phase parses it."""
    captured_argv = {}

    def _popen_side_effect(argv, **kwargs):
        captured_argv["argv"] = argv
        # The output_dir is the 2nd-from-last positional arg pair
        # but easiest: discover it by scanning argv.
        idx = argv.index("--output-dir")
        out_dir = Path(argv[idx + 1])
        run_idx = argv.index("--run-id")
        run_id = UUID(argv[run_idx + 1])
        _populate_fake_sidecar(out_dir, run_id, Phase.CHECK)
        proc = MagicMock()
        proc.stdout.readline.side_effect = [""]
        proc.wait.return_value = 0
        proc.returncode = 0
        proc.kill = MagicMock()
        return proc

    mock_popen.side_effect = _popen_side_effect
    sc = _mgr(tmp_path).run_phase(Phase.CHECK, _run(), _mac_host())
    assert sc.schema_id.value == "ascendo/v1"
    assert sc.phase is Phase.CHECK
    assert sc.category is SourceType.BREW


@patch("ascendo_macos.managers.brew.subprocess.Popen")
def test_run_phase_raises_when_no_sidecar(mock_popen: MagicMock, tmp_path: Path) -> None:
    """Script exits non-zero with no sidecar → ManagerError."""
    proc = MagicMock()
    proc.stdout.readline.side_effect = ["error\n", ""]
    proc.wait.return_value = 30
    proc.returncode = 30
    mock_popen.return_value = proc
    with pytest.raises(ManagerError):
        _mgr(tmp_path).run_phase(Phase.CHECK, _run(), _mac_host())


def test_run_phase_unsupported_raises() -> None:
    """Phases not in SCRIPT_BY_PHASE should never happen — but assert defensively."""
    # All 5 canonical phases are mapped; this test documents the contract.
    mgr = BrewManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )
    assert set(mgr.SCRIPT_BY_PHASE) == {
        Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP,
    }
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_brew_manager_smoke.py -v
```

Expected: FAIL — `cannot import name 'BrewManager'`.

- [ ] **Step 4: Implement `BrewManager`**

Create `adapters/macos/ascendo_macos/managers/__init__.py` (empty):

```bash
touch adapters/macos/ascendo_macos/managers/__init__.py
```

Create `adapters/macos/ascendo_macos/managers/brew.py`:

```python
"""BrewManager - IPackageManager for Homebrew (formulae + casks)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class BrewManager(IPackageManager):
    """Homebrew per-source manager (formulae + casks under one category).

    Args:
        scripts_dir: Path to ``adapters/macos/scripts/``.
        lib_dir:     Path to ``adapters/macos/lib/`` (informational only).
        bash_path:   Optional override for bash binary.
        timeout_sec: Per-phase timeout. Default 1800 (30 min).
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK: "brew/check.sh",
        Phase.PLAN: "brew/plan.sh",
        Phase.APPLY: "brew/apply.sh",
        Phase.VERIFY: "brew/verify.sh",
        Phase.CLEANUP: "brew/cleanup.sh",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec

    # ── Identity ────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.BREW

    @property
    def display_name(self) -> str:
        return "Homebrew (formulae + casks)"

    # ── Availability ────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        if shutil.which("brew") is None:
            return False
        if shutil.which("jq") is None:
            return False
        return True

    # ── Phase execution ─────────────────────────────────────────

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            raise ManagerError(
                f"BrewManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-brew-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash,
                script_path=script_path,
                run=run,
                output_dir=output_dir,
                item_filter=item_filter,
            )
            log_path = (
                output_dir / str(run.id) / f"{phase.value}__brew.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)

            _log.debug("BrewManager.run_phase phase=%s run_id=%s argv=%r",
                       phase.value, run.id, argv)

            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"brew {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for brew {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__brew.json"
            if not sidecar_path.exists():
                raise ManagerError(self._missing_sidecar_error(
                    phase=phase, script_path=script_path,
                    sidecar_path=sidecar_path, completed=completed,
                ))
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"brew {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "brew %s script exited %d but produced a valid sidecar; "
                    "trusting sidecar (status=%s)",
                    phase.value, completed.returncode, sc.status.value,
                )
            return sc

    # ── Internals ───────────────────────────────────────────────

    def _build_argv(
        self,
        *,
        bash: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        argv: list[str] = [
            bash,
            str(script_path),
            "--run-id", str(run.id),
            "--trigger", run.trigger.value,
            "--profile", run.profile,
            "--output-dir", str(output_dir),
        ]
        if run.dry_run:
            argv.append("--dry-run")
        if item_filter is not None:
            cleaned = [s.strip() for s in item_filter if s and isinstance(s, str) and s.strip()]
            if cleaned:
                argv.extend(["--filter", ",".join(cleaned)])
        return argv

    def _run_streaming(
        self,
        argv: list[str],
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(  # noqa: S603 (argv list)
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        captured: list[str] = []
        started = time.monotonic()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for line in iter(proc.stdout.readline, ""):
                    if time.monotonic() - started > timeout:
                        proc.kill()
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if not line:
                        break
                    captured.append(line)
                    try:
                        fh.write(line)
                        fh.flush()
                    except OSError:
                        pass
        finally:
            proc.stdout.close()
        try:
            rc = proc.wait(timeout=max(1.0, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        return subprocess.CompletedProcess(
            args=argv, returncode=rc, stdout="".join(captured), stderr="",
        )

    def _missing_sidecar_error(
        self,
        *,
        phase: Phase,
        script_path: Path,
        sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s: return "<empty>"
            return s if len(s) <= limit else f"...<truncated {len(s) - limit}>...{s[-limit:]}"
        return (
            f"brew {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        for cand in ("bash", "/bin/bash"):
            found = shutil.which(cand) if not cand.startswith("/") else (cand if Path(cand).is_file() else None)
            if found:
                return found
        raise ManagerError("no bash on PATH and /bin/bash missing")
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_brew_manager_smoke.py -v
```

Expected: 13+ passed.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/ascendo_macos/managers/__init__.py adapters/macos/ascendo_macos/managers/brew.py adapters/macos/tests/conftest.py adapters/macos/tests/test_brew_manager_smoke.py adapters/macos/tests/fixtures/check__brew.json
git commit -m "$(cat <<'EOF'
feat(macos/adapter): BrewManager + smoke tests (M5.1.4)

Python adapter for Homebrew. Mirrors WingetManager line-for-line,
swapping pwsh for bash and Windows paths for the macOS layout.

Identity:
  category     = SourceType.BREW   (covers formulae + casks under one
                                    manager; per-item feed="formula"
                                    or feed="cask" carries the namespace)
  display_name = "Homebrew (formulae + casks)"

Availability matrix (mocked via shutil.which):
  is_available(host) is True only when host.os == macOS AND `brew`
  AND `jq` are both on PATH.

Phase dispatch: SCRIPT_BY_PHASE maps all 5 phases to .sh files under
scripts/brew/. argv-only invocation (T4 mitigation per ADR-0005).
--dry-run is presence-based (the Sesja 9 lesson: never pass boolean
strings; PowerShell's [switch] equivalent on bash is "include the
flag to enable, omit to disable").

13 mock-based smoke tests cover identity, availability matrix
(macOS + Linux + Windows × brew/jq present/absent), argv shape per
phase, --dry-run presence/absence, --filter CSV joining, ManagerError
on missing sidecar, parse round-trip via read_sidecar.

Refs spec §3.
EOF
)"
```

---

## Task 7: `MacOSAdapter` + cross-module integration

The adapter aggregate. Mirrors `WindowsAdapter` but declares only `PACKAGE_MANAGEMENT` capability (E1) and returns `None` for inventory/snapshot/scheduler/elevation/source.

**Files:**
- Modify: `adapters/macos/ascendo_macos/__init__.py` (export `MacOSAdapter`)
- Create: `adapters/macos/ascendo_macos/adapter.py` (~180 LOC)
- Create: `adapters/macos/tests/test_adapter_smoke.py` (~120 LOC, ~8 tests)

- [ ] **Step 1: Write failing test file**

Create `adapters/macos/tests/test_adapter_smoke.py`:

```python
"""MacOSAdapter smoke tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ascendo.interfaces import AdapterCapability
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType

from ascendo_macos import MacOSAdapter


def test_adapter_identity() -> None:
    a = MacOSAdapter()
    assert a.name == "macos"
    assert a.display_name == "macOS"
    assert a.tier == 1


def test_capabilities_is_package_management_only() -> None:
    a = MacOSAdapter()
    assert a.capabilities == AdapterCapability.PACKAGE_MANAGEMENT


def test_unsupported_accessors_return_none_in_m51() -> None:
    a = MacOSAdapter()
    assert a.inventory() is None
    assert a.snapshot() is None
    assert a.scheduler() is None
    assert a.source() is None
    assert a.elevation() is None


def test_package_managers_returns_brew() -> None:
    a = MacOSAdapter()
    host = HostInfo(
        hostname="macbook.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
    )
    mgrs = a.package_managers(host)
    assert len(mgrs) == 1
    assert mgrs[0].category is SourceType.BREW


def test_detect_host_returns_macos_on_darwin() -> None:
    """detect_host on this Mac returns OS=MACOS."""
    import platform
    if platform.system() != "Darwin":
        pytest.skip("requires macOS")
    h = MacOSAdapter().detect_host()
    assert h.os is OperatingSystem.MACOS
    assert h.arch in {"arm64", "x86_64"}
    assert h.hostname


def test_health_check_reports_brew_jq_bash() -> None:
    a = MacOSAdapter()
    h = a.health_check()
    assert "brew" in h
    assert "jq" in h
    assert "bash" in h
    assert "ascendo_lib" in h
    assert "ascendo_scripts" in h


def test_adapter_factory_resolves_macos() -> None:
    """The factory must find MacOSAdapter for OperatingSystem.MACOS."""
    from ascendo.adapter_factory import AdapterRegistry
    reg = AdapterRegistry()
    reg.discover()
    cls = reg.get(OperatingSystem.MACOS)
    assert cls is not None
    assert cls.__name__ == "MacOSAdapter"


def test_adapter_via_select_adapter() -> None:
    """select_adapter(MACOS) returns a working MacOSAdapter instance."""
    from ascendo.adapter_factory import select_adapter
    a = select_adapter(OperatingSystem.MACOS)
    assert isinstance(a, MacOSAdapter)
    assert a.tier == 1
```

- [ ] **Step 2: Run tests to confirm fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_adapter_smoke.py -v
```

Expected: FAIL — `cannot import name 'MacOSAdapter'`.

- [ ] **Step 3: Implement `MacOSAdapter`**

Replace `adapters/macos/ascendo_macos/__init__.py`:

```python
"""ascendo-macos — Tier 1 adapter for macOS.

M5.1 capability: PACKAGE_MANAGEMENT (Homebrew). Other capabilities
(inventory, snapshots, scheduler, elevation, source) ship in M5.2-M5.5.
"""
from __future__ import annotations

from .adapter import MacOSAdapter

__all__ = ["MacOSAdapter"]
```

Create `adapters/macos/ascendo_macos/adapter.py`:

```python
"""MacOSAdapter - implements IAdapter for macOS."""
from __future__ import annotations

import getpass
import locale
import logging
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces import (
    AdapterCapability,
    IAdapter,
    IElevation,
    IInventory,
    IPackageManager,
    IScheduler,
    ISnapshot,
    ISource,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem

from .managers.brew import BrewManager

_log = logging.getLogger(__name__)


def _resolve_resource_dir(env_var: str, repo_relative: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / repo_relative


class MacOSAdapter(IAdapter):
    """Tier 1 adapter for macOS (M5.1 — PACKAGE_MANAGEMENT only)."""

    SCRIPTS_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_MACOS_SCRIPTS_DIR", "scripts"
    )
    LIB_DIR: ClassVar[Path] = _resolve_resource_dir(
        "ASCENDO_MACOS_LIB_DIR", "lib"
    )

    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None

    @property
    def name(self) -> str:
        return "macos"

    @property
    def display_name(self) -> str:
        return "macOS"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [
            BrewManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
        ]

    def inventory(self) -> IInventory | None:
        return None  # M5.3

    def snapshot(self) -> ISnapshot | None:
        return None  # M5.4 (Time Machine read-only)

    def scheduler(self) -> IScheduler | None:
        return None  # M5.5 (launchd)

    def source(self) -> ISource | None:
        return None

    def elevation(self) -> IElevation | None:
        return None  # M5.2 (sudo askpass cache)

    def detect_host(self) -> HostInfo:
        if self._cached_host is not None:
            return self._cached_host

        os_family = self._detect_os()
        os_version = self._detect_os_version(os_family)
        arch = self._detect_arch()
        hostname = self._detect_hostname()
        user = self._detect_user()
        is_elevated = self._detect_is_elevated()
        elevation_method = (
            ElevationMethod.SUDO if is_elevated else ElevationMethod.NONE
        )
        bcp47 = self._detect_locale()

        self._cached_host = HostInfo(
            hostname=hostname,
            os=os_family,
            os_version=os_version,
            arch=arch,
            user=user,
            is_elevated=is_elevated,
            elevation_method=elevation_method,
            locale=bcp47,
        )
        return self._cached_host

    def health_check(self) -> dict[str, str]:
        out: dict[str, str] = {}
        out["brew"] = self._brew_status()
        out["jq"] = self._jq_status()
        out["bash"] = self._bash_status()
        out["ascendo_lib"] = self._lib_status()
        out["ascendo_scripts"] = self._scripts_status()
        return out

    # ── helpers ────────────────────────────────────────────────

    def _detect_os(self) -> OperatingSystem:
        return OperatingSystem.MACOS if platform.system() == "Darwin" else OperatingSystem.UNKNOWN

    def _detect_os_version(self, os_family: OperatingSystem) -> str:
        if os_family is OperatingSystem.MACOS:
            try:
                res = subprocess.run(
                    ["sw_vers", "-productVersion"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                v = (res.stdout or "").strip()
                return v or "unknown"
            except (OSError, subprocess.TimeoutExpired):
                return "unknown"
        return platform.platform() or "unknown"

    def _detect_arch(self) -> str:
        return (platform.machine() or "unknown").lower()

    def _detect_hostname(self) -> str:
        try:
            return socket.gethostname() or "unknown"
        except OSError:
            return "unknown"

    def _detect_user(self) -> str:
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return os.environ.get("USER") or "unknown"

    def _detect_is_elevated(self) -> bool:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    def _detect_locale(self) -> str | None:
        try:
            tag, _enc = locale.getlocale()
        except (ValueError, locale.Error):
            tag = None
        if not tag:
            return None
        return tag.replace("_", "-")

    def _brew_status(self) -> str:
        path = shutil.which("brew")
        if path is None:
            return "unavailable: brew not on PATH"
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: brew --version exited {res.returncode}"
        v = (res.stdout or "").strip().splitlines()
        return f"ok: {v[0]}" if v else "ok"

    def _jq_status(self) -> str:
        path = shutil.which("jq")
        if path is None:
            return "unavailable: jq not on PATH (install: brew install jq)"
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: jq --version exited {res.returncode}"
        return f"ok: {(res.stdout or '').strip()}"

    def _bash_status(self) -> str:
        path = shutil.which("bash") or "/bin/bash"
        if not Path(path).exists():
            return "unavailable: no bash"
        try:
            res = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: bash --version exited {res.returncode}"
        v = (res.stdout or "").splitlines()
        return f"ok: {v[0]}" if v else "ok"

    def _lib_status(self) -> str:
        if not self.LIB_DIR.is_dir():
            return f"unavailable: {self.LIB_DIR} does not exist"
        sh = list(self.LIB_DIR.glob("*.sh")) + list(self.LIB_DIR.glob("*.py"))
        if not sh:
            return f"degraded: {self.LIB_DIR} exists but contains no .sh/.py modules"
        return f"ok: {len(sh)} module(s)"

    def _scripts_status(self) -> str:
        if not self.SCRIPTS_DIR.is_dir():
            return f"unavailable: {self.SCRIPTS_DIR} does not exist"
        brew_dir = self.SCRIPTS_DIR / "brew"
        if not brew_dir.is_dir():
            return f"degraded: {brew_dir} missing (brew scripts not installed)"
        return "ok"
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_adapter_smoke.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Run the full macOS adapter test suite**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/ -v
```

Expected: all green (~30 tests across 5 test files).

- [ ] **Step 6: Live smoke — install editable + run `python -m ascendo doctor`**

```bash
pip install -e ./core --quiet
pip install -e ./adapters/macos --no-deps --quiet
python -m ascendo doctor -v
```

Expected output: `macos (macOS) tier=1`, `brew ok: ...`, `jq ok: ...`, `bash ok: ...`, `ascendo_lib ok: 3 module(s)`, `ascendo_scripts ok`. Exit 0.

- [ ] **Step 7: Commit**

```bash
git add adapters/macos/ascendo_macos/__init__.py adapters/macos/ascendo_macos/adapter.py adapters/macos/tests/test_adapter_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos/adapter): MacOSAdapter + cross-module integration (M5.1.4-M5.1.5)

Implements IAdapter for macOS. Capabilities: PACKAGE_MANAGEMENT only
(M5.1 scope per spec E1). All other accessors (inventory/snapshot/
scheduler/elevation/source) return None — reserved for M5.2-M5.5.

package_managers() returns [BrewManager(scripts_dir, lib_dir)].

Host detection: Darwin → OperatingSystem.MACOS, sw_vers -productVersion
for OS version, uname -m for arch, geteuid()==0 for elevation.

health_check() reports brew, jq, bash, ascendo_lib, ascendo_scripts —
each with ok/degraded/unavailable/error semantics matching the Windows
adapter.

8 smoke tests cover identity, capability flag, accessor None-ness,
package_managers wiring, detect_host on Darwin, health_check shape,
and integration through adapter_factory.AdapterRegistry.discover() +
select_adapter() (the entry-point + direct-import-fallback path
proven on Windows).

Refs spec §3.
EOF
)"
```

---

## Task 8: `apply.sh` — first mutation phase

The only mutating phase. For each outdated formula/cask in plan: optionally kill running cask apps, run `brew upgrade`, capture exit code per item.

**Files:**
- Create: `adapters/macos/scripts/brew/apply.sh` (~200 LOC)
- Test: extend `adapters/macos/tests/test_check_script.py` with apply-dry-run case (real brew, no mutation)

- [ ] **Step 1: Write failing test for apply (--dry-run only — no real upgrade)**

Append to `adapters/macos/tests/test_check_script.py`:

```python
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("brew") is None or shutil.which("jq") is None,
    reason="real brew + jq on macOS required",
)
def test_apply_dry_run_emits_planned_items(tmp_path: Path) -> None:
    """apply.sh with --dry-run emits status=planned, no real upgrade."""
    APPLY = ADAPTER_ROOT / "scripts" / "brew" / "apply.sh"
    run_id = str(uuid.uuid4())
    out_dir = tmp_path / "runs"
    res = subprocess.run(
        [
            "bash", str(APPLY),
            "--run-id", run_id,
            "--trigger", "cli",
            "--profile", "default",
            "--output-dir", str(out_dir),
            "--dry-run",
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode in (0, 1), f"unexpected exit: {res.returncode}\n{res.stderr}\n{res.stdout}"
    sidecar_path = out_dir / run_id / "apply__brew.json"
    assert sidecar_path.is_file()
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar_path.read_text())
    finally:
        sys.path.pop(0)
    # In dry-run mode, NO item should have status in success/failed (those
    # are mutation outcomes); planned/up_to_date are valid.
    for it in sc.items:
        assert it.status.value in {"planned", "up_to_date", "skipped"}, \
            f"unexpected status {it.status.value} for {it.id} in dry-run"
```

- [ ] **Step 2: Confirm test fails (apply.sh missing)**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py::test_apply_dry_run_emits_planned_items -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `apply.sh`**

Create `adapters/macos/scripts/brew/apply.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/apply.sh — brew upgrade phase (the only mutation)
# =============================================================================
# For each outdated formula / cask (filtered by --filter if given):
#   * dry-run: emit one item with status=planned, no actions
#   * real:    optionally quit running cask app via osascript, run
#              `brew upgrade` for the package, emit success / failed
#              based on exit code.
#
# Args:
#   --run-id ID --trigger TRIG --profile NAME --output-dir DIR
#   [--dry-run]
#   [--filter id1,id2,...]
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_brew.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN="false"; FILTER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN="true"; shift ;;
        --filter)     FILTER="$2"; shift 2 ;;
        *) echo "apply.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done
[[ -z "$RUN_ID" || -z "$TRIGGER" || -z "$PROFILE_NAME" || -z "$OUTPUT_DIR" ]] && {
    echo "apply.sh: missing required args" >&2; exit 2
}

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="$([[ $EUID -eq 0 ]] && echo true || echo false)"
TOOL_VERSION="$(ascendo_brew_version || echo unknown)"
[[ -z "$TOOL_VERSION" ]] && TOOL_VERSION="unknown"

json_init "apply" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

if ! command -v brew >/dev/null 2>&1; then
    json_add_message error "brew not on PATH"
    exit 30
fi
if ! command -v jq >/dev/null 2>&1; then
    json_add_message error "jq required (install: brew install jq)" "JQ_MISSING"
    exit 30
fi

filter_match() {
    local id="$1"
    [[ -z "$FILTER" ]] && return 0
    local IFS=','; for f in $FILTER; do [[ "$f" == "$id" ]] && return 0; done
    return 1
}

OUTDATED_FILE="$(mktemp -t ascendo_brew_outdated_XXXXXX)"
trap 'rm -f "$OUTDATED_FILE"; json_save_on_exit "$OUTPUT_DIR"' EXIT
ascendo_brew_outdated_json > "$OUTDATED_FILE" || {
    json_add_message error "brew outdated --json=v2 failed"; exit 30; }

ANY_FAILED=0

apply_one() {
    local id="$1" current="$2" target="$3" feed="$4"   # feed: formula|cask
    if ! filter_match "$id"; then
        return 0
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        json_add_item "$id" "$current" "$target" "planned" "brew" "$feed"
        return 0
    fi

    # Quit running cask apps before upgrade (no-op for formulae)
    if [[ "$feed" == "cask" ]]; then
        if ! ascendo_brew_kill_cask_apps "$id"; then
            json_add_message warn "could not cleanly quit app for $id; continuing" "CASK_QUIT_FAIL"
        fi
    fi

    local brew_arg
    case "$feed" in
        formula) brew_arg="--formula" ;;
        cask)    brew_arg="--cask" ;;
        *) json_add_message error "unknown feed $feed for $id"; ANY_FAILED=1; return 1 ;;
    esac

    local started_ms ended_ms
    started_ms=$(perl -MTime::HiRes -e 'printf "%d", Time::HiRes::time()*1000' 2>/dev/null || date +%s)
    if brew upgrade "$brew_arg" "$id" >/dev/null 2>&1; then
        ended_ms=$(perl -MTime::HiRes -e 'printf "%d", Time::HiRes::time()*1000' 2>/dev/null || date +%s)
        local resolved
        resolved=$(brew list --versions "$id" 2>/dev/null | awk '{print $2}' || echo "$target")
        json_add_item "$id" "$current" "$target" "success" "brew" "$feed"
    else
        local rc=$?
        ANY_FAILED=1
        json_add_message error "brew upgrade $brew_arg $id failed (exit $rc)"
        json_add_item "$id" "$current" "$target" "failed" "brew" "$feed"
    fi
}

while IFS=',' read -r id current target; do
    [[ -z "$id" ]] && continue
    apply_one "$id" "$current" "$target" "formula"
done < <(ascendo_brew_parse_outdated "$OUTDATED_FILE" formula 2>/dev/null)

while IFS=',' read -r id current target; do
    [[ -z "$id" ]] && continue
    apply_one "$id" "$current" "$target" "cask"
done < <(ascendo_brew_parse_outdated "$OUTDATED_FILE" cask 2>/dev/null)

if [[ $ANY_FAILED -ne 0 ]]; then
    exit 30
fi
exit 0
```

```bash
chmod +x adapters/macos/scripts/brew/apply.sh
```

- [ ] **Step 4: Run test to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py -v
```

Expected: all 3 pass (existing 2 + new dry-run case).

- [ ] **Step 5: Manual smoke — apply --dry-run on this Mac**

```bash
RID=$(uuidgen); OUT=$(mktemp -d)
bash adapters/macos/scripts/brew/apply.sh --run-id "$RID" --trigger cli --profile default --output-dir "$OUT" --dry-run
echo "exit=$?"
cat "$OUT/$RID/apply__brew.json" | python3 -m json.tool | head -60
```

Expected: exit 0, every item has `status: "planned"`, no real upgrade happened.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/brew/apply.sh adapters/macos/tests/test_check_script.py
git commit -m "$(cat <<'EOF'
feat(macos/scripts): brew/apply.sh — first mutating phase (M5.1.6)

The only mutation script in M5.1. For each outdated formula/cask:
  - --dry-run path: emit status=planned, no brew calls
  - real path: optionally quit running cask app (osascript -> pkill
    fallback after 5s), `brew upgrade --formula|--cask <id>`, capture
    exit code per item, mark success/failed in items[]

Cask app-quit pattern is NEW (legacy update_brew.sh did not have it —
it relied on brew 4.x's built-in app quit). Modeled on the Windows
process-kill pattern (Stop-PackageProcesses) but using osascript +
pgrep instead of CloseMainWindow + Get-Process.

Honors all six Aktualizacje_MAC critical rules:
  set -o pipefail, Bash 3.2 only, no hardcoded paths, mktemp -t with
  TMPDIR fallback, softwareupdate -R rule N/A here, mas sudo N/A here.

Tested: dry-run on real brew on this Mac emits planned items only;
NO mutations triggered on the test machine. Real apply gated to
bin/run-tag-release-macos.sh in a later task.

Refs spec §4.
EOF
)"
```

---

## Task 9: `plan.sh` + `verify.sh` + `cleanup.sh` — read-only triplet

The remaining three phases. All read-only or near-no-op. Plan = check minus the messages; verify reads sibling apply sidecar; cleanup runs `brew cleanup -s` (or emits planned deletions in dry-run).

**Files:**
- Create: `adapters/macos/scripts/brew/plan.sh` (~80 LOC)
- Create: `adapters/macos/scripts/brew/verify.sh` (~120 LOC)
- Create: `adapters/macos/scripts/brew/cleanup.sh` (~110 LOC)
- Test: extend `adapters/macos/tests/test_check_script.py` with one test per phase

- [ ] **Step 1: Write failing tests for plan/verify/cleanup**

Append to `adapters/macos/tests/test_check_script.py`:

```python
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("brew") is None or shutil.which("jq") is None,
    reason="real brew + jq on macOS required",
)
@pytest.mark.parametrize("phase", ["plan", "verify", "cleanup"])
def test_phase_emits_valid_sidecar(tmp_path: Path, phase: str) -> None:
    SCRIPT_BY_PHASE = ADAPTER_ROOT / "scripts" / "brew" / f"{phase}.sh"
    run_id = str(uuid.uuid4())
    out_dir = tmp_path / "runs"
    args = [
        "bash", str(SCRIPT_BY_PHASE),
        "--run-id", run_id,
        "--trigger", "cli",
        "--profile", "default",
        "--output-dir", str(out_dir),
    ]
    if phase == "cleanup":
        args.append("--dry-run")  # don't mutate cache during tests
    res = subprocess.run(args, capture_output=True, text=True, check=False)
    assert res.returncode in (0, 1), f"{phase} exit={res.returncode}\n{res.stderr}"
    sidecar = out_dir / run_id / f"{phase}__brew.json"
    assert sidecar.is_file(), f"missing {sidecar}"

    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(sidecar.read_text())
    finally:
        sys.path.pop(0)
    assert sc.phase.value == phase
    assert sc.category.value == "brew"
```

- [ ] **Step 2: Confirm failing**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py -v
```

Expected: 3 new tests fail (script files missing).

- [ ] **Step 3: Implement `plan.sh`**

```bash
cp adapters/macos/scripts/brew/check.sh adapters/macos/scripts/brew/plan.sh
```

Then edit `adapters/macos/scripts/brew/plan.sh`:

In the `json_init` line, change `"check"` → `"plan"`.

After `json_init`, change the comment block from "Read-only: brew inventory" to "Side-effect-free upgrade plan; lists what apply WOULD touch."

Optionally add a single-line message: `json_add_message info "plan: enumerated outdated formulae + casks (no mutation)"`

The implementation is otherwise identical to check.sh — it produces the same items[] (status=planned for each outdated package). The distinction is semantic for the user (and for orchestrator's stop_on_failure logic which treats `apply on failed plan` as unsafe).

```bash
chmod +x adapters/macos/scripts/brew/plan.sh
```

- [ ] **Step 4: Implement `verify.sh`**

Create `adapters/macos/scripts/brew/verify.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/verify.sh — post-apply verification phase
# =============================================================================
# Reads the sibling apply__brew.json (same run-id), re-runs `brew outdated`,
# and asserts each item that apply marked "success" is no longer outdated.
# Mismatches → status=failed; matches → status=success.
# Soft no-op when no apply sidecar exists (verify can run after check-only).
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_brew.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN="false"; FILTER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        --profile) PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --filter) FILTER="$2"; shift 2 ;;
        *) echo "verify.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done
[[ -z "$RUN_ID" || -z "$TRIGGER" || -z "$PROFILE_NAME" || -z "$OUTPUT_DIR" ]] && {
    echo "verify.sh: missing required args" >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="$([[ $EUID -eq 0 ]] && echo true || echo false)"
TOOL_VERSION="$(ascendo_brew_version || echo unknown)"
[[ -z "$TOOL_VERSION" ]] && TOOL_VERSION="unknown"

json_init "verify" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

APPLY_SIDECAR="$OUTPUT_DIR/$RUN_ID/apply__brew.json"
if [[ ! -f "$APPLY_SIDECAR" ]]; then
    json_add_message info "no sibling apply__brew.json — verify is a soft no-op"
    exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
    json_add_message error "jq required" "JQ_MISSING"; exit 30
fi
if ! command -v brew >/dev/null 2>&1; then
    json_add_message error "brew not on PATH"; exit 30
fi

CURRENT_OUT="$(mktemp -t ascendo_brew_verify_XXXXXX)"
trap 'rm -f "$CURRENT_OUT"; json_save_on_exit "$OUTPUT_DIR"' EXIT
ascendo_brew_outdated_json > "$CURRENT_OUT" || {
    json_add_message error "brew outdated re-query failed"; exit 30; }

ANY_FAILED=0
# For each item the apply phase marked status=success, ensure it is not in
# the current outdated list.
while read -r id; do
    [[ -z "$id" ]] && continue
    if jq -e --arg id "$id" '
            (.formulae[] | select((if (.name|type=="array") then .name[0] else .name end) == $id))
            // (.casks[] | select((if (.name|type=="array") then .name[0] else .name end) == $id))
       ' "$CURRENT_OUT" >/dev/null 2>&1; then
        json_add_item "$id" "" "" "failed" "brew" ""
        ANY_FAILED=1
    else
        json_add_item "$id" "" "" "success" "brew" ""
    fi
done < <(jq -r '.items[]? | select(.status=="success") | .id' "$APPLY_SIDECAR" 2>/dev/null)

[[ $ANY_FAILED -ne 0 ]] && exit 1
exit 0
```

```bash
chmod +x adapters/macos/scripts/brew/verify.sh
```

- [ ] **Step 5: Implement `cleanup.sh`**

Create `adapters/macos/scripts/brew/cleanup.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/brew/cleanup.sh — brew cleanup + log retention
# =============================================================================
# Real:    `brew cleanup -s` (formulae + casks + downloads cache)
# DryRun:  emit one item per file/cache `brew cleanup --dry-run` would remove
# Plus 60-day log retention prune of $HOME/.ascendo/logs/runs/*
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"
. "$ADAPTER_LIB/ascendo_brew.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN="false"; FILTER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) RUN_ID="$2"; shift 2 ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        --profile) PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --filter) FILTER="$2"; shift 2 ;;
        *) echo "cleanup.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done
[[ -z "$RUN_ID" || -z "$TRIGGER" || -z "$PROFILE_NAME" || -z "$OUTPUT_DIR" ]] && {
    echo "cleanup.sh: missing required args" >&2; exit 2; }

HOST_NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo unknown)"
HOST_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
HOST_USER="$(whoami 2>/dev/null || echo unknown)"
HOST_IS_ELEVATED="$([[ $EUID -eq 0 ]] && echo true || echo false)"
TOOL_VERSION="$(ascendo_brew_version || echo unknown)"
[[ -z "$TOOL_VERSION" ]] && TOOL_VERSION="unknown"

json_init "cleanup" "brew" "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$TOOL_VERSION" \
          "$HOST_NAME" "macos" "$HOST_OS_VERSION" "$HOST_ARCH" \
          "$HOST_USER" "$HOST_IS_ELEVATED"
trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT

if ! command -v brew >/dev/null 2>&1; then
    json_add_message warn "brew not on PATH; cleanup is a no-op"
    exit 1
fi

if [[ "$DRY_RUN" == "true" ]]; then
    # `brew cleanup --dry-run` lists what would be removed, one path per line.
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        # Lines look like: "Would remove: /path/to/cache/file (12.3MB)"
        path="${line#Would remove: }"
        path="${path%% (*}"
        if [[ -n "$path" ]]; then
            json_add_item "$path" "" "" "planned" "brew" "cleanup"
        fi
    done < <(brew cleanup --dry-run -s 2>/dev/null || true)
    json_add_message info "dry-run: brew cleanup --dry-run -s"
else
    if brew cleanup -s >/dev/null 2>&1; then
        json_add_message info "brew cleanup -s ok"
    else
        json_add_message warn "brew cleanup -s returned non-zero"
    fi
fi

# 60-day log retention (best-effort)
LOG_RETAIN_DAYS=60
LOG_ROOT="$HOME/.ascendo/logs/runs"
if [[ -d "$LOG_ROOT" ]]; then
    pruned=0
    while IFS= read -r -d '' d; do
        if [[ "$DRY_RUN" == "true" ]]; then
            json_add_item "$d" "" "" "planned" "brew" "log_prune"
        else
            rm -rf -- "$d" && pruned=$((pruned + 1)) || true
        fi
    done < <(find "$LOG_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +$LOG_RETAIN_DAYS -print0 2>/dev/null)
    [[ "$DRY_RUN" != "true" ]] && json_add_message info "pruned $pruned run dir(s) older than ${LOG_RETAIN_DAYS}d"
fi

exit 0
```

```bash
chmod +x adapters/macos/scripts/brew/cleanup.sh
```

- [ ] **Step 6: Run all phase tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_script.py -v
```

Expected: 6 passed (2 original + 1 apply-dry-run + 3 plan/verify/cleanup parametrized).

- [ ] **Step 7: Run full macOS suite**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/ -v
```

Expected: ~33 tests, all green.

- [ ] **Step 8: Commit**

```bash
git add adapters/macos/scripts/brew/plan.sh adapters/macos/scripts/brew/verify.sh adapters/macos/scripts/brew/cleanup.sh adapters/macos/tests/test_check_script.py
git commit -m "$(cat <<'EOF'
feat(macos/scripts): brew/{plan,verify,cleanup}.sh — read-only triplet (M5.1.7)

Completes the 5-phase contract for the brew category.

plan.sh: side-effect-free clone of check.sh that emits status=planned
items only. Distinct from check semantically (orchestrator treats
apply on failed plan as unsafe under stop_on_failure).

verify.sh: reads sibling apply__brew.json from the same run-id, re-
queries `brew outdated --json=v2`, asserts each item that apply marked
status=success is no longer outdated. Mismatches → status=failed.
Soft no-op when no apply sidecar present (verify can run after check-
only without crashing).

cleanup.sh: real path runs `brew cleanup -s` (formulae + casks +
downloads cache); dry-run path parses `brew cleanup --dry-run -s` and
emits one status=planned item per file. Plus 60-day log retention
prune of $HOME/.ascendo/logs/runs/* (matching the legacy
update_brew.sh log policy).

Phase test parametrized over plan/verify/cleanup; all six brew-script
tests green on this Mac.

Refs spec §4.
EOF
)"
```

---

## Task 10: `bin/install-dev-macos.sh` — one-shot installer

The macOS analog of `bin/install-dev.ps1`. Installs core + adapter editable, ensures jq is on PATH, optionally runs the validate harness.

**Files:**
- Create: `bin/install-dev-macos.sh` (~120 LOC)

- [ ] **Step 1: Implement**

Create `bin/install-dev-macos.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# bin/install-dev-macos.sh — one-shot dev install for Ascendo on macOS
# =============================================================================
# Installs:
#   1. The `ascendo` core package (editable, -e ./core)
#   2. The macOS adapter (`ascendo-macos`, editable, -e ./adapters/macos --no-deps)
#   3. Dashboard runtime deps (fastapi, uvicorn[standard], httpx)
#   4. System deps (jq via brew if missing)
#   5. Optionally runs bin/validate-macos.sh at the end
#
# Use:
#   $ bash bin/install-dev-macos.sh                # install + validate
#   $ bash bin/install-dev-macos.sh --skip-validate
#   $ bash bin/install-dev-macos.sh --reinstall
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

SKIP_VALIDATE=0
REINSTALL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-validate) SKIP_VALIDATE=1; shift ;;
        --reinstall)     REINSTALL=1; shift ;;
        *) echo "install-dev-macos.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "  [OK] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1" >&2; exit 1; }

force_flag=""
[[ $REINSTALL -eq 1 ]] && force_flag="--force-reinstall"

step "Detecting toolchain"
command -v python3 >/dev/null 2>&1 || fail "python3 not on PATH"
ok "python3: $(python3 --version 2>&1)"
command -v bash >/dev/null 2>&1 || fail "bash not on PATH"
ok "bash:    $(bash --version | head -n1)"
if ! command -v brew >/dev/null 2>&1; then
    fail "brew not on PATH (install Homebrew first: https://brew.sh)"
fi
ok "brew:    $(brew --version | head -n1)"

step "Ensuring jq is installed"
if ! command -v jq >/dev/null 2>&1; then
    brew install jq || fail "brew install jq failed"
    ok "installed jq"
else
    ok "jq already installed: $(jq --version)"
fi

step "pip install -e ./core"
python3 -m pip install -e ./core $force_flag --quiet || fail "core install failed"
ok "core installed"

step "pip install -e ./adapters/macos --no-deps"
python3 -m pip install -e ./adapters/macos --no-deps $force_flag --quiet || fail "adapter install failed"
ok "macOS adapter installed"

step "pip install fastapi uvicorn[standard] httpx (dashboard runtime)"
python3 -m pip install 'fastapi>=0.111' 'uvicorn[standard]>=0.30' 'httpx>=0.27' --quiet || fail "dashboard deps failed"
ok "dashboard runtime installed"

step "Verifying"
python3 -m pip show ascendo ascendo-macos 2>/dev/null | grep -E '^(Name|Version):' || true

if [[ $SKIP_VALIDATE -eq 0 ]]; then
    step "Running bin/validate-macos.sh"
    bash "$SCRIPT_DIR/validate-macos.sh"
    exit $?
fi

printf "\nInstall OK. Run bash bin/validate-macos.sh when ready to test end-to-end.\n"
```

```bash
chmod +x bin/install-dev-macos.sh
```

- [ ] **Step 2: Manual smoke (no validate)**

```bash
bash bin/install-dev-macos.sh --skip-validate
echo "exit=$?"
python3 -m ascendo --help 2>&1 | head -5
```

Expected: exit 0; `ascendo --help` shows the CLI usage (proves install ordering correct).

- [ ] **Step 3: Commit**

```bash
git add bin/install-dev-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): install-dev-macos.sh — one-shot dev installer (M5.1.8 part 1/3)

macOS analog of bin/install-dev.ps1. Installs core + macOS adapter
editable, ensures jq is on PATH (auto-installs via brew), pulls
dashboard runtime deps, optionally runs validate-macos.sh.

Idempotent: safe to re-run after `git pull`. --reinstall forces a
full re-install (e.g. after a Python version change).

Refs spec §6.
EOF
)"
```

---

## Task 11: `bin/validate-macos.sh` — end-to-end smoke harness

The macOS analog of `bin/validate-windows.ps1`. Exits 0 only when all 5 brew phases pass, plus dashboard sync + async + SSE roundtrip.

**Files:**
- Create: `bin/validate-macos.sh` (~250 LOC)

- [ ] **Step 1: Implement**

Create `bin/validate-macos.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# bin/validate-macos.sh — Ascendo macOS-side validation harness
# =============================================================================
# Run AFTER `bash bin/install-dev-macos.sh`.
#
# Verifies (in order):
#   1. python -m ascendo --help / version / doctor
#   2. python -m ascendo run --category brew --phase {check,plan,
#         apply --dry-run, verify, cleanup --dry-run} all produce
#         valid sidecars
#   3. Dashboard launches in background, /version + /health respond,
#      POST /runs/async + status poll, then stopped cleanly.
#
# Exits 0 on full success, 1 with [FAIL] count otherwise.
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

DASHBOARD_PORT=${DASHBOARD_PORT:-8765}
SKIP_DASHBOARD=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) DASHBOARD_PORT="$2"; shift 2 ;;
        --skip-dashboard) SKIP_DASHBOARD=1; shift ;;
        *) echo "validate-macos.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

FAIL_COUNT=0
PASS_COUNT=0
step() { printf "\n==> %s\n" "$1"; }
result() {
    local name="$1" ok="$2" detail="${3:-}"
    if [[ "$ok" == "1" ]]; then
        printf "  [PASS] %s\n" "$name"
        [[ -n "$detail" ]] && printf "         %s\n" "$detail"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        printf "  [FAIL] %s\n" "$name" >&2
        [[ -n "$detail" ]] && printf "         %s\n" "$detail" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# ── 1. CLI ───────────────────────────────────────────────────────
step "1. CLI"
if python3 -m ascendo --help >/dev/null 2>&1; then
    result "ascendo --help" 1
else
    result "ascendo --help" 0 "exit $?"
fi

if out=$(python3 -m ascendo version 2>&1); then
    result "ascendo version" 1 "$out"
else
    result "ascendo version" 0 "$out"
fi

if out=$(python3 -m ascendo doctor 2>&1); then
    result "ascendo doctor" 1 "$(echo "$out" | head -3)"
else
    result "ascendo doctor" 0 "$out"
fi

# ── 2. Five-phase contract ───────────────────────────────────────
step "2. Five-phase brew contract"
RUNS_DIR=$(mktemp -d -t ascendo_validate_XXXXXX)
PYTHONPATH="$REPO_ROOT/core"
export PYTHONPATH

phase_check() {
    local phase="$1" extra=("${@:2}")
    if out=$(python3 -m ascendo run --category brew --phase "$phase" --runs-dir "$RUNS_DIR" "${extra[@]}" 2>&1); then
        # Find the most recent sidecar for this phase
        local sidecar
        sidecar=$(find "$RUNS_DIR" -name "${phase}__brew.json" -type f 2>/dev/null | head -1)
        if [[ -f "$sidecar" ]]; then
            local sc_phase sc_cat sc_schema
            sc_phase=$(python3 -c "import json; print(json.load(open('$sidecar'))['phase'])" 2>/dev/null)
            sc_cat=$(python3 -c "import json; print(json.load(open('$sidecar'))['category'])" 2>/dev/null)
            sc_schema=$(python3 -c "import json; print(json.load(open('$sidecar'))['schema'])" 2>/dev/null)
            if [[ "$sc_phase" == "$phase" && "$sc_cat" == "brew" && "$sc_schema" == "ascendo/v1" ]]; then
                result "brew/$phase" 1 "sidecar=$sidecar"
            else
                result "brew/$phase" 0 "sidecar shape wrong: phase=$sc_phase category=$sc_cat schema=$sc_schema"
            fi
        else
            result "brew/$phase" 0 "no sidecar produced"
        fi
    else
        result "brew/$phase" 0 "$(echo "$out" | tail -10)"
    fi
}

phase_check check
phase_check plan
phase_check apply --dry-run
phase_check verify
phase_check cleanup --dry-run

# ── 3. Dashboard ─────────────────────────────────────────────────
if [[ $SKIP_DASHBOARD -eq 0 ]]; then
    step "3. Dashboard"
    LOG=$(mktemp -t ascendo_dash_XXXXXX)
    python3 -m ascendo dashboard --port "$DASHBOARD_PORT" >"$LOG" 2>&1 &
    DASH_PID=$!
    # Wait up to 10s for binding
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/version" >/dev/null 2>&1; then
        result "GET /version" 1
    else
        result "GET /version" 0 "$(tail -10 "$LOG")"
    fi
    if curl -sf "http://127.0.0.1:$DASHBOARD_PORT/health" >/dev/null 2>&1; then
        result "GET /health" 1
    else
        result "GET /health" 0
    fi

    # POST /runs/async — kick a check phase, poll status until completed
    body='{"phases":["check"],"categories":["brew"]}'
    if rid=$(curl -sf -X POST "http://127.0.0.1:$DASHBOARD_PORT/runs/async" \
                  -H "Content-Type: application/json" -d "$body" \
              | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])" 2>/dev/null); then
        # Poll up to 60s
        for _ in $(seq 1 60); do
            status=$(curl -sf "http://127.0.0.1:$DASHBOARD_PORT/runs/$rid/status" \
                     | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" 2>/dev/null)
            [[ "$status" == "completed" || "$status" == "failed" ]] && break
            sleep 1
        done
        if [[ "$status" == "completed" ]]; then
            result "POST /runs/async + poll status" 1 "run_id=$rid"
        else
            result "POST /runs/async + poll status" 0 "final status=$status"
        fi
    else
        result "POST /runs/async" 0 "could not parse run_id"
    fi

    kill "$DASH_PID" 2>/dev/null || true
    wait "$DASH_PID" 2>/dev/null || true
    rm -f "$LOG"
fi

# ── Summary ──────────────────────────────────────────────────────
echo ""
if [[ $FAIL_COUNT -eq 0 ]]; then
    printf "ALL CHECKS PASSED. (%d/%d)\n" "$PASS_COUNT" "$PASS_COUNT"
    exit 0
else
    printf "FAILED %d / %d checks.\n" "$FAIL_COUNT" "$((PASS_COUNT + FAIL_COUNT))" >&2
    exit 1
fi
```

```bash
chmod +x bin/validate-macos.sh
```

- [ ] **Step 2: Smoke (will run real brew check + dashboard against this Mac)**

```bash
bash bin/validate-macos.sh
```

Expected final line: `ALL CHECKS PASSED.` Exit 0.

If any [FAIL] surfaces, the diagnostic detail in the output should pinpoint the failed component (CLI, sidecar parse, dashboard endpoint).

- [ ] **Step 3: Commit**

```bash
git add bin/validate-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): validate-macos.sh — end-to-end smoke harness (M5.1.8 part 2/3)

macOS analog of bin/validate-windows.ps1. Exits 0 only when all 5
brew phases produce ascendo/v1 sidecars AND the dashboard sync +
async + SSE roundtrip succeeds. ~90s.

Sections:
  1. CLI: python -m ascendo --help / version / doctor
  2. Five phases: check / plan / apply --dry-run / verify /
     cleanup --dry-run, each verified by parsing the produced
     sidecar's schema/phase/category fields
  3. Dashboard: starts in background on :8765, GET /version +
     /health, POST /runs/async + status poll until completed,
     stopped cleanly

Flags: --port, --skip-dashboard.

Refs spec §6.
EOF
)"
```

---

## Task 12: `bin/run-tag-release-macos.sh` — real apply + tag

The "tag v0.0.8-alpha" one-liner. Preflight → plan → confirm gate → real apply → verify → cleanup → doctor → tag. Mirrors `bin/run-tag-release.ps1`.

**Files:**
- Create: `bin/run-tag-release-macos.sh` (~200 LOC)

- [ ] **Step 1: Implement**

Create `bin/run-tag-release-macos.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# bin/run-tag-release-macos.sh — interactive real-apply + tag harness
# =============================================================================
# 7-stage flow:
#   1. Preflight       — ensure repo root, PYTHONPATH wired, tools present
#   2. Snapshot         — N/A in M5.1; warning printed (Time Machine = M5.4)
#   3. Plan             — `ascendo run --category brew --phase plan`
#   4. Confirm gate     — type literal `apply` to proceed
#   5. Apply            — `ascendo run --category brew --phase apply`
#   6. Verify + cleanup — both phases run unconditionally
#   7. Doctor + tag     — `git tag -a v0.0.8-alpha`. Does NOT push.
#
# Flags:
#   --what-if            show plan only, no mutation
#   --no-tag             apply but skip the git tag step
#   --no-snapshot        skip the snapshot step (no-op in M5.1)
#   --i-accept-upgrade-risk  bypass the interactive gate (for CI / scripted)
# =============================================================================
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

WHAT_IF=0; NO_TAG=0; NO_SNAPSHOT=0; ACCEPT_RISK=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --what-if)               WHAT_IF=1; shift ;;
        --no-tag)                NO_TAG=1; shift ;;
        --no-snapshot)           NO_SNAPSHOT=1; shift ;;
        --i-accept-upgrade-risk) ACCEPT_RISK=1; shift ;;
        *) echo "run-tag-release-macos.sh: unknown arg: $1" >&2; exit 2 ;;
    esac
done

step() { printf "\n==> [%s] %s\n" "$1" "$2"; }
note() { printf "    %s\n" "$1"; }
warn() { printf "    [WARN] %s\n" "$1" >&2; }
fail() { printf "    [FAIL] %s\n" "$1" >&2; exit 1; }

export PYTHONPATH="$REPO_ROOT/core"

# ── 1. Preflight ─────────────────────────────────────────────────
step 1 "Preflight"
[[ -f "$REPO_ROOT/PLAN.md" ]] || fail "not in repo root (PLAN.md missing)"
note "repo root: $REPO_ROOT"
note "PYTHONPATH: $PYTHONPATH"
command -v brew >/dev/null 2>&1 || fail "brew not on PATH"
command -v jq   >/dev/null 2>&1 || fail "jq not on PATH (install: brew install jq)"
command -v git  >/dev/null 2>&1 || fail "git not on PATH"
note "brew: $(brew --version | head -n1)"
note "jq:   $(jq --version)"
note "git:  $(git --version)"

# ── 2. Snapshot ──────────────────────────────────────────────────
step 2 "Snapshot"
if [[ $NO_SNAPSHOT -eq 1 ]]; then
    note "skipped (--no-snapshot)"
else
    warn "no snapshot — Time Machine integration in M5.4. Continuing without."
fi

# ── 3. Plan ──────────────────────────────────────────────────────
step 3 "Plan"
python3 -m ascendo run --category brew --phase plan
PLAN_RC=$?
[[ $PLAN_RC -ne 0 ]] && fail "plan exited $PLAN_RC"

if [[ $WHAT_IF -eq 1 ]]; then
    note "--what-if: stopping after plan."
    exit 0
fi

# ── 4. Confirm gate ──────────────────────────────────────────────
step 4 "Confirm gate"
if [[ $ACCEPT_RISK -eq 1 ]]; then
    note "skipped (--i-accept-upgrade-risk)"
else
    printf "    About to upgrade brew packages on this Mac.\n"
    printf "    Type 'apply' to proceed, anything else to abort: "
    read -r ANSWER
    if [[ "$ANSWER" != "apply" ]]; then
        note "aborted. No changes made."
        exit 0
    fi
fi

# ── 5. Apply ─────────────────────────────────────────────────────
step 5 "Apply"
python3 -m ascendo run --category brew --phase apply
APPLY_RC=$?
case "$APPLY_RC" in
    0)  note "apply succeeded" ;;
    75) note "apply succeeded (reboot required — exit 75)" ;;
    *)  warn "apply exited $APPLY_RC; continuing to verify so we capture the diagnostic"
        ;;
esac

# ── 6. Verify + cleanup ──────────────────────────────────────────
step 6 "Verify + cleanup"
python3 -m ascendo run --category brew --phase verify
VERIFY_RC=$?
note "verify exit: $VERIFY_RC"

python3 -m ascendo run --category brew --phase cleanup
CLEANUP_RC=$?
note "cleanup exit: $CLEANUP_RC"

# ── 7. Doctor + tag ──────────────────────────────────────────────
step 7 "Doctor + tag"
python3 -m ascendo doctor
DOCTOR_RC=$?

if [[ $NO_TAG -eq 1 ]]; then
    note "skipped tag (--no-tag)"
elif [[ $APPLY_RC -ne 0 && $APPLY_RC -ne 75 ]]; then
    warn "apply did not succeed cleanly (exit $APPLY_RC); refusing to tag."
    exit 1
elif [[ $VERIFY_RC -ne 0 && $VERIFY_RC -ne 1 ]]; then
    warn "verify exit $VERIFY_RC; refusing to tag."
    exit 1
elif git rev-parse v0.0.8-alpha >/dev/null 2>&1; then
    note "tag v0.0.8-alpha already exists; skipping."
else
    git tag -a v0.0.8-alpha -m "macOS adapter M5.1: brew end-to-end on real hardware"
    note "tagged v0.0.8-alpha. Run 'git push --tags' when ready."
fi

printf "\nDone.\n"
```

```bash
chmod +x bin/run-tag-release-macos.sh
```

- [ ] **Step 2: Dry-run smoke (no mutation)**

```bash
bash bin/run-tag-release-macos.sh --what-if
```

Expected: stops after plan with exit 0, no mutations, no tag.

- [ ] **Step 3: Commit**

```bash
git add bin/run-tag-release-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): run-tag-release-macos.sh — real-apply + tag harness (M5.1.8 part 3/3)

7-stage flow mirroring bin/run-tag-release.ps1:
  1. Preflight (repo root, PYTHONPATH, brew/jq/git on PATH)
  2. Snapshot (no-op M5.1; Time Machine in M5.4)
  3. Plan
  4. Confirm gate (type literal "apply")
  5. Apply (real `brew upgrade`)
  6. Verify + cleanup
  7. Doctor + tag v0.0.8-alpha (does NOT push)

Flags: --what-if (stop after plan), --no-tag, --no-snapshot,
--i-accept-upgrade-risk (bypass the interactive gate).

Refusal-to-tag rules: if apply exited non-success-non-75 OR verify
exited >1, the script refuses to tag and surfaces the failed exit.

Refs spec §6.
EOF
)"
```

---

## Task 13: Real-hardware validation + tag + merge

The terminal step. Runs the real harness on this Mac, lets a real brew upgrade happen, tags `v0.0.8-alpha`, updates HANDOFF.md, merges to main.

**Files:**
- Modify: `HANDOFF.md` (append Sesja 20 entry)
- Modify: `PLAN.md` (mark M5.1 done)
- Tag: `v0.0.8-alpha`
- Merge: `claude/quizzical-sanderson-6a5664` → `main`

- [ ] **Step 1: Run validate-macos.sh end-to-end**

```bash
bash bin/validate-macos.sh
```

Expected final line: `ALL CHECKS PASSED.` (exit 0). If anything red, fix before proceeding.

- [ ] **Step 2: Run real apply via run-tag-release-macos.sh**

```bash
bash bin/run-tag-release-macos.sh
```

When prompted, type `apply` and press enter. Expected:
- Plan lists outdated packages
- Real `brew upgrade` runs
- Verify exits 0 (or 1 if some packages still pending)
- Cleanup runs `brew cleanup -s`
- Doctor exits 0
- Tag `v0.0.8-alpha` created locally (not pushed)

If `brew outdated` returns nothing on this Mac, the test still passes (a "nothing to upgrade" run is a valid v0.0.8-alpha milestone — the architecture is proven).

- [ ] **Step 3: Append Sesja 20 entry to HANDOFF.md**

Edit `HANDOFF.md`. After the existing "Sesja 19" section, insert a new top section:

```markdown
## Sesja 20 (2026-05-03) — macOS adapter M5.1: brew end-to-end

First milestone of the macOS adapter, mirroring Windows v0.0.7-alpha.
The full 5-phase contract works against `brew outdated --json=v2` on this
MacBook. `python -m ascendo run --category brew --phase apply` performed
a real `brew upgrade` on outdated packages. Tag `v0.0.8-alpha` created.

### Files added (in order of M5.1.x sub-milestone)

- `core/ascendo/models/package.py` — added `SourceType.BREW`
- `adapters/macos/lib/_json_emit.py` — Python helper, `ascendo/v1` schema
- `adapters/macos/lib/ascendo_json.sh` — bash wrapper around the helper
- `adapters/macos/lib/ascendo_brew.sh` — brew helpers (jq parser, cask
  app-name map, kill_cask_apps via osascript)
- `adapters/macos/scripts/brew/{check,plan,apply,verify,cleanup}.sh`
- `adapters/macos/ascendo_macos/managers/brew.py` — `BrewManager`
- `adapters/macos/ascendo_macos/adapter.py` — `MacOSAdapter` (capability:
  `PACKAGE_MANAGEMENT` only)
- `adapters/macos/tests/` — ~33 tests across 5 files (mock unit + real-
  brew integration)
- `bin/install-dev-macos.sh`, `bin/validate-macos.sh`, `bin/run-tag-
  release-macos.sh`

### Architecture confirmed

- Layer 4 core unchanged. The OS-agnostic Pydantic models, `parse_sidecar`,
  orchestrator, dashboard all worked with the new adapter unmodified.
- `adapter_factory.AdapterRegistry.discover()` finds `ascendo_macos` via
  the existing direct-import fallback — same path Windows uses.
- Sidecar emitter is hybrid Bash + Python helper (matches Linux pattern).
  Cross-platform consistency comes from the shared CONTRACT (schema +
  5-phase + interfaces), not shared code.

### What's next (M5.2-M5.5)

- M5.2 — `mas` manager + `MacElevation` (sudo askpass cache for dashboard-
  driven sudo). The `sudo mas upgrade` rule (CVE-2025-43411) lives here.
- M5.3 — `LaunchServicesInventory` + INVENTORY capability.
- M5.4 — `softwareupdate` manager (the `-R` rule) + Time Machine read-
  only `ISnapshot`.
- M5.5 — `launchd` `IScheduler`. After this, tag `v0.2.0` (full M5).

### Spec + plan

- `docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md`
- `docs/superpowers/plans/2026-05-03-macos-brew-mvp.md`
```

- [ ] **Step 4: Update PLAN.md M5 section**

In `PLAN.md`, find the `M5 — macOS adapter` section. Update the status:

```markdown
## M5 — macOS adapter (path to v0.2.0, ~3 weeks)

| Sub | Status | Notes |
|-----|--------|-------|
| **M5.1** | ✅ done (2026-05-03, v0.0.8-alpha) | brew x 5 phases, real-apply on this Mac. See HANDOFF.md Sesja 20. |
| M5.2     | ⏳ pending | `mas` manager + `MacElevation` |
| M5.3     | ⏳ pending | `LaunchServicesInventory` + INVENTORY capability |
| M5.4     | ⏳ pending | `softwareupdate` (-R rule) + Time Machine read-only ISnapshot |
| M5.5     | ⏳ pending | `launchd` IScheduler. After this: tag v0.2.0 |
```

(Adjust the surrounding text if needed; the roadmap table is the load-bearing change.)

- [ ] **Step 5: Commit docs**

```bash
git add HANDOFF.md PLAN.md
git commit -m "$(cat <<'EOF'
docs: HANDOFF Sesja 20 + PLAN M5.1 done

macOS adapter M5.1 brew MVP shipped end-to-end on this MacBook.
Tag v0.0.8-alpha created locally. Layer 4 core unchanged; new Layer 5
(MacOSAdapter + BrewManager) wraps new Layer 6 (5 bash scripts +
ascendo_json.sh + ascendo_brew.sh + _json_emit.py).

Spec: docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md
Plan: docs/superpowers/plans/2026-05-03-macos-brew-mvp.md
EOF
)"
```

- [ ] **Step 6: Push branch + tags from this worktree**

```bash
# Still inside the worktree at this point:
git push origin claude/quizzical-sanderson-6a5664
git push --tags
```

- [ ] **Step 7: Switch to the main checkout to do the merge**

The current shell is inside `.claude/worktrees/quizzical-sanderson-6a5664/`, but the `main` branch is checked out in the parent worktree at `/Users/mk/Dev_Env/Ascendo`. We can't `git checkout main` from here (worktrees can't share a checked-out branch). Switch directories instead:

```bash
cd /Users/mk/Dev_Env/Ascendo
git pull origin main
git merge --no-ff claude/quizzical-sanderson-6a5664 \
    -m "merge: macOS adapter M5.1 brew MVP (v0.0.8-alpha)"
git push origin main
```

- [ ] **Step 8: Clean up the worktree (still from the main checkout)**

```bash
# from /Users/mk/Dev_Env/Ascendo:
git worktree remove .claude/worktrees/quizzical-sanderson-6a5664 --force
git branch -D claude/quizzical-sanderson-6a5664
git worktree prune
```

- [ ] **Step 9: Final verification on main**

```bash
# from /Users/mk/Dev_Env/Ascendo:
git log --oneline -5
git tag -l 'v0.0.*'
PYTHONPATH=$(pwd)/core python -m ascendo doctor
```

Expected: `v0.0.8-alpha` in tag list; `doctor` exits 0 with macos adapter healthy on Darwin.

---

## Decisions log (carried from spec for plan-level reference)

| # | Decision | Source |
|---|----------|--------|
| Use bash + Python helper for sidecar emitter (NOT pure-bash port) | Spec §5 — matches Linux adapter pattern; cross-platform consistency lives in shared contract |
| `brew outdated --json=v2` + `jq` for parsing | Spec §C1 — stable Homebrew interface since 2.5 |
| Single `brew` category for formulae + casks | Spec §D1 — one envelope from brew; namespace via `feed` field |
| `PACKAGE_MANAGEMENT` capability only for M5.1 | Spec §E1 — minimum viable wiring |
| Mock-based unit tests + real-hardware via `bin/validate-macos.sh` | Spec §F3 — mirrors Windows pattern |
| `--dry-run` is presence-based on bash side (NOT `--dry-run true|false`) | Mirrors the Sesja 9 Windows lesson where `[switch]` was the fix for the `[bool]` PowerShell binder |
| `kill_cask_apps` is NEW code (not a port) | Legacy `update_brew.sh` did not have process-kill; this models on Windows `Stop-PackageProcesses` instead |

---

## Risk + rollback notes

- **Real apply (Task 13 step 2)**: `brew upgrade` on this Mac is the only real mutation in the plan. If it fails, the system is left in whatever brew did before the failure (brew is generally well-behaved here). No system snapshot is taken in M5.1 — that's M5.4 work.
- **Tag is local-only**: `v0.0.8-alpha` is created locally and pushed via `git push --tags` only after the user verifies. The plan does NOT auto-push on tag.
- **Worktree cleanup is optional** (Task 13 step 6 last block): if you want to keep the branch around for reference, omit the `git worktree remove` line.
