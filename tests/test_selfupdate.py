"""Unit tests for the app self-update engine (ascendo.selfupdate).

Covers the PEP 440-aware version comparator, install detection, and the
fail-soft / update-available logic of check_for_updates with the network
fetch stubbed out (no real HTTP).
"""
from __future__ import annotations

import pytest

from ascendo.selfupdate import version as v
from ascendo.selfupdate import check as check_mod
from ascendo.selfupdate import detect


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.1", "1.0.0b1", True),    # release > its own beta-ish predecessor
        ("1.0.0", "1.0.0b1", True),    # final > beta
        ("1.0.0b1", "1.0.0", False),   # beta < final
        ("1.0.0b1", "1.0.0b1", False), # equal
        ("1.0.0b2", "1.0.0b1", True),  # beta bump
        ("1.10.0", "1.9.9", True),     # numeric (not lexical) compare
        ("v1.2.0", "1.10.0", False),   # leading v stripped; 1.2 < 1.10
        ("2.0.0", "1.0.0rc1", True),
        ("1.0.0rc1", "1.0.0b9", True), # rc > beta
    ],
)
def test_is_newer(a, b, expected):
    assert v.is_newer(a, b) is expected


def test_compare_symmetry():
    assert v.compare("1.2.3", "1.2.4") == -1
    assert v.compare("1.2.4", "1.2.3") == 1
    assert v.compare("1.2.3", "1.2.3") == 0


def test_detect_install_shape():
    info = detect.detect_install()
    d = info.to_dict()
    assert d["os"] in {"macos", "windows", "linux", "unknown"}
    assert d["method"] in {"git", "packaged"}
    assert "install_dir" in d and "is_git" in d


def _patch_manifest(monkeypatch, core_version, shell_version=None, artifacts=None):
    block = {"channel": "test", "core": {"version": core_version, "notes_url": "x"}}
    if shell_version:
        block["shell"] = {"version": shell_version, "artifacts": artifacts or {}}
    monkeypatch.setattr(check_mod._manifest, "fetch_manifest", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(check_mod._manifest, "select_channel", lambda *a, **k: block)


def test_check_reports_update_available(monkeypatch):
    _patch_manifest(monkeypatch, core_version="999.0.0")
    rep = check_mod.check_for_updates()
    assert rep["ok"] is True
    assert rep["core_update_available"] is True
    assert rep["update_available"] is True
    assert rep["can_self_update"] is True


def test_check_reports_up_to_date(monkeypatch):
    cur = check_mod.current_core_version()
    _patch_manifest(monkeypatch, core_version=cur)
    rep = check_mod.check_for_updates()
    assert rep["ok"] is True
    assert rep["core_update_available"] is False
    assert rep["update_available"] is False
    assert rep["can_self_update"] is False


def test_check_fails_soft_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(check_mod._manifest, "fetch_manifest", boom)
    rep = check_mod.check_for_updates()
    assert rep["ok"] is False
    assert rep["error"]
    assert rep["update_available"] is False  # never offers an update when blind


def test_shell_update_ignored_when_no_artifact(monkeypatch):
    cur = check_mod.current_core_version()
    _patch_manifest(monkeypatch, core_version=cur, shell_version="999.0.0", artifacts={})
    fake_info = detect.InstallInfo(
        os="macos",
        arch="arm64",
        install_dir="/tmp/fake",
        is_git=True,
        method="git",
        desktop=True,
        shell_version="1.0.0",
        updater="/tmp/fake/update.sh",
    )
    rep = check_mod.check_for_updates(fake_info)
    assert rep["ok"] is True
    assert rep["core_update_available"] is False
    assert rep["shell_update_available"] is False
    assert rep["update_available"] is False
    assert rep["can_self_update"] is False


def test_shell_update_when_artifact_present(monkeypatch):
    cur = check_mod.current_core_version()
    artifacts = {
        "macos_arm64": {
            "dmg_url": "https://example.com/Ascendo.dmg",
            "sha256": "abc",
        }
    }
    _patch_manifest(monkeypatch, core_version=cur, shell_version="999.0.0", artifacts=artifacts)
    fake_info = detect.InstallInfo(
        os="macos",
        arch="arm64",
        install_dir="/tmp/fake",
        is_git=True,
        method="git",
        desktop=True,
        shell_version="1.0.0",
        updater="/tmp/fake/update.sh",
    )
    rep = check_mod.check_for_updates(fake_info)
    assert rep["ok"] is True
    assert rep["core_update_available"] is False
    assert rep["shell_update_available"] is True
    assert rep["update_available"] is True
    assert rep["can_self_update"] is False  # Cannot self-update shell in-app!
    assert rep["shell_artifact"] is not None
    assert rep["shell_artifact"]["dmg_url"] == "https://example.com/Ascendo.dmg"


def test_both_updates_available(monkeypatch):
    artifacts = {
        "macos_arm64": {
            "dmg_url": "https://example.com/Ascendo.dmg",
            "sha256": "abc",
        }
    }
    _patch_manifest(monkeypatch, core_version="999.0.0", shell_version="999.0.0", artifacts=artifacts)
    fake_info = detect.InstallInfo(
        os="macos",
        arch="arm64",
        install_dir="/tmp/fake",
        is_git=True,
        method="git",
        desktop=True,
        shell_version="1.0.0",
        updater="/tmp/fake/update.sh",
    )
    rep = check_mod.check_for_updates(fake_info)
    assert rep["ok"] is True
    assert rep["core_update_available"] is True
    assert rep["shell_update_available"] is True
    assert rep["update_available"] is True
    assert rep["can_self_update"] is True  # Core can be self-updated
