"""Task 15 -- web discovery scope (roots, exclusions, iPad) and the
"absent registry apps are silent" delta (v1.5.4 DJI Assistant).

Fixture app trees in tmp dirs only; nothing reads the real /Applications.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from .mas_fakes import make_ipad_app

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"
SCRIPTS = REPO_ROOT / "adapters" / "macos" / "scripts" / "web"


def _app(root: Path, name: str, bundle_id: str, version: str = "1.0") -> Path:
    app = root / f"{name}.app"
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        f'<key>CFBundleIdentifier</key><string>{bundle_id}</string>'
        f'<key>CFBundleShortVersionString</key><string>{version}</string>'
        f'<key>CFBundleName</key><string>{name}</string>'
        '</dict></plist>')
    return app


def _env(tmp: Path, **extra: str) -> dict:
    # Drop inherited ASCENDO_*/MAC_UPDATE_* but KEEP the tripwire seams (conftest).
    keep = ("ASCENDO_WEB_MDFIND_BIN", "ASCENDO_WEB_SP_BIN", "ASCENDO_WEB_SP_CACHE",
            "ASCENDO_WEB_UPDATER_BIN", "ASCENDO_WEB_UPDATER_ALLOW_PREFIX",
            "ASCENDO_MSUPDATE_BIN", "ASCENDO_MAU_PREFS_FILE", "ASCENDO_APP_DIRS_UNUSED")
    env = {k: v for k, v in os.environ.items()
           if k in keep or not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    env.update({"HOME": str(tmp / "home"), "ASCENDO_WEB_BREW_CASKS": "",
                "ASCENDO_WEB_MAS_BUNDLE_IDS": "", "ASCENDO_WEB_APPLE_BUNDLES": ""})
    env.update(extra)
    return env


def _discover(tmp: Path, **extra: str):
    (tmp / "home").mkdir(exist_ok=True)
    res = subprocess.run(["bash", str(LIB / "web_discovery.sh"), "--emit-json"],
                         capture_output=True, text=True, env=_env(tmp, **extra), check=False)
    apps = [json.loads(ln) for ln in res.stdout.splitlines() if ln.startswith("{")]
    sentinel = [ln for ln in res.stdout.splitlines() if ln.startswith("DISCOVERY_")]
    return res, {a["bundle_id"]: a for a in apps}, sentinel


def test_colon_list_of_roots_is_walked_and_missing_roots_are_skipped(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    _app(a, "One", "com.t.one")
    _app(b, "Two", "com.t.two")
    _app(b, "OneDup", "com.t.one")           # same bundle id in a second root
    res, apps, sentinel = _discover(
        tmp_path, ASCENDO_WEB_APPS_ROOT=f"{a}:{tmp_path / 'nope'}:{b}")
    assert res.returncode == 0, res.stderr
    assert set(apps) == {"com.t.one", "com.t.two"}
    assert apps["com.t.one"]["app_path"] == str(a / "One.app")     # first root wins
    assert sentinel[-1].startswith("DISCOVERY_OK")


def test_all_roots_missing_is_a_discovery_failure_not_zero_apps(tmp_path):
    res, apps, sentinel = _discover(tmp_path, ASCENDO_WEB_APPS_ROOT=f"{tmp_path/'x'}:{tmp_path/'y'}")
    assert res.returncode == 3
    assert sentinel and sentinel[0].startswith("DISCOVERY_FAILED")


def test_default_roots_include_home_applications(tmp_path):
    home_apps = tmp_path / "home" / "Applications"
    home_apps.mkdir(parents=True)
    _app(home_apps, "Mine", "com.t.mine")
    res, apps, _ = _discover(tmp_path)
    assert "com.t.mine" in apps


def test_ascendo_itself_and_listed_names_are_excluded(tmp_path):
    root = tmp_path / "A"
    root.mkdir()
    _app(root, "Ascendo", "dev.example.asc")
    _app(root, "Keep", "com.t.keep")
    _app(root, "Skipme", "com.t.skip")
    excl = tmp_path / "excl.txt"
    excl.write_text("# c\nSkip*\n")
    _, apps, _ = _discover(tmp_path, ASCENDO_WEB_APPS_ROOT=str(root))
    assert set(apps) == {"dev.example.asc", "com.t.keep", "com.t.skip"} - {"dev.example.asc"}
    _, apps2, _ = _discover(tmp_path, ASCENDO_WEB_APPS_ROOT=str(root),
                            ASCENDO_INVENTORY_EXCLUSIONS=str(excl))
    assert set(apps2) == {"dev.example.asc", "com.t.keep"}


def test_ipad_wrapper_is_never_a_web_app(tmp_path):
    root = tmp_path / "A"
    root.mkdir()
    make_ipad_app(root, "WiFiman", 1385561119, "0.33.0")
    # a wrapper that ALSO carries an Info.plist must still be owned by mas
    wrapper = make_ipad_app(root, "Hybrid", 99, "1.0")
    _app(root, "Hybrid", "com.t.hybrid")
    assert (wrapper / "Wrapper" / "iTunesMetadata.plist").is_file()
    _, apps, _ = _discover(tmp_path, ASCENDO_WEB_APPS_ROOT=str(root))
    assert apps == {}
    _, owned, _ = _discover(tmp_path, ASCENDO_WEB_APPS_ROOT=str(root), ASCENDO_WEB_INCLUDE_OWNED="1")
    assert owned["com.t.hybrid"]["owned_by"] == "mas"


# -- delta v1.5.4: absent registry apps are silent (DJI Assistant) -------------

def _phase(phase: str, tmp: Path, apps_root: Path, *extra: str, registry: Path | None = None):
    out = tmp / "out"
    rid = str(uuid.uuid4())
    env = _env(tmp, ASCENDO_WEB_APPS_ROOT=str(apps_root),
               ASCENDO_WEB_USER_REGISTRY_PATH="", ASCENDO_SUDO_WARM_DISABLE="1",
               ASCENDO_WEB_REGISTRY_PATH=str(registry) if registry else "",
               ASCENDO_WEB_SEEN_FILE=str(tmp / "seen.tsv"),
               ASCENDO_RUNS_DIR=str(tmp / "runs"),
               ASCENDO_MAU_PREFS_FILE=str(tmp / "mau.plist"),
               ASCENDO_LSAPPINFO_BIN="/usr/bin/false",
               PYTHONPATH=f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos")
    res = subprocess.run(
        ["bash", str(SCRIPTS / f"{phase}.sh"), "--run-id", rid, "--trigger", "cli",
         "--profile", "full", "--output-dir", str(out), *extra],
        capture_output=True, text=True, env=env, check=False)
    sc = json.loads((out / rid / f"{phase}__web.json").read_text())
    return res, sc


def test_absent_registry_app_dji_assistant_is_silent_in_check_plan_apply(tmp_path):
    (tmp_path / "home").mkdir()
    empty = tmp_path / "Applications"
    empty.mkdir()
    # Hermetic registry holding ONLY the shipped dji-assistant entry: a registry
    # with other apps would resolve real installed apps by absolute path/mdfind.
    shipped = (REPO_ROOT / "adapters" / "macos" / "config" / "web_apps.toml").read_text()
    assert 'slug = "dji-assistant"' in shipped          # the fixture really is registered
    assert 'bundle_id = "DJI.Assistant"' in shipped and 'display_name = "DJI Assistant 2"' in shipped
    block = shipped[shipped.index('slug = "dji-assistant"'):]
    reg = tmp_path / "dji_only.toml"
    # Neutral display name / bundle id: the registry's "/Applications/<display>.app"
    # guess must not match a real DJI Assistant on the host running the test.
    block = block.replace('display_name = "DJI Assistant 2"', 'display_name = "Absent Fixture App"')
    block = block.replace('bundle_id = "DJI.Assistant"', 'bundle_id = "zzz.absent.fixture"')
    reg.write_text('schema = "ascendo-web-apps/v1"\n[[app]]\n' + block)
    for phase, extra in (("check", ()), ("plan", ()), ("apply", ()), ("apply", ("--dry-run",))):
        res, sc = _phase(phase, tmp_path, empty, *extra, registry=reg)
        assert res.returncode == 0, (phase, res.stderr)
        blob = json.dumps(sc).lower()
        assert "dji" not in blob, (phase, blob[:500])
        assert not [i for i in sc["items"] if "dji" in i["id"].lower()]


def test_present_dji_assistant_is_reported(tmp_path):
    (tmp_path / "home").mkdir()
    root = tmp_path / "Applications"
    root.mkdir()
    _app(root, "DJI Assistant 2", "DJI.Assistant", "2.1.0")
    shipped = (REPO_ROOT / "adapters" / "macos" / "config" / "web_apps.toml").read_text()
    reg = tmp_path / "dji_only.toml"
    reg.write_text('schema = "ascendo-web-apps/v1"\n[[app]]\n'
                   + shipped[shipped.index('slug = "dji-assistant"'):])
    _, sc = _phase("check", tmp_path, root, registry=reg)
    assert any(i["id"] == "web:dji-assistant" for i in sc["items"])


def test_shipped_registry_nondry_apply_with_empty_apps_root_hits_only_tripwires(tmp_path, tripwire):
    """Ruling 44 regression: the incident run (shipped registry, non-dry apply,
    empty apps root) resolved REAL apps by absolute path. With the suite-wide
    tripwire every updater / installer / launcher it reaches is a logged fake."""
    from .conftest import TRIPWIRE_TOOLS, tripwire_hits

    (tmp_path / "home").mkdir()
    empty = tmp_path / "Applications"
    empty.mkdir()
    res, sc = _phase("apply", tmp_path, empty, registry=REPO_ROOT / "adapters" / "macos"
                     / "config" / "web_apps.toml")
    assert res.returncode == 0, res.stderr
    assert sc["category"] == "web"
    assert set(tripwire_hits(tripwire)) <= set(TRIPWIRE_TOOLS)
    for it in sc["items"]:
        assert it["status"] != "success", it    # nothing real can have been installed
