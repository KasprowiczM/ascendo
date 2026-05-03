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

    assert sc.schema_.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"


def test_finalize_tolerates_truncated_jsonl(tmp_path: Path) -> None:
    """A SIGKILL'd phase script can leave a partial JSON object on the last
    line of items.jsonl. Finalize must skip the bad line, surface a warning
    on stderr, and produce a valid sidecar with the intact items.
    """
    bufdir = tmp_path / "buf"
    out = tmp_path / "check__brew.json"

    _run([
        "init", "--bufdir", str(bufdir),
        "--phase", "check", "--category", "brew",
        "--run-id", "00000000-0000-0000-0000-000000000007",
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

    # Corrupt the file by appending a truncated half-line -- simulates the
    # write being interrupted mid-JSON.
    items_path = bufdir / "items.jsonl"
    with items_path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "git", "status": "plan')   # no closing brace, no newline

    res = _run([
        "finalize", "--bufdir", str(bufdir),
        "--out", str(out),
        "--exit-code", "0",
        "--ended-at", "2026-05-03T12:00:01Z",
    ], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert out.is_file()

    # The truncated line must be skipped and the warning must surface on stderr.
    assert "skipping malformed" in (res.stderr or ""), \
        f"expected truncated-line warning on stderr; got: {res.stderr!r}"

    # Round-trip must still succeed with the one good item.
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        sc = parse_sidecar(out.read_text())
    finally:
        sys.path.pop(0)
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"
