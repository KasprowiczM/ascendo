"""check / plan / apply / verify phase scripts with the chromium_updater handler.

End-to-end through the real phase scripts against fakes only: a registry with
Gemini (history-proven current) and Google Drive (needs the updater), fake
GoogleUpdater, no network, no app launch, no /Applications.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from .omaha_fakes import (REPO_ROOT, hermetic_env, history_text, make_app, make_exe,
                          response_line)

SCRIPTS = REPO_ROOT / "adapters" / "macos" / "scripts" / "web"
GOOGLE_LOG_REL = "Library/Application Support/Google/GoogleUpdater/updater.log"

REGISTRY = '''schema = "ascendo-web-apps/v2"

[[app]]
slug = "gemini"
bundle_id = "com.google.GeminiMacOS"
display_name = "Gemini"
handler = "chromium_updater"
app_path = "{apps}/Gemini.app"
[app.chromium_updater]
appid = "com.google.geminimacos"

[[app]]
slug = "gdrive"
bundle_id = "com.google.drivefs"
display_name = "Google Drive"
handler = "chromium_updater"
app_path = "{apps}/Google Drive.app"
[app.chromium_updater]
appid = "com.google.drivefs"
'''


class Phase:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.apps = tmp / "Applications"
        self.apps.mkdir()
        self.env = hermetic_env(tmp)
        home = Path(self.env["HOME"])
        self.user_log = home / GOOGLE_LOG_REL
        self.user_log.parent.mkdir(parents=True)
        self.user_log.write_text("")
        self.calls = tmp / "updater_calls.log"
        self.env.update({
            "ASCENDO_GOOGLE_USER_LOG": str(self.user_log),
            "ASCENDO_GOOGLE_SYS_LOG": str(tmp / "nosys.log"),
            "ASCENDO_GOOGLE_USER_UPDATER": "",
            "ASCENDO_GOOGLE_SYS_UPDATER": "",
            "ASCENDO_GOOGLE_LEGACY_AGENT": "",
            "ASCENDO_WEB_APPS_ROOT": str(self.apps),
            "ASCENDO_WEB_BREW_CASKS": "", "ASCENDO_WEB_MAS_BUNDLE_IDS": "",
            "ASCENDO_WEB_APPLE_BUNDLES": "",
            "ASCENDO_SUDO_WARM_DISABLE": "1",
            "ASCENDO_RUNS_DIR": str(tmp / "runs"),
            "ASCENDO_WEB_SEEN_FILE": str(tmp / "seen.tsv"),
            "ASCENDO_WEB_VERIFY_SLEEP_OVERRIDE": "0",
            "PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos",
        })
        for name in ("open", "osascript", "killall", "pkill"):
            make_exe(tmp / "fakebin" / name, f'echo "{name} $@" >> "{tmp}/forbidden.log"\n')
        self.registry = tmp / "registry.toml"
        self.registry.write_text(REGISTRY.format(apps=self.apps))
        self.env["ASCENDO_WEB_REGISTRY_PATH"] = str(self.registry)
        self.env["ASCENDO_WEB_USER_REGISTRY_PATH"] = ""
        self.out = tmp / "out"
        self.run_id = str(uuid.uuid4())
        self.gemini = make_app(self.apps, "Gemini", "1.119.2.914", bundle_id="com.google.GeminiMacOS")
        self.drive = make_app(self.apps, "Google Drive", "124.0", bundle_id="com.google.drivefs")

    def updater(self, body: str = "") -> None:
        path = self.tmp / "user_bin" / "GoogleUpdater"
        make_exe(path, f'echo "$@" >> "{self.calls}"\n{body}\nexit 0\n')
        self.env["ASCENDO_GOOGLE_USER_UPDATER"] = str(path)

    def history(self, appid: str, version: str) -> None:
        (self.user_log.parent / "updater_history.jsonl").write_text(
            history_text([(appid, "T1", "NO_UPDATE", version)], time.time() - 600))

    def phase(self, name: str, *extra: str, env: dict | None = None):
        e = {**self.env, **(env or {})}
        r = subprocess.run(["bash", str(SCRIPTS / f"{name}.sh"), "--run-id", self.run_id,
                            "--trigger", "cli", "--profile", "full", "--output-dir", str(self.out), *extra],
                           capture_output=True, text=True, env=e, timeout=120)
        assert r.returncode == 0, (r.stdout, r.stderr)
        return json.loads((self.out / self.run_id / f"{name}__web.json").read_text())

    def calls_text(self) -> str:
        return self.calls.read_text() if self.calls.exists() else ""

    def forbidden(self) -> str:
        f = self.tmp / "forbidden.log"
        return f.read_text() if f.exists() else ""


@pytest.fixture
def ph(tmp_path: Path) -> Phase:
    return Phase(tmp_path)


def items(sc: dict) -> dict:
    return {i["id"]: i for i in sc["items"]}


def msgs(sc: dict) -> str:
    return "\n".join(m.get("text", "") for m in sc.get("messages", []))


def codes(sc: dict) -> set:
    return {m.get("code") for m in sc.get("messages", []) if m.get("code")}


def test_check_proof_is_up_to_date_with_code_and_no_proof_is_vendor_opaque(ph: Phase) -> None:
    ph.history("com.google.geminimacos", "1.119.2.914")
    ph.updater()
    sc = ph.phase("check")
    it = items(sc)
    assert it["web:gemini"]["status"] == "up_to_date"
    assert it["web:gdrive"]["status"] == "skipped"
    assert "current_vendor" in codes(sc)
    assert "gemini: verified by updater at " in msgs(sc)
    assert "gdrive: vendor_opaque" in msgs(sc)
    assert ph.calls_text() == "", "check must never wake the updater"
    assert ph.forbidden() == ""


def test_plan_drops_proven_current_and_plans_the_rest(ph: Phase) -> None:
    ph.history("com.google.geminimacos", "1.119.2.914")
    sc = ph.phase("plan")
    it = items(sc)
    assert "web:gemini" not in it
    assert it["web:gdrive"]["status"] == "planned"
    assert not it["web:gdrive"].get("target_version")


def test_apply_proven_current_up_to_date_without_wake(ph: Phase) -> None:
    ph.history("com.google.geminimacos", "1.119.2.914")
    ph.updater()
    sc = ph.phase("apply", "--filter", "gemini")
    assert items(sc)["web:gemini"]["status"] == "up_to_date"
    assert "current_vendor" in codes(sc)
    assert ph.calls_text() == ""


def test_apply_wakes_once_for_both_and_reports_vendor_verdicts(ph: Phase) -> None:
    ph.env["ASCENDO_OMAHA_WAIT"] = "2"
    ph.updater(f"cat >> '{ph.user_log}' <<'EOF'\n"
               f"{response_line('com.google.drivefs')}{response_line('com.google.geminimacos')}EOF")
    sc = ph.phase("apply")
    it = items(sc)
    assert it["web:gemini"]["status"] == "up_to_date"
    assert it["web:gdrive"]["status"] == "up_to_date"
    assert len(ph.calls_text().splitlines()) == 1
    assert "current_vendor" in codes(sc)
    assert ph.forbidden() == ""


def test_apply_update_in_progress_is_triggered_unverified(ph: Phase) -> None:
    ph.updater(f"cat >> '{ph.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF")
    sc = ph.phase("apply", "--filter", "gdrive")
    it = items(sc)["web:gdrive"]
    assert it["status"] == "triggered"
    assert "unverified" in codes(sc)
    assert "action_required" not in codes(sc)


def test_apply_update_installed_is_success_with_measured_version(ph: Phase) -> None:
    plist = ph.drive / "Contents" / "Info.plist"
    ph.env["ASCENDO_OMAHA_VERIFY_WAIT"] = "2"
    ph.env["ASCENDO_OMAHA_VERIFY_STEP"] = "1"
    ph.updater(f"cat >> '{ph.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF\n"
               f"sed -i '' 's/124.0/125.0.0.0/' '{plist}'")
    sc = ph.phase("apply", "--filter", "gdrive")
    it = items(sc)["web:gdrive"]
    assert it["status"] == "success" and it["current_version"] == "125.0.0.0"
    assert "updated" in codes(sc)


def test_apply_wake_failure_is_skipped_error_soft_never_failed(ph: Phase) -> None:
    sc = ph.phase("apply", "--filter", "gdrive")      # no updater anywhere
    it = items(sc)["web:gdrive"]
    assert it["status"] == "skipped"
    assert "error_soft" in codes(sc)
    assert "failed" not in {i["status"] for i in sc["items"]}


def test_apply_dry_run_never_wakes(ph: Phase) -> None:
    ph.updater()
    sc = ph.phase("apply", "--dry-run")
    assert {i["status"] for i in sc["items"]} == {"planned"}
    assert ph.calls_text() == ""


def test_verify_exempts_chromium_updater_from_unchanged_is_failed(ph: Phase) -> None:
    ph.updater(f"cat >> '{ph.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF")
    apply_sc = ph.phase("apply", "--filter", "gdrive")
    assert items(apply_sc)["web:gdrive"]["status"] == "triggered"
    # unchanged for 60 days (> the 45-day stale bound) must STILL not be failed
    now = int(time.time())
    Path(ph.env["ASCENDO_WEB_SEEN_FILE"]).write_text(f"gdrive\t124.0\t{now - 60 * 86400}\n")
    sc = ph.phase("verify", env={"ASCENDO_WEB_NOW_EPOCH": str(now)})
    it = items(sc)["web:gdrive"]
    assert it["status"] == "triggered"
    assert "unverified" in codes(sc)
    assert "failed" not in {i["status"] for i in sc["items"]}


def test_verify_changed_version_is_success(ph: Phase) -> None:
    ph.updater(f"cat >> '{ph.user_log}' <<'EOF'\n{response_line('com.google.drivefs', 'ok')}EOF")
    ph.phase("apply", "--filter", "gdrive")
    from .omaha_fakes import set_app_version
    set_app_version(ph.drive, "125.0.0.0", None, "com.google.drivefs")
    sc = ph.phase("verify")
    it = items(sc)["web:gdrive"]
    assert it["status"] == "success" and it["current_version"] == "125.0.0.0"
    assert "updated" in codes(sc)
