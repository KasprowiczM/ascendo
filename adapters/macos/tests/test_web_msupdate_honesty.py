"""Task 14 (Spec B + F): Microsoft AutoUpdate honesty. Fake msupdate / curl on
PATH, fixture prefs via ASCENDO_MAU_PREFS_FILE; nothing real is touched."""
from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_web_phase_check_plan import (  # noqa: E402
    _make_fake_app, _read_sidecar, _run_phase,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"

LIST_WORD = ("Checking for updates...\n"
             "MSWD2019  Microsoft Word Update 16.111.1 (26071913)\n")
LIST_NONE = "Checking for updates...\nNo updates available\n"


def _fake_msupdate(d: Path, body: str, log: Path | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "msupdate"
    logline = f'echo "$@" >> {log}\n' if log else ""
    f.write_text("#!/bin/sh\n" + logline + body)
    f.chmod(0o755)
    return d


def _prefs(path: Path, data: dict) -> Path:
    path.write_bytes(plistlib.dumps(data))
    return path


def _env(tmp: Path, extra: dict | None = None, fake: Path | None = None) -> dict:
    env = {**os.environ}
    if fake is not None:
        env["PATH"] = f"{fake}:{env['PATH']}"
    env["ASCENDO_WEB_APPS_ROOT"] = str(tmp / "Applications")
    if extra:
        env.update(extra)
    return env


def _sh(snippet: str, env: dict) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_json.sh
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/msupdate.sh
        {snippet}
    """)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def _cfg(app_id: str, display: str) -> str:
    return json.dumps({"slug": "x", "display_name": display,
                       "msupdate": {"app_id": app_id}})


def _bundle(tmp: Path, name: str, short: str) -> Path:
    return _make_fake_app(tmp, name, short, bundle_id=f"com.fixture.{name.split()[-1].lower()}")


def _check(tmp: Path, app_id: str, display: str, env: dict) -> subprocess.CompletedProcess:
    return _sh(f"msupdate_check x {shlex.quote(_cfg(app_id, display))}", env)


# ---- Spec B ---------------------------------------------------------------
def test_interrupted_listing_is_unknown_never_installed_version(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.111.1")
    fake = _fake_msupdate(tmp_path / "bin", 'echo "XPC Connection to updater invalidated"\nexit 1\n')
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", _env(tmp_path, fake=fake))
    assert r.stdout.strip() == "__MAU_UNKNOWN__", r.stderr
    assert "16.111.1" not in r.stdout


def test_exit_zero_but_interrupted_text_is_unknown(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.111.1")
    fake = _fake_msupdate(tmp_path / "bin", 'echo "Update Assistant terminated"\nexit 0\n')
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", _env(tmp_path, fake=fake))
    assert r.stdout.strip() == "__MAU_UNKNOWN__"


def test_listing_is_bounded_by_timeout(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.111.1")
    fake = _fake_msupdate(tmp_path / "bin", "sleep 30\n")
    t0 = time.time()
    r = _check(tmp_path, "MSWD2019", "Microsoft Word",
               _env(tmp_path, {"ASCENDO_MSUPDATE_CHECK_TIMEOUT": "1"}, fake))
    assert time.time() - t0 < 10
    assert r.stdout.strip() == "__MAU_UNKNOWN__"


def test_offered_version_extracted_from_listing(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.100.0")
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_WORD}EOF\n")
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", _env(tmp_path, fake=fake))
    assert r.stdout.strip() == "16.111.1"


def test_unparseable_offer_falls_back_to_pending(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.100.0")
    fake = _fake_msupdate(tmp_path / "bin", "echo 'MSWD2019  Microsoft Word Update'\n")
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", _env(tmp_path, fake=fake))
    assert r.stdout.strip() == "pending"


def test_three_msupdate_rows_one_listing_per_run(tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_NONE}EOF\n", log)
    reg = tmp_path / "registry.toml"
    rows = ""
    for slug, bid, name, aid in (("ms-word", "com.fixture.word", "Microsoft Word", "MSWD2019"),
                                 ("ms-excel", "com.fixture.excel", "Microsoft Excel", "XCEL2019"),
                                 ("ms-onenote", "com.fixture.onenote", "Microsoft OneNote", "ONMC2019")):
        _make_fake_app(tmp_path, name, "16.100.0", bundle_id=bid)
        rows += (f'[[app]]\nslug = "{slug}"\nbundle_id = "{bid}"\n'
                 f'display_name = "{name}"\nhandler = "msupdate"\n'
                 f'[app.msupdate]\napp_id = "{aid}"\n')
    reg.write_text('schema = "ascendo-web-apps/v2"\n' + rows)
    out = tmp_path / "out"
    r, run_id = _run_phase("check", reg, out, env_extra={"PATH": f"{fake}:{os.environ['PATH']}"})
    assert r.returncode == 0, r.stderr
    calls = [ln for ln in log.read_text().splitlines() if "--list" in ln]
    assert len(calls) == 1, calls
    items = {i["name"] if "name" in i else i["id"]: i for i in _read_sidecar(out, run_id, "check")["items"]}
    assert len(items) == 3
    assert (out / run_id / "_msupdate_list.txt").exists()
    assert (out / run_id / "_msupdate_list.rc").read_text().strip() == "0"


def test_check_phase_maps_unknown_listing_to_skipped_error_soft(tmp_path: Path) -> None:
    fake = _fake_msupdate(tmp_path / "bin", "echo 'Update Assistant terminated'\nexit 1\n")
    reg = tmp_path / "registry.toml"
    _make_fake_app(tmp_path, "Microsoft Word", "16.100.0", bundle_id="com.fixture.word")
    reg.write_text('schema = "ascendo-web-apps/v2"\n[[app]]\nslug = "ms-word"\n'
                   'bundle_id = "com.fixture.word"\ndisplay_name = "Microsoft Word"\n'
                   'handler = "msupdate"\n[app.msupdate]\napp_id = "MSWD2019"\n')
    out = tmp_path / "out"
    r, run_id = _run_phase("check", reg, out, env_extra={"PATH": f"{fake}:{os.environ['PATH']}"})
    sc = _read_sidecar(out, run_id, "check")
    item = sc["items"][0]
    assert item["status"] == "skipped"
    msgs = " ".join(m["text"] for m in sc["messages"])
    assert "mau_check_failed" in msgs and "NOT current" in msgs
    assert "code=error_soft" in msgs


# ---- Spec F2: DeferralDays ------------------------------------------------
def test_deferral_days_quarantine_is_not_current(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.100.0")
    prefs = _prefs(tmp_path / "p.plist",
                   {"OptionalUpdatesDeferrals": {"DeferralDays": {"MSWD2019": 7}}})
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_NONE}EOF\n")
    env = _env(tmp_path, {"ASCENDO_MAU_PREFS_FILE": str(prefs)}, fake)
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", env)
    assert r.stdout.strip() == "__MAU_QUARANTINED__"
    # no deferral => plain current (installed version echoed)
    r2 = _check(tmp_path, "XCEL2019", "Microsoft Excel", env)
    assert "__MAU" not in r2.stdout


# ---- Spec F3: regression ---------------------------------------------------
def test_offer_not_newer_than_installed_is_regression_with_channel(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.112.0")
    prefs = _prefs(tmp_path / "p.plist", {"ChannelName": "Beta"})
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_WORD}EOF\n")
    env = _env(tmp_path, {"ASCENDO_MAU_PREFS_FILE": str(prefs),
                          "ASCENDO_WEB_PROBE_NOTE_FILE": str(tmp_path / "note")}, fake)
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", env)
    assert r.stdout.strip() == "__MAU_REGRESSED__"
    note = (tmp_path / "note").read_text()
    assert "channel=Beta" in note and "16.111.1" in note and "16.112.0" in note


def test_equal_short_version_is_a_legitimate_update(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.111.1")
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_WORD}EOF\n")
    r = _check(tmp_path, "MSWD2019", "Microsoft Word", _env(tmp_path, fake=fake))
    assert r.stdout.strip() == "pending"  # surfaced as available, not the installed version


# ---- Spec F1: TEAMS21 pin -------------------------------------------------
def _teams_env(tmp: Path, pin: str, extra: dict | None = None) -> tuple[dict, Path]:
    _make_fake_app(tmp, "Microsoft Teams", "25306.805.4102.7211", bundle_id="com.microsoft.teams2")
    prefs = _prefs(tmp / "p.plist", {"OptionalUpdatesDeferrals":
                                     {"DeferralVersions": {"TEAMS21": pin}}})
    fake = _fake_msupdate(tmp / "bin", f"cat <<'EOF'\n{LIST_NONE}EOF\n")
    runs = tmp / "run"
    runs.mkdir(exist_ok=True)
    e = _env(tmp, {"ASCENDO_MAU_PREFS_FILE": str(prefs),
                   "ASCENDO_MSUPDATE_LIST_FILE": str(runs / "_msupdate_list.txt"),
                   **(extra or {})}, fake)
    return e, prefs


def test_stale_pin_released_only_when_opted_in_with_backup(tmp_path: Path) -> None:
    env, prefs = _teams_env(tmp_path, "25000.100", {"ASCENDO_MAU_MUTATE_PREFS": "1"})
    r = _sh("_MSU_PIN_MUTATE_ALLOWED=1 _msupdate_teams_pin_release \"$ASCENDO_WEB_APPS_ROOT/Microsoft Teams.app\"; "
            "printf '%s' \"$_MSU_PIN_NOTE\"", env)
    assert "mau_pin_released" in r.stdout, r.stderr
    assert "DeferralVersions" not in prefs.read_text() or "TEAMS21" not in prefs.read_text()
    assert (tmp_path / "run" / "com.microsoft.autoupdate2.pre-pin-release.plist").exists()
    backup = plistlib.loads((tmp_path / "run" / "com.microsoft.autoupdate2.pre-pin-release.plist").read_bytes())
    assert backup["OptionalUpdatesDeferrals"]["DeferralVersions"]["TEAMS21"] == "25000.100"


def test_pin_untouched_by_default_but_diagnosed(tmp_path: Path) -> None:
    env, prefs = _teams_env(tmp_path, "25000.100")
    before = prefs.read_bytes()
    r = _sh("_MSU_PIN_MUTATE_ALLOWED=1 _msupdate_teams_pin_release \"$ASCENDO_WEB_APPS_ROOT/Microsoft Teams.app\"; "
            "printf '%s' \"$_MSU_PIN_NOTE\"", env)
    assert "mau_pin_stale" in r.stdout
    assert prefs.read_bytes() == before


def test_pin_equal_to_installed_is_skipped_even_when_opted_in(tmp_path: Path) -> None:
    env, prefs = _teams_env(tmp_path, "25306.805.4102.7211", {"ASCENDO_MAU_MUTATE_PREFS": "1"})
    before = prefs.read_bytes()
    r = _sh("_MSU_PIN_MUTATE_ALLOWED=1 _msupdate_teams_pin_release \"$ASCENDO_WEB_APPS_ROOT/Microsoft Teams.app\"; "
            "printf '%s' \"$_MSU_PIN_NOTE\"", env)
    assert r.stdout == ""
    assert prefs.read_bytes() == before


def test_pin_recreated_is_not_claimed_released(tmp_path: Path) -> None:
    env, prefs = _teams_env(tmp_path, "25000.100", {"ASCENDO_MAU_MUTATE_PREFS": "1"})
    # a hostile prefs writer: every import restores the pin
    snippet = ('_msupdate_prefs_import() { cp "$ASCENDO_MAU_PREFS_FILE" /dev/null; return 0; }\n'
               '_MSU_PIN_MUTATE_ALLOWED=1; _msupdate_teams_pin_release "$ASCENDO_WEB_APPS_ROOT/Microsoft Teams.app"; '
               "printf '%s' \"$_MSU_PIN_NOTE\"")
    r = _sh(snippet, env)
    assert "mau_pin_recreated" in r.stdout


# ---- Spec F5: Teams oracle -------------------------------------------------
def _cask_curl(d: Path, version: str | None) -> None:
    c = d / "curl"
    if version is None:
        c.write_text("#!/bin/sh\nexit 22\n")
    else:
        c.write_text(f"#!/bin/sh\necho '{{\"version\": \"{version},9\"}}'\n")
    c.chmod(0o755)


def test_teams_oracle_current_cask_only(tmp_path: Path) -> None:
    env, _ = _teams_env(tmp_path, "25306.805.4102.7211",
                        {"ASCENDO_MSUPDATE_TEAMS_CASK_URL": "https://formulae.example/t.json",
                         "ASCENDO_WEB_PROBE_NOTE_FILE": str(tmp_path / "note")})
    _cask_curl(tmp_path / "bin", "25306.805.4102.7211")
    r = _check(tmp_path, "TEAMS21", "Microsoft Teams", env)
    assert r.stdout.strip() == "25306.805.4102.7211"
    assert (tmp_path / "note").read_text().startswith("current_cask_only\t")


def test_teams_oracle_newer_cask_is_offered(tmp_path: Path) -> None:
    env, _ = _teams_env(tmp_path, "25306.805.4102.7211",
                        {"ASCENDO_MSUPDATE_TEAMS_CASK_URL": "https://formulae.example/t.json"})
    _cask_curl(tmp_path / "bin", "25400.1.2.3")
    r = _check(tmp_path, "TEAMS21", "Microsoft Teams", env)
    assert r.stdout.strip() == "25400.1.2.3"


def test_teams_oracle_no_data_is_current_verified_only_with_valid_listing(tmp_path: Path) -> None:
    env, _ = _teams_env(tmp_path, "25306.805.4102.7211",
                        {"ASCENDO_MSUPDATE_TEAMS_CASK_URL": "https://formulae.example/t.json",
                         "ASCENDO_WEB_PROBE_NOTE_FILE": str(tmp_path / "note")})
    _cask_curl(tmp_path / "bin", None)
    r = _check(tmp_path, "TEAMS21", "Microsoft Teams", env)
    assert r.stdout.strip() == "25306.805.4102.7211"
    assert (tmp_path / "note").read_text().startswith("current_verified\t")


# ---- Spec F4: opt-in scoped install ---------------------------------------
def _install_fixture(tmp: Path, interrupt: bool = False) -> tuple[Path, Path, Path]:
    app = _bundle(tmp, "Microsoft Word", "16.100.0")
    plist = app / "Contents" / "Info.plist"
    log = tmp / "calls.log"
    body = f"""
case "$*" in
  *--install*)
    {'echo "XPC Connection to updater invalidated"' if interrupt else ''}
    {'' if interrupt else f'sed -i "" "s/16.100.0/16.111.1/" "{plist}"'}
    exit 0 ;;
  *--list*)
    cat <<'EOF'
{LIST_NONE}EOF
    ;;
