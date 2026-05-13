"""Regression tests for Tier-A apply fake-success detection (Sesja 64).

Operator request: "no fake runs, that ascendo shows success run, but
apps are not updated still, implement some kind of verification for
this."

Audit found:
- ``Invoke-GitHubReleaseApplyReal`` (lib/handlers/github_release.ps1)
- ``Invoke-ReleaseFeedApplyReal``   (lib/handlers/release_feed.ps1)

Both re-read DisplayVersion from the registry AFTER running the
installer, but BOTH returned ``Success=true`` regardless of whether
the version actually changed. If the installer returns exit code 0
but the registry DisplayVersion stays at the pre-install value
(Squirrel auto-rollback, silent-skip on running process, MSI ICE
warning, partial download), Ascendo emitted ``Success=true`` and the
operator saw a green-check apply that didn't actually upgrade
anything.

Sesja 64 fix:
  1. Capture ``$preInstallVersion`` at the START of the function
     (after the kill step, before download/install).
  2. After install + post-install readback, compare ``$newVersion``
     to ``$preInstallVersion``.
  3. When ``newVersion == preInstallVersion`` AND the version is
     NOT what we just tried to install, return ``Success=false``
     with a clear ``ErrorMessage`` that the apply phase surfaces
     to the operator.

The exemption: when ``newVersion == tag`` (the version we tried to
install), we DON'T treat it as fake-success even if it equals
preInstallVersion -- that's a legitimate re-install of the same
version (e.g. operator forcing a repair).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
GITHUB_RELEASE_PS1 = ADAPTER_ROOT / "lib" / "handlers" / "github_release.ps1"
RELEASE_FEED_PS1 = ADAPTER_ROOT / "lib" / "handlers" / "release_feed.ps1"


# ─── github_release Tier-A apply ──────────────────────────────────────


def test_github_release_captures_pre_install_version() -> None:
    """``Invoke-GitHubReleaseApplyReal`` must capture the registry
    DisplayVersion BEFORE the installer runs, so we have a baseline
    to compare against post-install.
    """
    text = GITHUB_RELEASE_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-GitHubReleaseApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    assert "$preInstallVersion" in fn_block, (
        "Invoke-GitHubReleaseApplyReal must capture $preInstallVersion "
        "before invoking the installer. Without a baseline we can't "
        "detect fake-success (installer returns exit 0 but registry "
        "DisplayVersion didn't change)."
    )
    # The capture must call Get-WebReinstalledVersion (or equivalent).
    assert "Get-WebReinstalledVersion" in fn_block


def test_github_release_compares_pre_and_post_versions() -> None:
    """The function must compare the post-install $newVersion to the
    captured $preInstallVersion and return Success=false on no-change
    fake-success.
    """
    text = GITHUB_RELEASE_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-GitHubReleaseApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    # The fake-success detection block must compare newVersion ==
    # preInstallVersion AND return Success=$false in that case.
    assert "$newVersion -eq $preInstallVersion" in fn_block, (
        "Must compare $newVersion to $preInstallVersion for fake-"
        "success detection"
    )
    # Look for the Success=$false return after the comparison.
    fake_success_block = fn_block.split("$newVersion -eq $preInstallVersion", 1)[1].split("return", 1)[1]
    assert "Success" in fake_success_block, (
        "Fake-success comparison must lead to a return statement"
    )
    # The error message must mention DisplayVersion or 'fake-success'.
    assert (
        "DisplayVersion" in fn_block
        and "fake-success" in fn_block.lower()
    ), (
        "Fake-success ErrorMessage must mention the DisplayVersion "
        "mismatch so the operator can debug"
    )


def test_github_release_allows_legitimate_reinstall_of_same_version() -> None:
    """When ``newVersion == tag`` (the operator forced a re-install of
    the same version we tried to install), do NOT mark as fake-
    success even though newVersion == preInstallVersion.
    """
    text = GITHUB_RELEASE_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-GitHubReleaseApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    # The detection block must include an exemption for newVersion == tag.
    assert "$newVersion -ne $tag" in fn_block, (
        "Fake-success detection must NOT fire on a legitimate re-"
        "install of the same version (newVersion == tag). Check the "
        "comparison includes ``$newVersion -ne $tag``."
    )


# ─── release_feed Tier-A apply ────────────────────────────────────────


def test_release_feed_captures_pre_install_version() -> None:
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-ReleaseFeedApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    assert "$preInstallVersion" in fn_block, (
        "Invoke-ReleaseFeedApplyReal must capture $preInstallVersion "
        "before invoking the installer (mirror of github_release)"
    )
    assert "Get-WebReinstalledVersion" in fn_block


def test_release_feed_compares_pre_and_post_versions() -> None:
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-ReleaseFeedApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    assert "$newVersion -eq $preInstallVersion" in fn_block, (
        "Must compare $newVersion to $preInstallVersion for fake-"
        "success detection"
    )
    assert (
        "DisplayVersion" in fn_block
        and "fake-success" in fn_block.lower()
    )


def test_release_feed_allows_legitimate_reinstall_of_same_version() -> None:
    text = RELEASE_FEED_PS1.read_text(encoding="utf-8")
    fn_block = (
        text.split("function Invoke-ReleaseFeedApplyReal", 1)[1]
            .split("# Functions are auto-exported", 1)[0]
    )
    # The release_feed branch uses $candidate (not $tag) but the
    # exemption rule is the same.
    assert "$newVersion -ne $candidate" in fn_block, (
        "Fake-success detection must NOT fire on a legitimate re-"
        "install of the same version (newVersion == candidate)"
    )


# ─── Tier-A registry promotions (Sesja 64) ────────────────────────────


@pytest.mark.parametrize(
    "slug,publisher,silent_first_arg",
    [
        ("obsidian",   "Obsidian",                  "/S"),
        ("obs-studio", "Open Broadcaster Software", "/S"),
    ],
)
def test_sesja64_tier_a_promotion(
    slug: str, publisher: str, silent_first_arg: str,
) -> None:
    """Sesja 64 promoted obsidian + obs-studio to Tier-A silent install.
    Pin the configuration so a future refactor doesn't revert.
    """
    import tomllib
    cfg_path = ADAPTER_ROOT / "config" / "web_apps.toml"
    with open(cfg_path, "rb") as f:
        reg = tomllib.load(f)
    apps = reg.get("app", [])
    entry = next((a for a in apps if a.get("slug") == slug), None)
    assert entry is not None, f"slug={slug} missing"
    assert entry.get("tier_a_apply") is True, (
        f"{slug} must be tier_a_apply=true (Sesja 64 promotion)"
    )
    gh = entry.get("github_release", {})
    assert gh.get("expected_publisher") == publisher
    assert gh.get("silent_args", [None])[0] == silent_first_arg
    assert gh.get("installer_kind") == "exe"
    assert gh.get("kill_processes"), (
        f"{slug} must list kill_processes so the installer can replace "
        "the running binary"
    )


# ─── MSI / NSIS retirement (Sesja 64) ─────────────────────────────────


def test_tauri_conf_disables_msi_and_nsis_bundlers() -> None:
    """The Tauri shell config must NOT list ``msi`` or ``nsis`` in its
    bundle targets. Public distribution is the ``iwr install.ps1
    | iex`` one-liner; MSI/NSIS artifacts are retired pending the
    signing infrastructure roadmap (PLAN.md v0.7+).
    """
    import json
    p = (Path(__file__).resolve().parents[3]
         / "ui" / "desktop-tauri" / "src-tauri" / "tauri.conf.json")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    targets = cfg.get("bundle", {}).get("targets")
    if isinstance(targets, list):
        assert "msi" not in targets, (
            "MSI bundler retired in Sesja 64 (public dist is "
            "install.ps1 one-liner). Re-enable only after signing "
            "infrastructure lands."
        )
        assert "nsis" not in targets, (
            "NSIS bundler retired in Sesja 64. Same rationale."
        )
    else:
        # If targets is "all" or some other shape, the retirement
        # didn't land.
        raise AssertionError(
            f"tauri.conf.json targets must be an explicit allow-list "
            f"list (not '{targets!r}') so msi/nsis exclusion is auditable"
        )


def test_tauri_conf_no_wix_or_nsis_blocks() -> None:
    """The Tauri shell config's ``bundle.windows`` block must no
    longer carry ``wix`` or ``nsis`` sub-tables; those references
    pointed at retired installer-assets.
    """
    import json
    p = (Path(__file__).resolve().parents[3]
         / "ui" / "desktop-tauri" / "src-tauri" / "tauri.conf.json")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    win = cfg.get("bundle", {}).get("windows", {})
    assert "wix" not in win, (
        "bundle.windows.wix removed in Sesja 64"
    )
    assert "nsis" not in win, (
        "bundle.windows.nsis removed in Sesja 64"
    )
