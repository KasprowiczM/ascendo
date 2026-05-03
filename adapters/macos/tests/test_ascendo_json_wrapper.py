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

    # schema_ is the Pydantic field name (alias "schema" in JSON)
    assert sc.schema_.value == "ascendo/v1"
    assert sc.phase.value == "check"
    assert sc.category.value == "brew"
    assert len(sc.items) == 1
    assert sc.items[0].id == "node"
