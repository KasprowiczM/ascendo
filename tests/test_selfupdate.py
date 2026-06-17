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


def _patch_manifest(monkeypatch, core_version, shell_version=None):
    block = {"channel": "test", "core": {"version": core_version, "notes_url": "x"}}
    if shell_version:
        block["shell"] = {"version": shell_version, "artifacts": {}}
    monkeypatch.setattr(check_mod._manifest, "fetch_manifest", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(check_mod._manifest, "select_channel", lambda *a, **k: block)


def test_check_reports_update_available(monkeypatch):
    _patch_manifest(monkeypatch, core_version="999.0.0")
    rep = check_mod.check_for_updates()
    assert rep["ok"] is True
    assert rep["core_update_available"] is True
    assert rep["update_available"] is True


def test_check_reports_up_to_date(monkeypatch):
    cur = check_mod.current_core_version()
    _patch_manifest(monkeypatch, core_version=cur)
    rep = check_mod.check_for_updates()
    assert rep["ok"] is True
    assert rep["core_update_available"] is False
    assert rep["update_available"] is False


def test_check_fails_soft_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(check_mod._manifest, "fetch_manifest", boom)
    rep = check_mod.check_for_updates()
    assert rep["ok"] is False
    assert rep["error"]
    assert rep["update_available"] is False  # never offers an update when blind
