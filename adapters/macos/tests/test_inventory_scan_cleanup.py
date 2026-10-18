"""Task 15 -- inventory scan cleanup, iPad identity, exclusions.

Fixture app trees live in tmp dirs; system_profiler is a fake that returns a
JSON document built from that tree. Nothing reads the real /Applications.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .mas_fakes import make_ipad_app

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "inventory" / "list.sh"
sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _sp_entry(path: Path | str, name: str, obtained: str = "identified_developer",
              version: str = "1.0", arch: str = "arch_arm") -> dict:
    return {"_name": name, "path": str(path), "version": version,
            "obtained_from": obtained, "arch_kind": arch}


def _fake_sp(tmp: Path, entries: list[dict]) -> Path:
    doc = json.dumps({"SPApplicationsDataType": entries})
    p = tmp / "fake_sp"
    p.write_text("#!/usr/bin/env bash\n"
                 "if [ \"$1\" = '--version' ]; then echo 'sp fake'; exit 0; fi\n"
                 f"cat <<'EOF_SP'\n{doc}\nEOF_SP\n")
    p.chmod(0o755)
    return p


def _run(tmp: Path, entries: list[dict], *, env_extra: dict | None = None,
         sp: Path | None = None):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    env.update({"HOME": str(home), "SP_BIN": str(sp or _fake_sp(tmp, entries)),
                "MAS_BIN": "", "BREW_BIN": "",
                "ASCENDO_ROSETTA_PRESENT": "1", "ASCENDO_HOST_ARCH": "arm64"})
    env.update(env_extra or {})
    out = tmp / "out"
    rid = str(uuid.uuid4())
    res = subprocess.run(
        ["bash", str(SCRIPT), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out)],
        capture_output=True, text=True, env=env, check=False)
    from ascendo.models.sidecar import parse_sidecar
    sc = parse_sidecar((out / rid / "check__inventory.json").read_text())
    return res, sc


def _kinds(sc) -> dict[str, str]:
    return {i.id: i.source.type.value for i in sc.items}


def _tree(tmp: Path) -> dict[str, Path]:
    apps = tmp / "Applications"
    home_apps = tmp / "home" / "Applications"
    paths = {
        "apps": apps,
        "home_apps": home_apps,
        "foo": apps / "Foo.app",
        "bar": home_apps / "Bar.app",
        "xcode_helper": apps / "Xcode.app" / "Contents" / "Applications" / "Simulator.app",
        "lib_app": tmp / "home" / "Library" / "Application Support" / "Y.app",
        "updater": tmp / "Library" / "Application Support" / "Google" / "GoogleUpdater.app",
        "in_progress": apps / "in_progress" / "Docker.app",
        "ascendo": apps / "Ascendo.app",
        "fixture": tmp / "repo" / "tests" / "fixtures" / "Applications" / "FakeThing.app",
    }
    for key, p in paths.items():
        if key in ("apps", "home_apps"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)
    return paths


def _entries(p: dict[str, Path]) -> list[dict]:
    return [
        _sp_entry(p["foo"], "Foo"),
        _sp_entry(p["bar"], "Bar"),
        _sp_entry(p["xcode_helper"], "Simulator"),
        _sp_entry(p["lib_app"], "Y"),
        _sp_entry(p["updater"], "GoogleUpdater"),
        _sp_entry(p["in_progress"], "Docker"),
        _sp_entry(p["ascendo"], "Ascendo"),
        _sp_entry(p["fixture"], "FakeThing"),
        _sp_entry("/System/Library/CoreServices/AddressBookManager.app", "AddressBookManager",
                  obtained="apple"),
    ]


def test_only_top_level_real_apps_of_the_app_dirs_are_listed(tmp_path):
    p = _tree(tmp_path)
    res, sc = _run(tmp_path, _entries(p),
                   env_extra={"ASCENDO_APP_DIRS": f"{p['apps']}:{p['home_apps']}"})
    assert res.returncode == 0, res.stderr
    assert _kinds(sc) == {"Foo": "web", "Bar": "web"}


def test_default_dirs_are_applications_home_applications_and_system_applications(tmp_path):
    p = _tree(tmp_path)
    entries = [
        _sp_entry(p["bar"], "Bar"),                           # $HOME/Applications
        _sp_entry("/Applications/RealButAbsent.app", "RealButAbsent"),
        _sp_entry("/System/Applications/Safari.app", "Safari", obtained="apple"),
        _sp_entry("/System/Library/CoreServices/Dock.app", "Dock", obtained="apple"),
        _sp_entry("/Library/Printers/X/Y.app", "Y"),
    ]
    res, sc = _run(tmp_path, entries)
    assert res.returncode == 0, res.stderr
    assert _kinds(sc) == {"Bar": "web", "RealButAbsent": "web", "Safari": "system"}


def test_exclusions_file_drops_ascendo_and_helper_apps(tmp_path):
    p = _tree(tmp_path)
    excl = tmp_path / "excl.txt"
    excl.write_text("# comment\n\nFoo\nGoogle*\n")
    res, sc = _run(tmp_path, [_sp_entry(p["foo"], "Foo"), _sp_entry(p["bar"], "Bar"),
                              _sp_entry(p["ascendo"], "Ascendo")],
                   env_extra={"ASCENDO_APP_DIRS": f"{p['apps']}:{p['home_apps']}",
                              "ASCENDO_INVENTORY_EXCLUSIONS": str(excl)})
    assert _kinds(sc) == {"Bar": "web", "Ascendo": "web"}   # custom file replaces default


def test_shipped_exclusions_drop_ascendo_itself(tmp_path):
    p = _tree(tmp_path)
    res, sc = _run(tmp_path, [_sp_entry(p["ascendo"], "Ascendo"), _sp_entry(p["foo"], "Foo")],
                   env_extra={"ASCENDO_APP_DIRS": str(p["apps"])})
    assert _kinds(sc) == {"Foo": "web"}


def test_apps_below_ascendo_home_are_dropped_with_default_dirs(tmp_path):
    p = _tree(tmp_path)
    res, sc = _run(tmp_path, [_sp_entry(p["bar"], "Bar")],
                   env_extra={"ASCENDO_HOME": str(p["home_apps"])})
    assert _kinds(sc) == {}


def test_ipad_apps_are_filed_under_mas_not_web_with_plist_identity(tmp_path):
    p = _tree(tmp_path)
    make_ipad_app(p["apps"], "WiFiman", 1385561119, "0.33.0", item_name="WiFiman Wizard")
    make_ipad_app(p["apps"], "Wrapperonly", 42, "7.1")           # sp says unknown vendor
    (p["apps"] / "NoPlist.app").mkdir()
    entries = [
        _sp_entry(p["apps"] / "WiFiman.app", "WiFiman", obtained="ios_app_store", version="x"),
        _sp_entry(p["apps"] / "Wrapperonly.app", "Wrapperonly", obtained="unknown"),
        _sp_entry(p["apps"] / "NoPlist.app", "NoPlist", obtained="ios_app_store", version="3.0"),
        _sp_entry(p["foo"], "Foo"),
    ]
    res, sc = _run(tmp_path, entries, env_extra={"ASCENDO_APP_DIRS": str(p["apps"])})
    assert res.returncode == 0, res.stderr
    kinds = _kinds(sc)
    assert kinds == {"WiFiman Wizard": "mas", "Wrapperonly": "mas", "NoPlist": "mas",
                     "Foo": "web"}
    wifi = next(i for i in sc.items if i.id == "WiFiman Wizard")
    assert wifi.current_version == "0.33.0"
    assert wifi.source.feed == str(p["apps"] / "WiFiman.app")
    assert next(i for i in sc.items if i.id == "NoPlist").current_version == "3.0"
    ipad = [m for m in sc.messages if m.code == "IPAD_APP"]
    assert len(ipad) == 3 and "App Store app, not" in ipad[0].text


def test_intel_only_app_warns_when_rosetta_is_absent(tmp_path):
    p = _tree(tmp_path)
    entries = [_sp_entry(p["foo"], "Foo", arch="arch_i64"), _sp_entry(p["bar"], "Bar")]
    env = {"ASCENDO_APP_DIRS": f"{p['apps']}:{p['home_apps']}"}
    _, sc = _run(tmp_path, entries, env_extra={**env, "ASCENDO_ROSETTA_PRESENT": "0"})
    warn = [m for m in sc.messages if m.code == "INTEL_ONLY"]
    assert len(warn) == 1 and "Foo" in warn[0].text and warn[0].level.value == "warn"
    _, sc2 = _run(tmp_path, entries, env_extra={**env, "ASCENDO_ROSETTA_PRESENT": "1"})
    assert not [m for m in sc2.messages if m.code == "INTEL_ONLY"]
    _, sc3 = _run(tmp_path, entries, env_extra={**env, "ASCENDO_ROSETTA_PRESENT": "0",
                                                "ASCENDO_HOST_ARCH": "x86_64"})
    assert not [m for m in sc3.messages if m.code == "INTEL_ONLY"]


def test_failed_system_profiler_emits_no_bogus_package(tmp_path):
    bad = tmp_path / "bad_sp"
    bad.write_text("#!/usr/bin/env bash\n"
                   "if [ \"$1\" = '--version' ]; then echo v; exit 0; fi\n"
                   "echo boom >&2; exit 1\n")
    bad.chmod(0o755)
    res, sc = _run(tmp_path, [], sp=bad)
    assert res.returncode == 30
    assert sc.items == []
    assert any(m.level.value == "error" for m in sc.messages)


def test_sidecar_to_packages_drops_failed_items(tmp_path):
    from ascendo.models.sidecar import parse_sidecar
    from ascendo_macos.inventory import MacOSInventory

    p = _tree(tmp_path)
    _, sc = _run(tmp_path, [_sp_entry(p["foo"], "Foo")],
                 env_extra={"ASCENDO_APP_DIRS": str(p["apps"])})
    raw = json.loads(sc.model_dump_json(by_alias=True))
    bad = dict(raw["items"][0], id="inventory:system-profiler-error", name="inventory:system-profiler-error",
               status="failed")
    raw["items"].append(bad)
    raw["summary"]["total"] = len(raw["items"])
    raw["summary"]["failed"] = 1
    sc2 = parse_sidecar(json.dumps(raw))
    inv = MacOSInventory(scripts_dir=ADAPTER_ROOT / "scripts", lib_dir=ADAPTER_ROOT / "lib")
    pkgs = inv._sidecar_to_packages(sc2, categories=None)
    assert [pk.id for pk in pkgs] == ["Foo"]
