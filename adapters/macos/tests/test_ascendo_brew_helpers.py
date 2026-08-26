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


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_cask_app_name_maps_brew_migrated_casks() -> None:
    """Casks migrated from web handlers in macOS_updates 2026-08-05."""
    script = f'''
        . "{LIB}"
        ascendo_brew_cask_app_name "brave-browser"
        ascendo_brew_cask_app_name "capcut"
        ascendo_brew_cask_app_name "lm-studio"
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0
    lines = res.stdout.splitlines()
    assert lines[0] == "Brave Browser"
    assert lines[1] == "CapCut"
    assert lines[2] == "LM Studio"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_outdated_json_does_not_merge_stderr() -> None:
    """Regression lock: brew outdated stderr must stay off stdout.

    macOS_updates 2026-08-19: capturing brew outdated with 2>&1 treated
    '==> Downloading Homebrew API data' as outstanding packages.
    """
    text = LIB.read_text(encoding="utf-8")
    start = text.index("ascendo_brew_outdated_json() {")
    end = text.index("ascendo_brew_parse_outdated() {")
    fn = text[start:end]
    outdated_lines = [
        ln for ln in fn.splitlines()
        if "outdated" in ln and "json" in ln
    ]
    assert outdated_lines, "expected a brew outdated --json=v2 invocation"
    assert all("2>&1" not in ln for ln in outdated_lines)
    assert '2>"$err_file"' in fn


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_cask_versions_falls_back_to_caskroom(tmp_path: Path) -> None:
    """When `brew list --cask --versions` is empty (Cask::CaskLoader
    regression), fall back to Caskroom/<token>/<version>."""
    import os

    fake_brew = tmp_path / "brew"
    prefix = tmp_path / "opt" / "homebrew"
    room = prefix / "Caskroom" / "capcut" / "9.3.0.4490"
    room.mkdir(parents=True)
    fake_brew.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--prefix" ]; then echo "{prefix}"; exit 0; fi\n'
        'if [ "$1" = "list" ] && [ "$2" = "--cask" ] && [ "$3" = "--versions" ]; then exit 0; fi\n'
        'if [ "$1" = "list" ] && [ "$2" = "--cask" ]; then echo capcut; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake_brew.chmod(0o755)
    script = f'. "{LIB}" && ascendo_brew_cask_versions'
    res = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"},
    )
    assert res.returncode == 0, res.stderr
    assert "capcut" in res.stdout
    assert "9.3.0.4490" in res.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_brew_check_script_uses_cask_versions_helper() -> None:
    """check.sh must not call `brew list --cask --versions` as a command."""
    script = ADAPTER_ROOT / "scripts" / "brew" / "check.sh"
    code = "\n".join(
        ln for ln in script.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "list --cask --versions" not in code
    assert "ascendo_brew_cask_versions" in code


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_version_gt_detects_brave_style_cask_lag() -> None:
    """Installed Brave 151.x is newer than Homebrew cask 1.93.x (not a real upgrade)."""
    script = f'''
        . "{LIB}"
        ascendo_brew_version_gt "151.1.93.138" "1.93.138.0"; echo gt:$?
        ascendo_brew_version_gt "1.93.138.0" "151.1.93.138"; echo lt:$?
        ascendo_brew_version_gt "1.0.0" "1.0.0"; echo eq:$?
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert "gt:0" in res.stdout
    assert "lt:1" in res.stdout
    assert "eq:1" in res.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_cask_would_downgrade_brave_from_fake_plist(tmp_path: Path) -> None:
    """Brave.app CFBundleShortVersionString 151.x vs cask target 1.93.x → skip."""
    app = tmp_path / "Applications" / "Brave Browser.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        "<key>CFBundleShortVersionString</key><string>151.1.93.138</string>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    script = f'''
        . "{LIB}"
        export ASCENDO_BREW_APPLICATIONS_DIR="{tmp_path / "Applications"}"
        ascendo_brew_cask_would_downgrade brave-browser 1.93.138.0; echo down:$?
        ascendo_brew_cask_would_downgrade brave-browser 151.2.0; echo up:$?
    '''
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert "down:0" in res.stdout
    assert "up:1" in res.stdout


def test_brew_phase_scripts_call_downgrade_guard() -> None:
    for name in ("check.sh", "plan.sh", "apply.sh"):
        text = (ADAPTER_ROOT / "scripts" / "brew" / name).read_text(encoding="utf-8")
        assert "ascendo_brew_cask_would_downgrade" in text, name
