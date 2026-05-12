"""Tests for the _BaseWindowsManager sidecar salvage mixin (Windows-side
mirror of Ubuntu adapter's Sesja 56 mechanism).

The salvage path activates when a PowerShell phase script dies before
its Save-Sidecar call lands — e.g. signal, parse error, kernel kill,
StackOverflowException. The Python manager pre-allocates a bufdir; the
PS-side AscendoJson appends each item/message to JSONL files in that
dir as it goes. On crash, the Python manager reconstructs a sidecar
from whatever survived in the bufdir.

These tests cover the Python helpers directly (file IO, status logic,
schema synthesis) plus the integration point in WingetManager.run_phase.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.result import ItemStatus
from ascendo.models.run import Phase, PhaseStatus, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo_windows.managers._base import _BaseWindowsManager
from ascendo_windows.managers.arp import ArpManager
from ascendo_windows.managers.msstore import MSStoreManager
from ascendo_windows.managers.npm import NpmManager
from ascendo_windows.managers.pip import PipManager
from ascendo_windows.managers.web import WebManager
from ascendo_windows.managers.windows_update import WindowsUpdateManager
from ascendo_windows.managers.winget import WingetManager


# ── Test fixtures + helpers ───────────────────────────────────────────


class _StubMixinHost(_BaseWindowsManager):
    """Concrete subclass for testing mixin methods in isolation.

    The mixin is otherwise abstract — it expects ``self.category``
    etc. to be supplied by a real IPackageManager subclass.
    """

    pass


def _stub() -> _StubMixinHost:
    return _StubMixinHost()


def _build_meta_payload(
    *,
    run_id: str,
    phase: Phase = Phase.CHECK,
    category: str = "winget",
    started_at: str | None = None,
) -> dict[str, Any]:
    if started_at is None:
        started_at = "2026-05-12T12:00:00.000000Z"
    return {
        "schema": "ascendo/v1",
        "run": {
            "id": run_id,
            "trigger": "cli",
            "profile": "full",
            "dry_run": False,
            "started_at": started_at,
            "finished_at": None,
            "invocation": "ascendo run --category winget --phase check",
        },
        "host": {
            "hostname": "DP5520WMK",
            "os": "windows",
            "os_version": "11 Pro 26200",
            "arch": "x86_64",
            "user": "MK",
            "is_elevated": False,
            "elevation_method": "none",
            "locale": "en-US",
        },
        "tool": {
            "name": "winget",
            "version": "v1.28.240",
            "binary_path": "C:\\winget.exe",
        },
        "phase": phase.value,
        "category": category,
        "started_at": started_at,
    }


def _write_meta(bufdir: Path, payload: dict[str, Any]) -> None:
    (bufdir / "meta.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(bufdir: Path, name: str, rows: list[dict[str, Any]]) -> None:
    (bufdir / name).write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _build_item(
    *,
    pid: str = "Microsoft.PowerShell",
    name: str = "PowerShell",
    status: str = "success",
    current: str | None = "7.5.0",
    target: str | None = "7.6.0",
    resolved: str | None = "7.6.0",
) -> dict[str, Any]:
    return {
        "id": pid,
        "name": name,
        "category": "winget",
        "source": {"type": "winget", "feed": "winget", "url": None},
        "current_version": current,
        "target_version": target,
        "resolved_version": resolved,
        "status": status,
        "exit_code": 0,
        "duration_ms": 120,
        "evidence": None,
        "rollback": None,
        "messages": [],
    }


# ── 1. _allocate_bufdir ───────────────────────────────────────────────


def test_allocate_bufdir_creates_directory(tmp_path: Path) -> None:
    """The bufdir path is created on disk."""
    rid = "12345678-1234-1234-1234-123456789abc"
    bufdir = _stub()._allocate_bufdir(
        rid, Phase.CHECK, "winget", tmp_path,
    )
    assert bufdir.exists()
    assert bufdir.is_dir()
    assert bufdir.name == "check__winget"
    assert bufdir.parent.name == ".bufdir"
    assert bufdir.parent.parent.name == rid


def test_allocate_bufdir_is_idempotent(tmp_path: Path) -> None:
    """Calling twice for the same (run, phase, category) is safe."""
    rid = "12345678-1234-1234-1234-123456789abc"
    stub = _stub()
    first = stub._allocate_bufdir(rid, Phase.APPLY, "msstore", tmp_path)
    second = stub._allocate_bufdir(rid, Phase.APPLY, "msstore", tmp_path)
    assert first == second
    # Pre-existing file inside the bufdir survives the second allocate.
    marker = first / "marker.txt"
    marker.write_text("preserved", encoding="utf-8")
    third = stub._allocate_bufdir(rid, Phase.APPLY, "msstore", tmp_path)
    assert third == first
    assert marker.read_text(encoding="utf-8") == "preserved"


# ── 2. _read_jsonl_safely ─────────────────────────────────────────────


def test_read_jsonl_safely_with_missing_file(tmp_path: Path) -> None:
    out = _BaseWindowsManager._read_jsonl_safely(tmp_path / "missing.jsonl")
    assert out == []


def test_read_jsonl_safely_with_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert _BaseWindowsManager._read_jsonl_safely(p) == []


def test_read_jsonl_safely_with_malformed_lines(tmp_path: Path) -> None:
    """Mix of valid + garbage JSON lines — only valid dicts parsed."""
    p = tmp_path / "items.jsonl"
    p.write_text(
        '{"id": "good-1", "value": 1}\n'
        "not json at all\n"
        '\n'  # blank
        '{"id": "good-2", "value": 2}\n'
        '"a json string but not a dict"\n'
        '{ truly broken\n'
        '   \n'  # whitespace
        '[1, 2, 3]\n'  # array, not dict
        '{"id": "good-3", "value": 3}\n',
        encoding="utf-8",
    )
    out = _BaseWindowsManager._read_jsonl_safely(p)
    assert len(out) == 3
    assert [r["id"] for r in out] == ["good-1", "good-2", "good-3"]


# ── 3. _salvage_sidecar (happy path + edge cases) ─────────────────────


def test_salvage_sidecar_from_full_bufdir(tmp_path: Path) -> None:
    """Full bufdir → salvaged Sidecar with items + messages preserved."""
    rid = "12345678-1234-1234-1234-123456789abc"
    bufdir = tmp_path / ".bufdir" / "check__winget"
    bufdir.mkdir(parents=True)
    _write_meta(bufdir, _build_meta_payload(run_id=rid))
    _write_jsonl(
        bufdir, "items.jsonl",
        [
            _build_item(pid="Microsoft.PowerShell"),
            _build_item(pid="Mozilla.Firefox", status="success"),
            _build_item(pid="7zip.7zip", status="up_to_date",
                        target="23.01", resolved="23.01"),
        ],
    )
    _write_jsonl(
        bufdir, "messages.jsonl",
        [{"level": "info", "text": "Found 2 upgrades.", "timestamp": None}],
    )

    expected = tmp_path / rid / "check__winget.json"
    salvaged = _stub()._salvage_sidecar(
        run_id=rid,
        phase=Phase.CHECK,
        category="winget",
        bufdir=bufdir,
        expected_sidecar_path=expected,
        exit_code=0,
    )
    assert salvaged is not None
    assert isinstance(salvaged, Sidecar)
    assert len(salvaged.items) == 3
    assert {it.id for it in salvaged.items} == {
        "Microsoft.PowerShell", "Mozilla.Firefox", "7zip.7zip",
    }
    # Original info message + the synthesised ASCENDO-SALVAGED warn.
    levels = [m.level.value for m in salvaged.messages]
    texts = [m.text for m in salvaged.messages]
    assert levels[0] == "warn"  # ASCENDO-SALVAGED message comes first
    assert "Sidecar reconstructed post-crash" in texts[0]
    assert "Found 2 upgrades." in texts


def test_salvage_sidecar_writes_to_expected_path(tmp_path: Path) -> None:
    """write_sidecar lands the salvaged sidecar at the canonical path."""
    rid = "12345678-1234-1234-1234-123456789abc"
    bufdir = tmp_path / rid / ".bufdir" / "apply__winget"
    bufdir.mkdir(parents=True)
    _write_meta(
        bufdir,
        _build_meta_payload(run_id=rid, phase=Phase.APPLY, category="winget"),
    )
    _write_jsonl(bufdir, "items.jsonl", [_build_item(status="success")])

    expected = tmp_path / rid / "apply__winget.json"
    salvaged = _stub()._salvage_sidecar(
        run_id=rid,
        phase=Phase.APPLY,
        category="winget",
        bufdir=bufdir,
        expected_sidecar_path=expected,
        exit_code=0,
    )
    assert salvaged is not None
    assert expected.exists(), "salvaged sidecar should land at the expected path"
    on_disk = json.loads(expected.read_text(encoding="utf-8"))
    assert on_disk["category"] == "winget"
    assert on_disk["phase"] == "apply"
    assert len(on_disk["items"]) == 1


def test_salvage_sidecar_missing_meta_returns_none(tmp_path: Path) -> None:
    """No meta.json → nothing to salvage."""
    bufdir = tmp_path / ".bufdir" / "check__winget"
    bufdir.mkdir(parents=True)
    # Don't write meta.json.
    result = _stub()._salvage_sidecar(
        run_id="11111111-2222-3333-4444-555555555555",
        phase=Phase.CHECK,
        category="winget",
        bufdir=bufdir,
        expected_sidecar_path=tmp_path / "out.json",
        exit_code=1,
    )
    assert result is None


@pytest.mark.parametrize(
    ("exit_code", "item_statuses", "expected"),
    [
        # exit 0 + all success/up_to_date → SUCCESS
        (0, ["success", "up_to_date"], PhaseStatus.SUCCESS),
        # exit 0 + any failed → PARTIAL (something landed)
        (0, ["success", "failed"], PhaseStatus.PARTIAL),
        # exit 0 + everything failed → FAILED
        (0, ["failed", "failed"], PhaseStatus.FAILED),
        # exit non-zero + any success → PARTIAL
        (1, ["success", "failed"], PhaseStatus.PARTIAL),
        # exit non-zero + no successes → FAILED
        (1, ["failed", "skipped"], PhaseStatus.FAILED),
        # No items → FAILED (nothing survived)
        (1, [], PhaseStatus.FAILED),
        # Empty + exit 0 → still FAILED (nothing to claim success on)
        (0, [], PhaseStatus.FAILED),
        # Unknown exit + mixed → PARTIAL (conservative)
        (None, ["success", "failed"], PhaseStatus.PARTIAL),
    ],
)
def test_salvage_sidecar_status_logic(
    tmp_path: Path,
    exit_code: int | None,
    item_statuses: list[str],
    expected: PhaseStatus,
) -> None:
    """Status synthesis from (exit_code, items) is correct."""
    rid = "12345678-1234-1234-1234-123456789abc"
    bufdir = tmp_path / ".bufdir" / "check__winget"
    bufdir.mkdir(parents=True)
    _write_meta(bufdir, _build_meta_payload(run_id=rid))
    items = [
        _build_item(pid=f"Pkg{i}", status=s)
        for i, s in enumerate(item_statuses)
    ]
    _write_jsonl(bufdir, "items.jsonl", items)

    out = tmp_path / rid / "check__winget.json"
    salvaged = _stub()._salvage_sidecar(
        run_id=rid,
        phase=Phase.CHECK,
        category="winget",
        bufdir=bufdir,
        expected_sidecar_path=out,
        exit_code=exit_code,
    )
    # An empty items list passes Pydantic if summary matches, so the
    # salvage helper should still produce a Sidecar (just with FAILED
    # status). For non-empty cases we expect a Sidecar; for empty +
    # FAILED we still get a Sidecar with zero items.
    assert salvaged is not None
    assert salvaged.status is expected


def test_salvage_sidecar_carries_ascendo_salvaged_diagnostic(
    tmp_path: Path,
) -> None:
    """messages[0] is the ASCENDO-SALVAGED breadcrumb."""
    rid = "12345678-1234-1234-1234-123456789abc"
    bufdir = tmp_path / ".bufdir" / "check__winget"
    bufdir.mkdir(parents=True)
    _write_meta(bufdir, _build_meta_payload(run_id=rid))
    _write_jsonl(bufdir, "items.jsonl", [_build_item()])

    out = tmp_path / rid / "check__winget.json"
    salvaged = _stub()._salvage_sidecar(
        run_id=rid,
        phase=Phase.CHECK,
        category="winget",
        bufdir=bufdir,
        expected_sidecar_path=out,
        exit_code=137,  # SIGKILL
    )
    assert salvaged is not None
    assert salvaged.messages, "salvage must always emit at least one message"
    first = salvaged.messages[0]
    assert first.level.value == "warn"
    assert "Sidecar reconstructed post-crash" in first.text
    assert "exit code 137" in first.text


# ── 4. _cleanup_bufdir ────────────────────────────────────────────────


def test_cleanup_bufdir_removes_directory(tmp_path: Path) -> None:
    """Cleanup actually removes the populated bufdir."""
    bufdir = tmp_path / ".bufdir" / "check__winget"
    bufdir.mkdir(parents=True)
    (bufdir / "meta.json").write_text("{}", encoding="utf-8")
    (bufdir / "items.jsonl").write_text("{}\n", encoding="utf-8")

    _stub()._cleanup_bufdir(bufdir)
    assert not bufdir.exists()


def test_cleanup_bufdir_handles_missing_directory(tmp_path: Path) -> None:
    """Cleanup on a never-allocated bufdir is a no-op."""
    bufdir = tmp_path / "does-not-exist"
    # Must not raise.
    _stub()._cleanup_bufdir(bufdir)


# ── 5. Mixin inheritance ─────────────────────────────────────────────


def test_winget_manager_inherits_base(tmp_path: Path) -> None:
    """WingetManager picked up the mixin."""
    assert issubclass(WingetManager, _BaseWindowsManager)


@pytest.mark.parametrize(
    "manager_cls",
    [
        WingetManager,
        MSStoreManager,
        ArpManager,
        NpmManager,
        PipManager,
        WebManager,
        WindowsUpdateManager,
    ],
)
def test_all_seven_managers_inherit_base(manager_cls: type) -> None:
    """All 7 IPackageManager classes carry the salvage mixin."""
    assert issubclass(manager_cls, _BaseWindowsManager), (
        f"{manager_cls.__name__} must inherit from _BaseWindowsManager"
    )
    assert issubclass(manager_cls, IPackageManager), (
        f"{manager_cls.__name__} must still implement IPackageManager"
    )


# ── 6. Integration: WingetManager salvages on missing sidecar ─────────


def test_winget_run_phase_salvages_on_missing_sidecar(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    tmp_path: Path,
) -> None:
    """When the PS script exits non-zero AND doesn't write a sidecar
    but DID leave bufdir contents behind, run_phase salvages instead
    of raising ManagerError."""
    manager = WingetManager(
        scripts_dir=fake_scripts_dir,
        lib_dir=fake_lib_dir,
        pwsh_path="/usr/bin/true",
        timeout_sec=30,
    )

    rid_str = str(run_info.id)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        # Find the bufdir the manager allocated and stuff partial state
        # into it (mimicking the PS-side AscendoJson behaviour) BEFORE
        # the fake "crash" returns.
        idx = argv.index("-BufDir")
        bufdir = Path(argv[idx + 1])
        # PS-side New-Sidecar would have written meta.json here.
        bufdir.mkdir(parents=True, exist_ok=True)
        bufdir.joinpath("meta.json").write_text(
            json.dumps(_build_meta_payload(run_id=rid_str)),
            encoding="utf-8",
        )
        bufdir.joinpath("items.jsonl").write_text(
            json.dumps(_build_item(status="success")) + "\n",
            encoding="utf-8",
        )
        # Script "crashes" with a non-zero exit and never writes the
        # canonical sidecar.
        return subprocess.CompletedProcess(
            args=argv, returncode=137, stdout="", stderr="killed by signal\n",
        )

    with patch.object(
        manager, "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(
            argv, capture_output=True, text=True,
            timeout=kw.get("timeout"), check=False,
        ),
    ):
        sidecar = manager.run_phase(Phase.CHECK, run_info, windows_host)

    # Salvage path produced a Sidecar instead of raising.
    assert isinstance(sidecar, Sidecar)
    # The argv passed -BufDir.
    assert "-BufDir" in captured["argv"]
    # The salvaged sidecar has the partial item + the ASCENDO-SALVAGED
    # message at messages[0].
    assert len(sidecar.items) == 1
    assert sidecar.messages[0].level.value == "warn"
    assert "Sidecar reconstructed post-crash" in sidecar.messages[0].text
    # exit_code 137 + 1 success → PARTIAL.
    assert sidecar.status is PhaseStatus.PARTIAL
