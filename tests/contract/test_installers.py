"""Contract tests for the cross-OS installer + updater scripts.

Scope: argv parsing, help text, env-var honour, structural integrity.
We do NOT actually run the installs — that's too disruptive in CI.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
UPDATE_SH = REPO_ROOT / "update.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
UPDATE_PS1 = REPO_ROOT / "update.ps1"


# ── install.sh ────────────────────────────────────────────────────────────


def test_install_sh_exists_and_executable() -> None:
    assert INSTALL_SH.is_file(), "install.sh missing"
    # On POSIX, executable bit should be set after `chmod +x`.
    if os.name == "posix":
        assert os.access(INSTALL_SH, os.X_OK), "install.sh is not executable"


def test_install_sh_has_valid_bash_syntax() -> None:
    """`bash -n` parses the script without executing it."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available on this host")
    proc = subprocess.run(
        [bash, "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


def test_install_sh_help_works() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = subprocess.run(
        [bash, str(INSTALL_SH), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    out = proc.stdout
    # Help text must mention the canonical user-facing concepts.
    assert "Ascendo" in out
    assert "--update" in out
    assert "--reinstall" in out
    assert "--verbose" in out
    assert "--non-interactive" in out
    assert "ASCENDO_LANG" in out
    assert "ASCENDO_PROFILE" in out


def test_install_sh_rejects_unknown_flag() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = subprocess.run(
        [bash, str(INSTALL_SH), "--no-such-flag"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "Unknown flag" in proc.stderr


def test_install_sh_short_flags_alias() -> None:
    """Short flags must alias correctly."""
    text = INSTALL_SH.read_text()
    # -v  ↔ --verbose
    assert re.search(r"--verbose\|-v", text)
    # -u  ↔ --update
    assert re.search(r"--update\|-u", text)
    # -h  ↔ --help
    assert re.search(r"--help\|-h", text)


def test_install_sh_supports_env_vars() -> None:
    """The script must read ASCENDO_LANG / ASCENDO_PROFILE / ASCENDO_HOME / etc."""
    text = INSTALL_SH.read_text()
    for var in (
        "ASCENDO_LANG",
        "ASCENDO_PROFILE",
        "ASCENDO_HOME",
        "ASCENDO_NONINTERACTIVE",
        "ASCENDO_VERBOSE",
        "ASCENDO_REPO_URL",
        "ASCENDO_BRANCH",
    ):
        assert f"${{{var}" in text or f"${var}" in text, f"missing env-var support: {var}"


def test_install_sh_self_test_invokes_doctor() -> None:
    """The installer must call `ascendo doctor` at the end."""
    text = INSTALL_SH.read_text()
    assert "ascendo doctor" in text, "self-test missing"


def test_install_sh_path_check_handles_zsh_fish_bash() -> None:
    """PATH instructions must be shell-aware."""
    text = INSTALL_SH.read_text()
    assert "zsh" in text
    assert "fish" in text
    assert "bashrc" in text


def test_install_sh_reinstall_purges_install_dir() -> None:
    """--reinstall must rm -rf the install dir."""
    text = INSTALL_SH.read_text()
    assert re.search(r"FORCE_REINSTALL=1.*rm -rf", text, re.DOTALL)


# ── update.sh ─────────────────────────────────────────────────────────────


def test_update_sh_exists() -> None:
    assert UPDATE_SH.is_file()
    if os.name == "posix":
        assert os.access(UPDATE_SH, os.X_OK)


def test_update_sh_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = subprocess.run(
        [bash, "-n", str(UPDATE_SH)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


def test_update_sh_help_works() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = subprocess.run(
        [bash, str(UPDATE_SH), "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0
    assert "Ascendo" in proc.stdout
    assert "--verbose" in proc.stdout


def test_update_sh_rejects_unknown_flag() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = subprocess.run(
        [bash, str(UPDATE_SH), "--bogus"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 2
    assert "Unknown flag" in proc.stderr


def test_update_sh_refuses_to_merge_on_local_changes() -> None:
    """The updater must refuse to merge — fast-forward only."""
    text = UPDATE_SH.read_text()
    assert "--ff-only" in text
    # And bail loudly if dirty
    assert "Local changes" in text


def test_update_sh_pulls_then_pip_upgrades() -> None:
    text = UPDATE_SH.read_text()
    assert "git -C" in text
    assert "pull" in text
    assert "pip install --upgrade" in text


def test_update_sh_restarts_dashboard_when_running() -> None:
    text = UPDATE_SH.read_text()
    assert "pgrep -f" in text
    assert "ascendo dashboard" in text


def test_update_sh_self_tests() -> None:
    text = UPDATE_SH.read_text()
    assert "ascendo doctor" in text


# ── install.ps1 ───────────────────────────────────────────────────────────


def test_install_ps1_exists() -> None:
    assert INSTALL_PS1.is_file()


def test_install_ps1_has_balanced_braces_and_parens() -> None:
    """A first-cut structural sanity check that doesn't need pwsh."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    # Strip strings + comments to avoid counting braces inside them.
    # Simple heuristic: count after removing block comments and double-quoted strings.
    stripped = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    stripped = re.sub(r"#[^\r\n]*", "", stripped)
    stripped = re.sub(r'"[^"\r\n]*"', '""', stripped)
    stripped = re.sub(r"'[^'\r\n]*'", "''", stripped)
    assert stripped.count("{") == stripped.count("}"), "unbalanced braces"
    assert stripped.count("(") == stripped.count(")"), "unbalanced parens"


def test_install_ps1_has_valid_pwsh_syntax_if_pwsh_available() -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("pwsh / powershell not available")
    # Use the .NET parser via PowerShell to detect syntax errors WITHOUT executing.
    proc = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-Command",
            # $tokens / $errors MUST be declared before being passed by [ref]:
            # `[ref]$errors` on an undefined variable raises "[ref] cannot be
            # applied to a variable that does not exist", which made this check
            # fail on its own harness (not on install.ps1) wherever pwsh exists.
            f"$tokens = $null; $errors = $null; "
            f"$ast = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{INSTALL_PS1}', [ref]$tokens, [ref]$errors); "
            f"if ($errors.Count -gt 0) {{ $errors | Format-List; exit 1 }}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"pwsh parser found errors: {proc.stdout}"


def test_install_ps1_supports_required_parameters() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for param in ("Reinstall", "Update", "Profile", "Language", "NonInteractive"):
        assert param in text, f"missing parameter: {param}"


def test_install_ps1_supports_env_vars() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for var in (
        "ASCENDO_HOME",
        "ASCENDO_PROFILE",
        "ASCENDO_LANG",
        "ASCENDO_NONINTERACTIVE",
        "ASCENDO_VERBOSE",
        "ASCENDO_REPO_URL",
        "ASCENDO_BRANCH",
    ):
        assert var in text, f"missing env var: {var}"


def test_install_ps1_offers_winget_for_python_and_git() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "winget install" in text
    assert "Python.Python" in text
    assert "Git.Git" in text


def test_install_ps1_self_tests_via_doctor() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "ascendo doctor" in text


def test_install_ps1_drops_shim_to_windowsapps() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "WindowsApps" in text
    assert "ascendo.cmd" in text


def test_install_ps1_refuses_old_windows() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    # Build 17763 = Windows 10 1809
    assert "17763" in text


# ── update.ps1 ────────────────────────────────────────────────────────────


def test_update_ps1_exists() -> None:
    assert UPDATE_PS1.is_file()


def test_update_ps1_has_balanced_braces_and_parens() -> None:
    text = UPDATE_PS1.read_text(encoding="utf-8")
    stripped = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    stripped = re.sub(r"#[^\r\n]*", "", stripped)
    stripped = re.sub(r'"[^"\r\n]*"', '""', stripped)
    stripped = re.sub(r"'[^'\r\n]*'", "''", stripped)
    assert stripped.count("{") == stripped.count("}")
    assert stripped.count("(") == stripped.count(")")


def test_update_ps1_pulls_ff_only() -> None:
    text = UPDATE_PS1.read_text(encoding="utf-8")
    assert "--ff-only" in text


def test_update_ps1_restarts_service_if_installed() -> None:
    text = UPDATE_PS1.read_text(encoding="utf-8")
    assert "AscendoDashboard" in text
    assert "Restart-Service" in text


def test_update_ps1_self_tests() -> None:
    text = UPDATE_PS1.read_text(encoding="utf-8")
    assert "ascendo doctor" in text


# ── Cross-cutting ─────────────────────────────────────────────────────────


def test_all_four_scripts_mention_repo_url_consistently() -> None:
    """All four entrypoints must point at the same canonical repo by default."""
    expected = "KasprowiczM/ascendo"
    for f in (INSTALL_SH, UPDATE_SH, INSTALL_PS1, UPDATE_PS1):
        assert expected in f.read_text(encoding="utf-8"), f"{f.name} missing repo URL"


def test_readme_mentions_all_four_one_liners() -> None:
    """README must document each one-liner."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "install.sh" in readme
    assert "update.sh" in readme
    assert "install.ps1" in readme
    assert "update.ps1" in readme
