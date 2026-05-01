"""Contract tests for :mod:`ascendo.orchestrator.sidecar_io`.

The I/O layer guarantees:
  - atomic writes (no torn output)
  - canonical filename layout under ``<base>/<run-id>/``
  - listing skips lock files / partial files / hidden files
  - best-effort recovery of truncated or garbage files
  - serialized concurrent writes
  - UTF-8 round-trip survives non-ASCII payloads
"""

from __future__ import annotations

import copy
import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from ascendo.models.sidecar import parse_sidecar
from ascendo.orchestrator.sidecar_io import (
    RecoveryStub,
    list_run_sidecars,
    read_run,
    read_sidecar,
    recover_partial,
    write_sidecar,
)


def test_write_then_read_round_trip(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    """write_sidecar then read_sidecar reproduces an equal model."""
    sidecar = parse_sidecar(sample_ascendo_v1_apply_winget)
    written_path = write_sidecar(sidecar, base_dir=tmp_path)
    assert written_path.exists()

    loaded = read_sidecar(written_path)
    assert loaded == sidecar


def test_filename_layout(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    """Path is ``<base>/<run-id>/<phase>__<category>.json``."""
    sidecar = parse_sidecar(sample_ascendo_v1_apply_winget)
    path = write_sidecar(sidecar, base_dir=tmp_path)

    assert path.parent.name == str(sidecar.run.id)
    assert path.name == f"{sidecar.phase.value}__{sidecar.category.value}.json"
    assert path.parent.parent == tmp_path


def test_list_run_sidecars_skips_partial_and_lock_files(
    sample_ascendo_v1_apply_winget: dict[str, Any],
    sample_ascendo_v1_check_apt: dict[str, Any],
    tmp_path: Path,
) -> None:
    """list_run_sidecars surfaces only real sidecar JSON files."""
    a = parse_sidecar(sample_ascendo_v1_apply_winget)
    b = parse_sidecar(sample_ascendo_v1_check_apt)

    # Both go under the SAME run id by design (typical run has many phases).
    payload_b = copy.deepcopy(sample_ascendo_v1_check_apt)
    payload_b["run"]["id"] = str(a.run.id)
    b = parse_sidecar(payload_b)

    write_sidecar(a, base_dir=tmp_path)
    write_sidecar(b, base_dir=tmp_path)
    run_dir = tmp_path / str(a.run.id)

    # Drop noise files that must be skipped.
    (run_dir / ".hidden.json").write_text("{}", encoding="utf-8")
    (run_dir / "apply__winget.json.lock").write_text("", encoding="utf-8")
    (run_dir / "stale.partial").write_text("", encoding="utf-8")
    (run_dir / "not-a-sidecar.txt").write_text("", encoding="utf-8")

    listed = list_run_sidecars(run_dir)
    names = sorted(p.name for p in listed)
    assert names == ["apply__winget.json", "check__apt.json"]


def test_recover_partial_truncated_returns_stub(
    sample_ascendo_v1_apply_winget: dict[str, Any],
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """recover_partial salvages a partial-but-recoverable JSON if we trim trailing junk.

    Build a guaranteed-recoverable fixture: a valid ascendo/v1 payload
    with extra trailing garbage bytes appended after the closing brace.
    The truncate-strategy in ``recover_partial`` should walk back to the
    last valid '}' and either re-validate or synthesize a stub.
    """
    raw_path = tmp_path / "trailing_garbage.json"
    valid_payload = json.dumps(sample_ascendo_v1_apply_winget)
    raw_path.write_text(valid_payload + "\n@@@@trailing garbage@@@@", encoding="utf-8")

    recovered = recover_partial(raw_path)
    assert recovered is not None
    # Either full sidecar or a synthesized stub. Both cases must validate.
    assert recovered.schema_.value == "ascendo/v1"


def test_recover_partial_pure_garbage_returns_none(fixtures_dir: Path) -> None:
    """A file containing zero JSON-parseable content yields None."""
    garbage_path = fixtures_dir / "garbage.json"
    assert garbage_path.exists(), "garbage fixture missing"
    assert recover_partial(garbage_path) is None


def test_concurrent_writes_serialize(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    """Four threads writing the same sidecar produce one valid file (no torn output)."""
    sidecar = parse_sidecar(sample_ascendo_v1_apply_winget)
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def writer() -> None:
        try:
            barrier.wait(timeout=10)
            write_sidecar(sidecar, base_dir=tmp_path)
        except BaseException as exc:  # pragma: no cover — surface on failure
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"writer threads raised: {errors}"

    # The destination must be readable as a valid sidecar (no half-written
    # tempfile contents bled into the canonical path).
    final = read_sidecar(
        tmp_path / str(sidecar.run.id) / f"{sidecar.phase.value}__{sidecar.category.value}.json"
    )
    assert final == sidecar


def test_unicode_payload_round_trips(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    """Polish + emoji content survives write+read."""
    payload = copy.deepcopy(sample_ascendo_v1_apply_winget)
    weird = "Zażółć gęślą jaźń (test) — ellipsis: …"
    payload["messages"] = [
        {"level": "info", "text": weird},
        {"level": "warn", "text": "snowman"},
    ]
    sidecar = parse_sidecar(payload)
    written = write_sidecar(sidecar, base_dir=tmp_path)

    # Bytes on disk must be valid UTF-8 and contain the original codepoints.
    raw = written.read_bytes().decode("utf-8")
    assert weird in raw

    loaded = read_sidecar(written)
    assert loaded.messages[0].text == weird


def test_read_run_returns_recovery_stub_for_bad_sidecar(
    sample_ascendo_v1_apply_winget: dict[str, Any], tmp_path: Path
) -> None:
    """read_run includes RecoveryStub entries for unreadable sidecars in the dir."""
    sidecar = parse_sidecar(sample_ascendo_v1_apply_winget)
    write_sidecar(sidecar, base_dir=tmp_path)
    run_dir = tmp_path / str(sidecar.run.id)

    # Drop an unrecoverable sibling sidecar with a phase__category-shaped name.
    (run_dir / "verify__apt.json").write_text(
        "definitely not json", encoding="utf-8"
    )

    results = read_run(run_dir)
    # Two entries: the good one + a stub for the bad one.
    assert len(results) == 2
    stubs = [r for r in results if isinstance(r, RecoveryStub)]
    assert len(stubs) == 1
    assert stubs[0].path.name == "verify__apt.json"
