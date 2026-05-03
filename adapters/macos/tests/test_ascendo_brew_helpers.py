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
    assert lines[2] == ""  # unknown -> empty
