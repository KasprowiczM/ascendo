"""``clt`` health component (Task 17, brew.md row 11): ok/missing/stale/orphan.

Read-only probes only; pkgutil / sw_vers are fakes on PATH, the CLT dir is a temp dir.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from ascendo_macos.adapter import MacOSAdapter
from .su_fakes import _write_exec


def _setup(tmp_path, monkeypatch, *, pkg_version: str | None, os_version: str,
           clt_dir_exists: bool = True, xcode_select: str = "/nonexistent"):
    clt = tmp_path / "CommandLineTools"
    if clt_dir_exists:
        clt.mkdir()
    b = tmp_path / "bin"
    b.mkdir()
    out = "" if pkg_version is None else f"package-id: com.apple.pkg.CLTools_Executables\\nversion: {pkg_version}\\n"
    _write_exec(b / "pkgutil", f"#!/bin/sh\nprintf '{out}'\n[ -n '{pkg_version or ''}' ] || exit 1\n")
    _write_exec(b / "xcode-select", f"#!/bin/sh\necho '{xcode_select}'\n")
    _write_exec(b / "sw_vers", f"#!/bin/sh\necho {os_version}\n")
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ASCENDO_CLT_DIR", str(clt))


def test_clt_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version="27.0.0.0.1", os_version="27.0.1")
    assert MacOSAdapter()._clt_status().startswith("ok: Command Line Tools 27.0.0")


def test_clt_missing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version="27.0", os_version="27.0", clt_dir_exists=False)
    s = MacOSAdapter()._clt_status()
    assert s.startswith("unavailable:") and "xcode-select --install" in s


def test_clt_stale(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version="26.1.0.0.1", os_version="27.0")
    s = MacOSAdapter()._clt_status()
    assert s.startswith("degraded:") and "stale" in s and "softwareupdate --install -a" in s


def test_clt_orphan_without_receipt(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version=None, os_version="27.0")
    s = MacOSAdapter()._clt_status()
    assert s.startswith("degraded:") and "orphan" in s


def test_clt_is_a_health_component(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version="27.0", os_version="27.0")
    assert "clt" in MacOSAdapter().health_check()


def test_clt_absent_but_xcode_selected_is_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pkg_version=None, os_version="27.0", clt_dir_exists=False,
           xcode_select="/Applications/Xcode.app/Contents/Developer")
    assert MacOSAdapter()._clt_status().startswith("ok: Xcode selected")
