"""iPad / iOS-on-Mac App Store apps: identity scan, iTunes Lookup gate and the
report-only behaviour of the mas phase scripts.

Fakes only (mas_fakes.py: fake mas / sudo / curl, temp app dirs) -- no network,
no App Store GUI, no writes into /Applications.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .mas_fakes import make_ipad_app, mas_env

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib"
SCRIPTS = ADAPTER_ROOT / "scripts" / "mas"

sys.path.insert(0, str(LIB))
import appstore_lookup as al  # noqa: E402

US = "\x1f"
WIFIMAN_ID = 1385561119
LOOKUP_WIFIMAN_NEWER = json.dumps({"results": [{"trackId": WIFIMAN_ID, "version": "0.34.0"}]})


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


# -- pure Python ---------------------------------------------------------------


def test_read_itunes_metadata_extracts_fields():
    raw = plistlib.dumps({"itemId": 1385561119, "bundleShortVersionString": "0.33.0",
                          "itemName": "WiFiman"})
    assert al.read_itunes_metadata(raw) == {
        "itemId": "1385561119", "bundleShortVersionString": "0.33.0", "itemName": "WiFiman"}


def test_read_itunes_metadata_falls_back_to_bundle_version_and_display_name():
    raw = plistlib.dumps({"itemId": 5, "bundleVersion": "12", "bundleDisplayName": "Foo"})
    meta = al.read_itunes_metadata(raw)
    assert meta["bundleShortVersionString"] == "12" and meta["itemName"] == "Foo"


@pytest.mark.parametrize("raw", [b"", b"not a plist", plistlib.dumps({"no": "itemId"}),
                                 plistlib.dumps(["a list"])])
def test_read_itunes_metadata_rejects_garbage(raw):
    assert al.read_itunes_metadata(raw) is None


def test_parse_lookup_and_validity():
    body = json.dumps({"resultCount": 2, "results": [
        {"trackId": 1, "version": "2.0"}, {"trackId": 2, "version": "3"}, {"nope": 1}]})
    assert al.parse_lookup(body) == {"1": "2.0", "2": "3"}
    assert al.lookup_is_valid(body)
    assert al.lookup_is_valid('{"results": []}')          # store knows none of the ids
    for bad in ("", "<html>", "[]", '{"results": 3}', "null"):
        assert not al.lookup_is_valid(bad)
    assert al.parse_lookup("garbage") == {}


def test_pending_ios_only_strictly_newer():
    installed = [
        {"name": "UniFi", "itemId": "1", "version": "10.0.1", "path": "/a"},
        {"name": "WiFiman", "itemId": "2", "version": "0.33.0", "path": "/b"},
        {"name": "Picsart", "itemId": "3", "version": "30.1", "path": "/c"},
        {"name": "Odd", "itemId": "4", "version": "", "path": "/d"},
        {"name": "Gone", "itemId": "5", "version": "1.0", "path": "/e"},
    ]
    store = {"1": "10.0.1", "2": "0.34.0", "3": "29.9", "4": "2.0"}
    pending = al.pending_ios(installed, store)
    assert [(p["name"], p["installed"], p["store"]) for p in pending] == [
        ("WiFiman", "0.33.0", "0.34.0")]


def test_scan_ipad_apps_dedups_by_item_id_and_ignores_non_wrappers(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    make_ipad_app(a, "WiFiman", WIFIMAN_ID, "0.33.0")
    make_ipad_app(b, "WiFiman copy", WIFIMAN_ID, "0.33.0")     # same itemId: dropped
    make_ipad_app(a, "UniFi", 42, "1.0", item_name="UniFi Network")
    (a / "Plain.app" / "Contents").mkdir(parents=True)          # not a wrapper
    (a / "notes.txt").write_text("x")
    found = al.scan_ipad_apps([str(a), str(b), str(tmp_path / "missing")])
    assert [(f["name"], f["itemId"]) for f in found] == [
        ("UniFi Network", "42"), ("WiFiman", str(WIFIMAN_ID))]


def test_is_app_store_managed(tmp_path):
    ipad = make_ipad_app(tmp_path, "P", 1, "1")
    mas_app = tmp_path / "M.app"
    (mas_app / "Contents" / "_MASReceipt").mkdir(parents=True)
    web = tmp_path / "W.app"
    (web / "Contents").mkdir(parents=True)
    assert al.is_app_store_managed(str(ipad))
    assert al.is_app_store_managed(str(mas_app))
    assert not al.is_app_store_managed(str(web))
    assert not al.is_app_store_managed("")


# -- bash interface (ascendo_appstore_ios.sh) -----------------------------------


def _bash(fakes: dict, snippet: str, extra_env: dict | None = None):
    env = dict(os.environ)
    env.update(fakes["env"])
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}/ascendo_appstore_ios.sh"; {snippet}'],
        capture_output=True, text=True, env=env, check=False)


def test_bash_scan_and_managed_predicate(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    ipad = make_ipad_app(apps, "WiFiman", WIFIMAN_ID, "0.33.0")
    fakes = mas_env(tmp_path, app_dirs=[apps])
    res = _bash(fakes, "ascendo_ios_apps_scan; ascendo_app_store_managed '%s' && echo MANAGED" % ipad)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].split(US) == ["WiFiman", str(WIFIMAN_ID), "0.33.0", str(ipad)]
    assert lines[1] == "MANAGED"


def test_bash_country_from_mas_config_and_override(tmp_path):
    fakes = mas_env(tmp_path)
    res = _bash(fakes, "ascendo_ios_store_country", {"FAKE_MAS_STORE": "PL"})
    assert res.stdout.strip() == "pl"
    res = _bash(fakes, "ascendo_ios_store_country", {"ASCENDO_APPSTORE_COUNTRY": "DE"})
    assert res.stdout.strip() == "de"
    # unknown / malformed => nothing (the lookup then omits &country=)
    assert _bash(fakes, "ascendo_ios_store_country").stdout.strip() == ""
    assert _bash(fakes, "ascendo_ios_store_country",
                 {"ASCENDO_APPSTORE_COUNTRY": "../etc"}).stdout.strip() == ""


def test_bash_pending_builds_https_lookup_url_with_country(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    make_ipad_app(apps, "WiFiman", WIFIMAN_ID, "0.33.0")
    make_ipad_app(apps, "UniFi", 42, "1.0")
    body = tmp_path / "lookup.json"
    body.write_text(LOOKUP_WIFIMAN_NEWER)
    fakes = mas_env(tmp_path, app_dirs=[apps])
    res = _bash(fakes, "ascendo_ios_apps_pending", {
        "FAKE_CURL_BODY_FILE": str(body), "FAKE_MAS_STORE": "PL"})
    assert res.returncode == 0, res.stderr
    rec = res.stdout.strip().split(US)
    assert rec[:4] == ["WiFiman", str(WIFIMAN_ID), "0.33.0", "0.34.0"]
    curl = (fakes["state"] / "curl.log").read_text()
    assert "--proto =https" in curl and "--max-time" in curl and "--max-filesize" in curl
    assert "https://itunes.apple.com/lookup?id=" in curl
    assert f"id=42,{WIFIMAN_ID}" in curl        # apps sorted by file name: UniFi, WiFiman
    assert "&country=pl" in curl


def test_bash_pending_no_ipad_apps_never_touches_the_network(tmp_path):
    fakes = mas_env(tmp_path)          # empty app dir
    res = _bash(fakes, "ascendo_ios_apps_pending; echo rc=$?")
    assert res.stdout.strip() == "rc=0"
    assert not (fakes["state"] / "curl.log").exists()


@pytest.mark.parametrize("env", [{"FAKE_CURL_RC": "22"}, {"FAKE_CURL_RC": "28"}])
def test_bash_pending_lookup_failure_is_rc2_not_empty_success(tmp_path, env):
    apps = tmp_path / "apps"
    apps.mkdir()
    make_ipad_app(apps, "WiFiman", WIFIMAN_ID, "0.33.0")
    fakes = mas_env(tmp_path, app_dirs=[apps])
    res = _bash(fakes, "ascendo_ios_apps_pending; echo rc=$?", env)
    assert res.stdout.strip() == "rc=2"


def test_bash_pending_garbage_response_is_rc2(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    make_ipad_app(apps, "WiFiman", WIFIMAN_ID, "0.33.0")
    body = tmp_path / "garbage.html"
    body.write_text("<html>rate limited</html>")
    fakes = mas_env(tmp_path, app_dirs=[apps])
    res = _bash(fakes, "ascendo_ios_apps_pending; echo rc=$?", {"FAKE_CURL_BODY_FILE": str(body)})
    assert res.stdout.strip().endswith("rc=2")


# -- phase scripts: report-only ---------------------------------------------------


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run(script: str, fakes: dict, out: Path, rid: str, *extra: str, env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(fakes["env"])
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(SCRIPTS / script), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=env, check=False)


def _ipad_fakes(tmp_path: Path, *, outdated: str | None = None):
    apps = tmp_path / "apps"
    apps.mkdir()
    make_ipad_app(apps, "WiFiman", WIFIMAN_ID, "0.33.0")
    body = tmp_path / "lookup.json"
    body.write_text(LOOKUP_WIFIMAN_NEWER)
    fakes = mas_env(tmp_path, app_dirs=[apps], outdated_text=outdated)
    fakes["env"]["FAKE_CURL_BODY_FILE"] = str(body)
    return fakes


TOKEN = "ASCENDO-ACTION-REQUIRED reason=ipad_pending cur=0.33.0 cand=0.34.0"


@pytest.mark.parametrize("script,phase", [("check.sh", "check"), ("plan.sh", "plan")])
def test_check_plan_report_pending_ipad_as_planned_with_token(tmp_path, script, phase):
    fakes = _ipad_fakes(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run(script, fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / f"{phase}__mas.json")
    item = next(i for i in sc.items if i.id == "ipad:WiFiman")
    assert item.status.value == "planned"
    assert (item.current_version, item.target_version) == ("0.33.0", "0.34.0")
    assert any(m.text == f"ipad:WiFiman: iPad app - update it in the App Store (mas cannot) | {TOKEN}"
               for m in sc.messages)


def test_apply_reports_ipad_skipped_and_never_upgrades_it(tmp_path):
    fakes = _ipad_fakes(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run("apply.sh", fakes, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    by_id = {i.id: i.status.value for i in sc.items}
    assert by_id["ipad:WiFiman"] == "skipped"
    assert by_id["1153157709"] == "success"
    assert any(TOKEN in (m.text or "") for m in sc.messages)
    # The only upgrade calls are the two native ids -- nothing iPad-shaped,
    # and no osascript/App Store GUI anywhere in the script tree it runs.
    upgrades = (fakes["state"] / "upgrades.log").read_text()
    assert str(WIFIMAN_ID) not in upgrades and "WiFiman" not in upgrades


def test_apply_dry_run_also_reports_ipad_as_skipped_not_planned(tmp_path):
    fakes = _ipad_fakes(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run("apply.sh", fakes, out, rid, "--dry-run")
    sc = _parse(out / rid / "apply__mas.json")
    assert {i.id: i.status.value for i in sc.items}["ipad:WiFiman"] == "skipped"


def test_ipad_reported_even_when_no_native_updates(tmp_path):
    fakes = _ipad_fakes(tmp_path, outdated="")
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run("apply.sh", fakes, out, rid)
    sc = _parse(out / rid / "apply__mas.json")
    assert {i.id: i.status.value for i in sc.items} == {"ipad:WiFiman": "skipped"}
    assert (fakes["state"] / "sudo.log").exists() is False


def test_ipad_up_to_date_emits_nothing(tmp_path):
    fakes = _ipad_fakes(tmp_path)
    Path(fakes["env"]["FAKE_CURL_BODY_FILE"]).write_text(
        json.dumps({"results": [{"trackId": WIFIMAN_ID, "version": "0.33.0"}]}))
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run("check.sh", fakes, out, rid)
    sc = _parse(out / rid / "check__mas.json")
    assert not any(i.id.startswith("ipad:") for i in sc.items)
    assert "ASCENDO-ACTION-REQUIRED" not in "\n".join(m.text or "" for m in sc.messages)


@pytest.mark.parametrize("script,phase", [("check.sh", "check"), ("plan.sh", "plan"),
                                          ("apply.sh", "apply")])
def test_lookup_failure_is_soft_unknown_never_all_current(tmp_path, script, phase):
    fakes = _ipad_fakes(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    res = _run(script, fakes, out, rid, env_extra={"FAKE_CURL_RC": "22"})
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / f"{phase}__mas.json")
    by_id = {i.id: i.status.value for i in sc.items}
    assert by_id["ipad:lookup"] == "skipped"
    assert not any(k == "ipad:WiFiman" for k in by_id)
    texts = "\n".join(m.text or "" for m in sc.messages)
    assert "iPad app check unavailable" in texts and "unknown" in texts
    # the native part of the phase still ran normally
    # (check/plan key items by display name, apply by numeric id -- pre-existing)
    assert (by_id.get("Keka") or by_id.get("1153157709")) in {"planned", "success"}


def test_filter_can_select_ipad_by_name_or_hides_it(tmp_path):
    fakes = _ipad_fakes(tmp_path)
    out, rid = tmp_path / "out", str(uuid.uuid4())
    _run("check.sh", fakes, out, rid, "--filter", "Keka")
    sc = _parse(out / rid / "check__mas.json")
    assert not any(i.id.startswith("ipad:") for i in sc.items)
    out2, rid2 = tmp_path / "out2", str(uuid.uuid4())
    _run("check.sh", fakes, out2, rid2, "--filter", "WiFiman")
    sc2 = _parse(out2 / rid2 / "check__mas.json")
    assert any(i.id == "ipad:WiFiman" for i in sc2.items)


def test_gui_fallback_is_not_wired_into_any_phase_script():
    for script in SCRIPTS.glob("*.sh"):
        if script.name == "gui_fallback.sh":
            continue
        assert "gui_fallback" not in script.read_text(), script.name
        assert "osascript" not in script.read_text(), script.name