esac
"""
    fake = _fake_msupdate(tmp / "bin", body, log)
    return fake, log, plist


def test_scoped_install_default_is_rc95_and_never_installs(tmp_path: Path) -> None:
    fake, log, _ = _install_fixture(tmp_path)
    r = _sh(f"msupdate_apply x {shlex.quote(_cfg('MSWD2019', 'Microsoft Word'))}",
            _env(tmp_path, fake=fake))
    assert r.returncode == 95
    assert not log.exists() or "--install" not in log.read_text()


def test_scoped_install_opt_in_measures_version_change(tmp_path: Path) -> None:
    fake, log, plist = _install_fixture(tmp_path)
    r = _sh(f"msupdate_apply x {shlex.quote(_cfg('MSWD2019', 'Microsoft Word'))}",
            _env(tmp_path, {"ASCENDO_MAU_SCOPED_INSTALL": "1"}, fake))
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "--install --wait 600 --apps MSWD2019" in calls
    assert "16.111.1" in plist.read_text()


def test_scoped_install_off_in_safe_and_quick_profiles(tmp_path: Path) -> None:
    fake, log, _ = _install_fixture(tmp_path)
    for prof in ("safe", "quick"):
        r = _sh(f"PROFILE_NAME={prof}; msupdate_apply x {shlex.quote(_cfg('MSWD2019', 'Microsoft Word'))}",
                _env(tmp_path, {"ASCENDO_MAU_SCOPED_INSTALL": "1"}, fake))
        assert r.returncode == 95
    assert not log.exists() or "--install" not in log.read_text()


def test_scoped_install_interruption_is_rc10(tmp_path: Path) -> None:
    fake, _, _ = _install_fixture(tmp_path, interrupt=True)
    r = _sh(f"msupdate_apply x {shlex.quote(_cfg('MSWD2019', 'Microsoft Word'))}",
            _env(tmp_path, {"ASCENDO_MAU_SCOPED_INSTALL": "1"}, fake))
    assert r.returncode == 10


def test_scoped_install_never_for_teams(tmp_path: Path) -> None:
    fake, log, _ = _install_fixture(tmp_path)
    r = _sh(f"msupdate_apply x {shlex.quote(_cfg('TEAMS21', 'Microsoft Teams'))}",
            _env(tmp_path, {"ASCENDO_MAU_SCOPED_INSTALL": "1"}, fake))
    assert r.returncode == 95
    assert not log.exists() or "--install" not in log.read_text()


# ---- Fix round 1 ------------------------------------------------------------
def _teams_phase(tmp: Path, pin: str):
    fake_app = _make_fake_app(tmp, "Microsoft Teams", "25306.805.4102.7211", bundle_id="com.microsoft.teams2")
    prefs = _prefs(tmp / "p.plist", {"OptionalUpdatesDeferrals":
                                     {"DeferralVersions": {"TEAMS21": pin}}})
    fake = _fake_msupdate(tmp / "bin", f"cat <<'EOF'\n{LIST_NONE}EOF\n")
    reg = tmp / "registry.toml"
    reg.write_text('schema = "ascendo-web-apps/v2"\n[[app]]\nslug = "ms-teams"\n'
                   'bundle_id = "com.microsoft.teams2"\ndisplay_name = "Microsoft Teams"\n'
                   f'app_path = "{fake_app}"\n'
                   'handler = "msupdate"\n[app.msupdate]\napp_id = "TEAMS21"\n')
    fake_home = tmp / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": f"{fake}:{os.environ['PATH']}", "ASCENDO_MAU_PREFS_FILE": str(prefs),
           "ASCENDO_MAU_MUTATE_PREFS": "1", "HOME": str(fake_home)}
    return reg, prefs, env


def test_pin_mutation_never_in_check_plan_or_dry_run_apply(tmp_path: Path) -> None:
    reg, prefs, env = _teams_phase(tmp_path, "25000.100")
    before = prefs.read_bytes()
    for phase, extra in (("check", None), ("plan", None), ("apply", ["--dry-run"])):
        out = tmp_path / f"out_{phase}"
        r, rid = _run_phase(phase, reg, out, env_extra=env, extra_args=extra)
        assert r.returncode == 0, r.stderr
        assert prefs.read_bytes() == before, phase
        assert not list(out.rglob("*pre-pin-release*")), phase


def test_pin_mutation_happens_in_real_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    reg, prefs, env = _teams_phase(tmp_path, "25000.100")
    env["HOME"] = str(fake_home)
    out = tmp_path / "out"
    r, rid = _run_phase("apply", reg, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    assert "TEAMS21" not in prefs.read_text() or b"25000.100" not in prefs.read_bytes()
    assert (out / rid / "com.microsoft.autoupdate2.pre-pin-release.plist").exists()


def test_standalone_check_does_not_mutate_pin(tmp_path: Path) -> None:
    env, prefs = _teams_env(tmp_path, "25000.100", {"ASCENDO_MAU_MUTATE_PREFS": "1"})
    del env["ASCENDO_MSUPDATE_LIST_FILE"]
    before = prefs.read_bytes()
    _check(tmp_path, "TEAMS21", "Microsoft Teams", env)
    assert prefs.read_bytes() == before


def test_build_only_update_is_planned_not_up_to_date(tmp_path: Path) -> None:
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_WORD}EOF\n")
    _make_fake_app(tmp_path, "Microsoft Word", "16.111.1", bundle_id="com.fixture.word")
    reg = tmp_path / "registry.toml"
    reg.write_text('schema = "ascendo-web-apps/v2"\n[[app]]\nslug = "ms-word"\n'
                   'bundle_id = "com.fixture.word"\ndisplay_name = "Microsoft Word"\n'
                   'handler = "msupdate"\n[app.msupdate]\napp_id = "MSWD2019"\n')
    env = {"PATH": f"{fake}:{os.environ['PATH']}"}
    for phase in ("check", "plan"):
        out = tmp_path / f"o_{phase}"
        r, rid = _run_phase(phase, reg, out, env_extra=env)
        items = _read_sidecar(out, rid, phase)["items"]
        assert items and items[0]["status"] == "planned", (phase, items)


def test_deferral_days_zero_is_not_a_quarantine(tmp_path: Path) -> None:
    _bundle(tmp_path, "Microsoft Word", "16.100.0")
    prefs = _prefs(tmp_path / "p.plist",
                   {"OptionalUpdatesDeferrals": {"DeferralDays": {"MSWD2019": 0}}})
    fake = _fake_msupdate(tmp_path / "bin", f"cat <<'EOF'\n{LIST_NONE}EOF\n")
    r = _check(tmp_path, "MSWD2019", "Microsoft Word",
               _env(tmp_path, {"ASCENDO_MAU_PREFS_FILE": str(prefs)}, fake))
    assert "__MAU" not in r.stdout
