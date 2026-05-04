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
