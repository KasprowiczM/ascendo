"""squirrel / docker vendor-truth handlers (Task 11, Spec F + G).

Ports macOS_updates tests/test_vendor_truth_handlers.py and test_docker_update.py
against ``squirrel_apply`` / ``docker_apply``. Everything is faked at the seams the
handlers use: the running probe (``ascendo_app_running_state``), the hidden launch
(``_web_open_hidden``), the sleep (``_web_sleep``), the installer
(``_web_vendor_install``) and the installed version (``_web_installed_version``).
The vendor feed comes from ASCENDO_WEB_VENDOR_FEED_DIR. Nothing touches /Applications,
the network, a real ``open`` / ``docker`` or the wall clock.

Action log vocabulary (one line per call, in order): ``launch``, ``wait``, ``direct``.
There is deliberately no ``quit`` seam: the handlers never quit an app.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"
FIX = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"

PYDIR = str(Path(sys.executable).parent)

CLAUDE_FEED = {"kind": "json", "url": "https://downloads.claude.ai/r.json", "arg": "claude_releases",
               "artifact": "zip", "allowed_host": "downloads.claude.ai", "direct_first": False}
CODEX_FEED = {"kind": "sparkle", "url": "https://persistent.oaistatic.com/appcast.xml", "arg": "-",
              "artifact": "zip", "allowed_host": "persistent.oaistatic.com", "direct_first": True}
PROTON_FEED = {"kind": "json", "url": "https://proton.me/download/mail/macos/version.json",
               "arg": "proton_releases", "artifact": "dmg", "allowed_host": "proton.me",
               "direct_first": True}
WARP_FEED = {"kind": "json", "url": "https://releases.warp.dev/channel_versions.json",
             "arg": "warp_channels", "artifact": "-", "allowed_host": "-", "direct_first": False}
RDM_FEED = {"kind": "kv", "url": "https://devolutions.net/productinfo.htm", "arg": "RDMMacbin",
            "artifact": "dmg", "allowed_host": "cdn.devolutions.net", "direct_first": False}
DOCKER_FEED = {"kind": "sparkle", "url": "https://desktop.docker.com/mac/main/arm64/appcast.xml", "arg": "-",
               "artifact": "dmg", "allowed_host": "desktop.docker.com", "direct_first": False}

CLAUDE_BODY = (FIX / "claude_releases.json").read_text()        # 2.7032.0, zip
WARP_BODY = (FIX / "warp_channels.json").read_text()
RDM_BODY = (FIX / "productinfo.htm").read_text()                 # RDMMacbin 2026.3.0.5, dmg
DOCKER_BODY = (FIX / "docker_attr.xml").read_text()              # newest 4.92.0, dmg
CODEX_BODY = (
    '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item>'
    '<enclosure url="https://persistent.oaistatic.com/update.zip" sparkle:shortVersionString="2.0"/>'
    '</item></channel></rss>')


def _proton_body(version: str = "2.0", rollout: float | None = None) -> str:
    rel = {"CategoryName": "Stable", "Version": version,
           "File": [{"Identifier": "Apple Disk Image", "Url": "https://proton.me/update.dmg",
                     "Sha512CheckSum": "abc"}]}
    if rollout is not None:
        rel["RolloutProportion"] = rollout
    return json.dumps({"Releases": [rel]})


class Harness:
    """One handler invocation in a hermetic bash with the seams stubbed."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.actions = tmp / "actions"
        self.marker = tmp / "installed-now"
        self.feeds = tmp / "feeds"
        self.feeds.mkdir(exist_ok=True)

    def run(self, handler: str, slug: str, feed: dict | None, body: str | None, *,
            installed: str = "1.0", newver: str = "2.0", state: str = "false",
            direct_rc: int = 0, open_rc: int = 0, open_updates: bool = False,
            bundle_id: str = "com.example.app", app_path: str | None = None,
            env: dict | None = None, extra_bash: str = "") -> dict:
        self.actions.unlink(missing_ok=True)
        self.marker.unlink(missing_ok=True)
        if body is not None:
            (self.feeds / f"{slug}.feed").write_text(body)
        cfg = {"slug": slug, "bundle_id": bundle_id, "handler": handler,
               "app_path": app_path or str(self.tmp / "Applications" / "Example.app"),
               "vendor_feed": feed}
        script = f'''
. "{LIB}/ascendo_web.sh"
. "{LIB}/handlers/squirrel.sh"
. "{LIB}/handlers/docker.sh"
_web_installed_version() {{ if [ -f "{self.marker}" ]; then cat "{self.marker}"; else echo "{installed}"; fi; }}
ascendo_app_running_state() {{ echo "{state}"; }}
_web_open_hidden() {{ echo launch >> "{self.actions}"; {f'echo "{newver}" > "{self.marker}";' if open_updates else ''} return {open_rc}; }}
_web_sleep() {{ echo wait >> "{self.actions}"; }}
_web_vendor_install() {{ echo direct >> "{self.actions}"; echo "ARGS $*" >> "{self.tmp}/direct-args"; if [ {direct_rc} -eq 0 ]; then echo "{newver}" > "{self.marker}"; fi; return {direct_rc}; }}
{extra_bash}
{handler}_apply "{slug}" "$ASCENDO_T_CFG"
rc=$?
echo "RC=$rc"
echo "OUTCOME=$ASCENDO_WEB_OUTCOME"
echo "MSG=$ASCENDO_WEB_OUTCOME_MSG"
'''
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("ASCENDO_", "MAC_UPDATE_", "FAKE_")) and k != "BUN_INSTALL"}
        e.update({"PATH": f"{PYDIR}:/usr/bin:/bin:/usr/sbin:/sbin",
                  "ASCENDO_WEB_VENDOR_FEED_DIR": str(self.feeds),
                  "ASCENDO_WEB_HOST_OS_VERSION": "27.0", "ASCENDO_WEB_HOST_ARCH": "arm64",
                  "ASCENDO_WEB_CACHE_DIR": str(self.tmp / "cache"),
                  "ASCENDO_T_CFG": json.dumps(cfg)})
        e.update(env or {})
        (self.tmp / "direct-args").unlink(missing_ok=True)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)
        out = {"proc": r}
        for line in r.stdout.splitlines():
            for key in ("RC", "OUTCOME", "MSG"):
                if line.startswith(key + "="):
                    out[key.lower()] = line.split("=", 1)[1]
        assert "rc" in out, (r.stdout, r.stderr)
        out["rc"] = int(out["rc"])
        out["actions"] = self.actions.read_text().split() if self.actions.exists() else []
        out["direct_args"] = (self.tmp / "direct-args").read_text() if (self.tmp / "direct-args").exists() else ""
        return out


def _h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ============================================================================
# squirrel: feed relation without any action
# ============================================================================

def test_vendor_truth_equal_does_not_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, installed="2.7032.0")
    assert r["rc"] == 0 and r["outcome"] == "current"
    assert r["actions"] == []


def test_vendor_truth_stale_feed_older_does_not_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, installed="3.0")
    assert r["rc"] == 0 and r["outcome"] == "feed_stale"
    assert "feed_stale" in r["msg"]
    assert r["actions"] == []


def test_vendor_truth_unknown_relation_does_not_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, installed="dev-build")
    assert r["rc"] == 0 and r["outcome"] == "unknown_version"
    assert r["actions"] == []


def test_vendor_truth_feed_unreachable_is_probe_unavailable_not_a_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, None)       # no feed file
    assert r["rc"] == 25 and r["outcome"] == "probe_unavailable"
    assert r["actions"] == []


# ============================================================================
# squirrel: newer feed
# ============================================================================

def test_chatgpt_running_has_no_side_effect(tmp_path: Path) -> None:
    """Running app (codex stands in for ChatGPT/Codex): nothing launched, nothing
    installed, nothing quit; needs_restart BEFORE any launch."""
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, state="true")
    assert r["rc"] == 4 and r["outcome"] == "needs_restart"
    assert r["actions"] == []


def test_unknown_running_state_is_never_idle(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, state="unknown")
    assert r["rc"] == 4 and r["outcome"] == "needs_restart"
    assert r["actions"] == []


def test_chatgpt_fast_direct_does_not_launch_or_wait(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY)
    assert r["actions"] == ["direct"]
    assert r["rc"] == 0 and r["outcome"] == "updated"


def test_direct_install_receives_feed_values_and_expected_version(tmp_path: Path) -> None:
    h = _h(tmp_path)
    r = h.run("squirrel", "codex", CODEX_FEED, CODEX_BODY)
    assert r["outcome"] == "updated"
    # _web_vendor_install <slug> <url> <artifact> <ck> <cs> <host> <expected_version> <installed_app>
    assert r["direct_args"].split() == [
        "ARGS", "codex", "https://persistent.oaistatic.com/update.zip", "zip", "-", "-",
        "persistent.oaistatic.com", "2.0", str(tmp_path / "Applications" / "Example.app")]


def test_direct_download_failure_retains_native_fallback(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, direct_rc=1)
    assert r["actions"] == ["direct", "launch"]
    assert r["rc"] == 0 and r["outcome"] == "triggered"
    assert "error_soft" in r["msg"] and "unverified" in r["msg"].lower()


def test_direct_download_failure_in_safe_mode_does_not_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, direct_rc=1,
                         env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == ["direct"]
    assert r["rc"] == 95 and r["outcome"] == "error_soft"
    assert "safe mode" in r["msg"].lower()


def test_direct_defer_is_soft_without_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, direct_rc=4)
    assert r["actions"] == ["direct"]
    assert r["rc"] == 4 and r["outcome"] == "needs_restart"


def test_direct_hard_failure_is_install_error_without_launch(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, direct_rc=3)
    assert r["actions"] == ["direct"]
    assert r["rc"] == 3 and r["outcome"] == "install_error"


def test_direct_install_that_leaves_old_version_reports_behind(tmp_path: Path) -> None:
    """rc 0 from the installer but the on-disk version is still behind: never `updated`."""
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY, newver="1.5")
    assert r["actions"] == ["direct"]
    assert r["rc"] == 95 and r["outcome"] == "behind"


def test_native_observed_update_finishes_without_sleep(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, open_updates=True,
                         newver="2.7032.0")
    assert r["actions"] == ["launch"]
    assert r["rc"] == 0 and r["outcome"] == "updated"


def test_native_launch_failure_has_no_wait_or_quit(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, open_rc=1)
    assert r["actions"] == ["launch"]          # no wait, no direct, no quit
    assert r["rc"] == 95 and r["outcome"] == "launch_failed"


def test_launch_cycle_polls_every_second_up_to_stage_wait(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY,
                         env={"ASCENDO_WEB_STAGE_WAIT": "3", "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["actions"] == ["launch", "wait", "wait", "wait"]
    # Ascendo launched it and nothing changed: launched (unverified), not behind.
    assert r["rc"] == 0 and r["outcome"] == "launched"


def test_launch_cycle_that_does_nothing_never_falls_through_to_direct(tmp_path: Path) -> None:
    """M1: after Ascendo's own hidden launch the app is running (because of us); a
    vendor-direct install would only defer with a misleading "quit the app". Report
    `launched` (apply => triggered) and say so; never `direct`, never `updated`."""
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, newver="2.7032.0",
                         env={"ASCENDO_WEB_STAGE_WAIT": "2"})
    assert r["actions"] == ["launch", "wait", "wait"]
    assert r["rc"] == 0 and r["outcome"] == "launched"
    assert "launched the app hidden" in r["msg"]


def test_launch_then_running_probe_is_not_consulted_again_for_a_direct_install(tmp_path: Path) -> None:
    """Same scenario with a running probe that flips to `true` once open was called
    (what a real lsappinfo does): still launched, and no rc 4 'quit the app' report."""
    flip = f'ascendo_app_running_state() {{ if [ -f "{tmp_path}/actions" ]; then echo true; else echo false; fi; }}'
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, newver="2.7032.0",
                         env={"ASCENDO_WEB_STAGE_WAIT": "1"}, extra_bash=flip)
    assert r["actions"] == ["launch", "wait"]
    assert r["rc"] == 0 and r["outcome"] == "launched"
    assert r["outcome"] != "needs_restart"


def test_stage_wait_zero_skips_the_launch_cycle(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, newver="2.7032.0",
                         env={"ASCENDO_WEB_STAGE_WAIT": "0"})
    assert r["actions"] == ["direct"]
    assert r["outcome"] == "updated"


def test_stage_wait_clamp_and_non_numeric_default(tmp_path: Path) -> None:
    def wait(value: str) -> str:
        r = subprocess.run(
            ["bash", "-c", f'. "{LIB}/ascendo_web.sh"; _web_stage_wait'],
            capture_output=True, text=True,
            env={"PATH": f"{PYDIR}:/usr/bin:/bin", "ASCENDO_WEB_STAGE_WAIT": value})
        return r.stdout.strip()

    assert wait("") == "90" and wait("abc") == "90" and wait("-5") == "90" and wait("1.5") == "90"
    assert wait("0") == "0" and wait("7") == "7" and wait("007") == "7"
    assert wait("600") == "600" and wait("601") == "600" and wait("99999999999999999999") == "600"
    r = subprocess.run(["bash", "-c", f'. "{LIB}/ascendo_web.sh"; _web_stage_wait'],
                       capture_output=True, text=True, env={"PATH": f"{PYDIR}:/usr/bin:/bin"})
    assert r.stdout.strip() == "90"


def test_safe_mode_never_launches_but_still_installs_idle_app_directly(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, newver="2.7032.0",
                         env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == ["direct"]            # no launch, no wait
    assert r["rc"] == 0 and r["outcome"] == "updated"


def test_safe_mode_without_artifact_reports_behind_with_reason(tmp_path: Path) -> None:
    """Warp is verify-only (artifact '-'): safe mode launches nothing, installs nothing."""
    r = _h(tmp_path).run("squirrel", "warp", WARP_FEED, WARP_BODY, installed="0.1",
                         env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == []
    assert r["rc"] == 95 and r["outcome"] == "behind"
    assert "safe mode" in r["msg"].lower()


def test_warp_verify_only_never_installs(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "warp", WARP_FEED, WARP_BODY, installed="0.1",
                         env={"ASCENDO_WEB_STAGE_WAIT": "1"})
    assert r["actions"] == ["launch", "wait"]
    assert r["rc"] == 0 and r["outcome"] == "launched"     # nothing to install; never `direct`


def test_vendor_direct_kill_switch_disables_only_the_direct_step(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "codex", CODEX_FEED, CODEX_BODY,
                         env={"ASCENDO_WEB_VENDOR_DIRECT": "0", "ASCENDO_WEB_STAGE_WAIT": "1"})
    # direct_first bypass is off, so the native cycle runs; no direct install.
    assert r["actions"] == ["launch", "wait"]
    assert r["rc"] == 0 and r["outcome"] == "launched"


def test_rdm_kv_feed_check_returns_the_RDMMacbin_version(tmp_path: Path) -> None:
    """M6: the shipped rdm row (kind=kv, arg=RDMMacbin) end to end through squirrel_check."""
    cfg = {"slug": "rdm", "handler": "squirrel", "vendor_feed": RDM_FEED}
    r = _check(tmp_path, "squirrel_check", cfg, RDM_BODY)
    assert r.returncode == 0 and r.stdout.strip() == "2026.3.0.5"


def test_rdm_kv_feed_apply_installs_the_devolutions_dmg_from_the_pinned_host(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "rdm", RDM_FEED, RDM_BODY, installed="2025.1.0", newver="2026.3.0.5",
                         bundle_id="com.devolutions.remotedesktopmanager",
                         env={"ASCENDO_SAFE_MODE": "true"})      # apply.sh's default: no launch
    assert r["actions"] == ["direct"]
    args = r["direct_args"].split()
    assert args[:4] == ["ARGS", "rdm",
                        "https://cdn.devolutions.net/download/Mac/Devolutions.RemoteDesktopManager.Mac.2026.3.0.5.dmg",
                        "dmg"]
    assert "cdn.devolutions.net" in args and "2026.3.0.5" in args
    assert r["rc"] == 0 and r["outcome"] == "updated"


def test_rdm_kv_feed_current_and_older_do_not_act(tmp_path: Path) -> None:
    cur = _h(tmp_path).run("squirrel", "rdm", RDM_FEED, RDM_BODY, installed="2026.3.0.5")
    assert cur["actions"] == [] and cur["outcome"] == "current"
    old = _h(tmp_path).run("squirrel", "rdm", RDM_FEED, RDM_BODY, installed="2027.1.0")
    assert old["actions"] == [] and old["outcome"] == "feed_stale"


def test_safe_mode_soft_download_failure_names_the_skipped_launch(tmp_path: Path) -> None:
    """M8: the error_soft message in safe mode says why the native updater was not tried,
    exactly like the behind / rollout_hold messages do."""
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY, direct_rc=1,
                         env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == ["direct"]
    assert r["rc"] == 95 and r["outcome"] == "error_soft"
    assert "safe mode: the app's own updater was not launched" in r["msg"]


def test_vendor_truth_behind_without_actions_reports_behind(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "claude", CLAUDE_FEED, CLAUDE_BODY,
                         env={"ASCENDO_WEB_STAGE_WAIT": "0", "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["actions"] == []
    assert r["rc"] == 95 and r["outcome"] == "behind"
    assert "1.0 < 2.7032.0" in r["msg"] or ("1.0" in r["msg"] and "2.7032.0" in r["msg"])


def test_proton_staged_rollout_uses_native_without_forcing_direct(tmp_path: Path) -> None:
    held = _proton_body("2.0", rollout=0.2)
    for _ in range(2):                       # policy is re-evaluated every run
        r = _h(tmp_path).run("squirrel", "proton-mail", PROTON_FEED, held,
                             env={"ASCENDO_WEB_STAGE_WAIT": "1"})
        assert r["actions"] == ["launch", "wait"]
        assert "direct" not in r["actions"]
        assert r["rc"] == 95 and r["outcome"] == "rollout_hold"
        assert "rollout_hold" in r["msg"]
    r = _h(tmp_path).run("squirrel", "proton-mail", PROTON_FEED, _proton_body("2.0"))
    assert r["actions"] == ["direct"] and r["outcome"] == "updated"


def test_proton_staged_rollout_in_safe_mode_does_nothing_but_report(tmp_path: Path) -> None:
    r = _h(tmp_path).run("squirrel", "proton-mail", PROTON_FEED, _proton_body("2.0", rollout=0.2),
                         env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == []
    assert r["rc"] == 95 and r["outcome"] == "rollout_hold"


def test_feedless_squirrel_is_unchanged_hidden_open(tmp_path: Path) -> None:
    """Without [app.vendor_feed] the legacy behaviour (blind `open -gjF`) stays."""
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "open.log"
    opener = fake / "open"
    opener.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
    opener.chmod(0o755)
    h = _h(tmp_path)
    r = h.run("squirrel", "slack", None, None, env={"PATH": f"{fake}:{PYDIR}:/usr/bin:/bin"},
              extra_bash='unset -f _web_open_hidden')
    assert r["rc"] == 0 and r["outcome"] == ""
    assert r["actions"] == []                      # the vendor-truth launch seam was not used
    assert log.read_text().split()[0] == "-gjF"


# ============================================================================
# squirrel_check / docker_check: probe = vendor feed version
# ============================================================================

def _check(tmp: Path, fn: str, cfg: dict, body: str | None, env: dict | None = None) -> subprocess.CompletedProcess:
    feeds = tmp / "feeds"
    feeds.mkdir(exist_ok=True)
    if body is not None:
        (feeds / f"{cfg['slug']}.feed").write_text(body)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    e.update({"PATH": f"{PYDIR}:/usr/bin:/bin:/usr/sbin:/sbin", "ASCENDO_WEB_VENDOR_FEED_DIR": str(feeds),
              "ASCENDO_WEB_HOST_OS_VERSION": "27.0", "ASCENDO_WEB_HOST_ARCH": "arm64",
              "ASCENDO_T_CFG": json.dumps(cfg)})
    e.update(env or {})
    script = (f'. "{LIB}/ascendo_web.sh"; . "{LIB}/handlers/squirrel.sh"; . "{LIB}/handlers/docker.sh"; '
              f'{fn} "{cfg["slug"]}" "$ASCENDO_T_CFG"')
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)


def test_squirrel_check_with_feed_echoes_vendor_version(tmp_path: Path) -> None:
    r = _check(tmp_path, "squirrel_check", {"slug": "claude", "handler": "squirrel",
                                            "vendor_feed": CLAUDE_FEED}, CLAUDE_BODY)
    assert r.returncode == 0 and r.stdout.strip() == "2.7032.0"


def test_squirrel_check_feedless_or_unreachable_is_empty(tmp_path: Path) -> None:
    r = _check(tmp_path, "squirrel_check", {"slug": "slack", "handler": "squirrel"}, None)
    assert r.returncode == 0 and r.stdout.strip() == ""
    r = _check(tmp_path, "squirrel_check", {"slug": "claude", "handler": "squirrel",
                                            "vendor_feed": CLAUDE_FEED}, "not json")
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_docker_check_is_the_vendor_feed_version_never_the_cli_plugin(tmp_path: Path) -> None:
    """P0 regression: `docker desktop version` printed the CLI plugin version (v0.4.4)."""
    fake = tmp_path / "bin"
    fake.mkdir()
    docker = fake / "docker"
    docker.write_text('#!/bin/sh\necho "Docker Desktop CLI plugin version: v0.4.4"\n')
    docker.chmod(0o755)
    cfg = {"slug": "docker", "handler": "docker", "vendor_feed": DOCKER_FEED}
    r = _check(tmp_path, "docker_check", cfg, DOCKER_BODY,
               env={"PATH": f"{fake}:{PYDIR}:/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip() == "4.92.0"


def test_docker_check_without_feed_is_empty_even_with_a_docker_cli(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    docker = fake / "docker"
    docker.write_text('#!/bin/sh\necho "Docker Desktop CLI plugin version: v0.4.4"\n')
    docker.chmod(0o755)
    r = _check(tmp_path, "docker_check", {"slug": "docker", "handler": "docker"}, None,
               env={"PATH": f"{fake}:{PYDIR}:/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip() == ""


# ============================================================================
# docker_apply
# ============================================================================

def _docker(tmp: Path, **kw) -> dict:
    kw.setdefault("bundle_id", "com.docker.docker")
    kw.setdefault("installed", "4.91.0")
    kw.setdefault("newver", "4.92.0")
    env = dict(kw.pop("env", {}))
    env.setdefault("ASCENDO_WEB_DOCKER_BIN", str(tmp / "no-docker"))   # no CLI by default
    return Harness(tmp).run("docker", "docker", DOCKER_FEED, DOCKER_BODY, env=env, **kw)


def test_docker_idle_behind_installs_verified_dmg_without_starting_engine(tmp_path: Path) -> None:
    r = _docker(tmp_path)
    assert r["actions"] == ["direct"]
    assert r["rc"] == 0 and r["outcome"] == "updated"
    args = r["direct_args"].split()
    assert args[:4] == ["ARGS", "docker", "https://desktop.docker.com/mac/main/arm64/4.92.0/Docker.dmg", "dmg"]
    assert args[-3] == "desktop.docker.com" and args[-2] == "4.92.0"


def test_docker_behind_running_is_left_alone(tmp_path: Path) -> None:
    r = _docker(tmp_path, state="true")
    assert r["actions"] == []
    assert r["rc"] == 4 and r["outcome"] == "needs_restart"


def test_docker_unknown_state_is_left_alone(tmp_path: Path) -> None:
    r = _docker(tmp_path, state="unknown")
    assert r["actions"] == [] and r["rc"] == 4 and r["outcome"] == "needs_restart"


def test_docker_engine_status_running_is_left_alone(tmp_path: Path) -> None:
    """`docker desktop status` rc 0 (engine up) => needs_restart even if lsappinfo says idle."""
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "docker.log"
    docker = fake / "docker"
    docker.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexit 0\n')
    docker.chmod(0o755)
    r = _docker(tmp_path, env={"ASCENDO_WEB_DOCKER_BIN": str(docker)})
    assert r["actions"] == [] and r["rc"] == 4 and r["outcome"] == "needs_restart"
    assert log.read_text().split() == ["desktop", "status"]        # only the read-only probe


def test_docker_installer_deferral_is_needs_restart(tmp_path: Path) -> None:
    r = _docker(tmp_path, direct_rc=4)
    assert r["actions"] == ["direct"] and r["rc"] == 4 and r["outcome"] == "needs_restart"


def _fake_docker(tmp: Path, *, update_rc: int = 0, start_brings_engine_up: bool = True,
                 update_installs: bool = True, marker: Path | None = None) -> tuple[Path, Path]:
    """A stateful fake `docker`: status reads a state file, start/stop toggle it, update logs.

    Every call is appended to docker.log as its argv; `desktop update` optionally
    prints a failure and exits update_rc, and (when it succeeds and update_installs)
    drops the 'pending' flag the version stub uses to flip to the new version.
    """
    fake = tmp / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp / "docker.log"
    state = tmp / "engine.up"
    pending = tmp / "update.pending"
    docker = fake / "docker"
    docker.write_text(f'''#!/bin/sh
echo "$*" >> "{log}"
if [ "$2" = "status" ]; then [ -f "{state}" ] && exit 0 || exit 1; fi
if [ "$2" = "start" ]; then {'touch "' + str(state) + '"' if start_brings_engine_up else ':'}; exit 0; fi
if [ "$2" = "stop" ]; then rm -f "{state}"; exit 0; fi
if [ "$2" = "update" ]; then
  if [ {update_rc} -ne 0 ]; then echo "install error: validating application: validating signature: <APP>: No such file or directory"; exit {update_rc}; fi
  {'touch "' + str(pending) + '"' if update_installs else ':'}
  exit 0
fi
exit 0
''')
    docker.chmod(0o755)
    return docker, log


def _version_stub(tmp: Path, *, installed: str = "4.91.0", new: str = "4.92.0", polls_before: int = 1) -> str:
    """_web_installed_version override: logs every read to events.log; reports the new
    version only after `docker desktop update` ran AND `polls_before` reads have passed."""
    return f'''
_web_installed_version() {{
  local v="{installed}" c=0
  if [ -f "{tmp}/update.pending" ]; then
    [ -f "{tmp}/pollcount" ] && c=$(cat "{tmp}/pollcount")
    c=$((c + 1)); echo "$c" > "{tmp}/pollcount"
    [ "$c" -gt {polls_before} ] && v="{new}"
  fi
  echo "app_version:$v" >> "{tmp}/events.log"
  echo "$v"
}}
'''


def test_docker_failed_download_in_safe_mode_is_soft_and_never_runs_the_cli(tmp_path: Path) -> None:
    docker, log = _fake_docker(tmp_path)
    r = _docker(tmp_path, direct_rc=1,
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == ["direct"]
    assert r["rc"] == 95 and r["outcome"] == "error_soft"
    assert "safe mode" in r["msg"]
    calls = [l.split()[1] for l in log.read_text().splitlines()]
    assert calls == ["status"]                         # only the read-only probe; no start/update/stop


def test_docker_failed_download_falls_back_to_the_vendor_cli(tmp_path: Path) -> None:
    """Spec G step 4: idle + feed newer + vendor DMG soft-failed + not safe mode =>
    start -> update -q -> poll the bundle version -> stop. Success only when measured."""
    docker, log = _fake_docker(tmp_path)
    r = _docker(tmp_path, direct_rc=1, extra_bash=_version_stub(tmp_path),
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker)})
    calls = [" ".join(l.split()[:2]) for l in log.read_text().splitlines()]
    assert "desktop start" in calls and "desktop update" in calls
    assert calls.index("desktop start") < calls.index("desktop update") < calls.index("desktop stop")
    assert r["rc"] == 0 and r["outcome"] == "updated"
    assert "docker desktop update" in r["msg"]


def test_docker_kill_switch_uses_the_vendor_cli_without_a_direct_install(tmp_path: Path) -> None:
    docker, log = _fake_docker(tmp_path)
    r = _docker(tmp_path, extra_bash=_version_stub(tmp_path),
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert "direct" not in r["actions"]
    assert r["rc"] == 0 and r["outcome"] == "updated"


def test_docker_cli_update_without_a_measured_version_is_not_success(tmp_path: Path) -> None:
    """`update -q` exits 0 but Docker.app never changes within the bound: behind, never success."""
    docker, log = _fake_docker(tmp_path, update_installs=False)
    r = _docker(tmp_path, extra_bash=_version_stub(tmp_path),
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_WEB_VENDOR_DIRECT": "0",
                     "ASCENDO_WEB_DOCKER_UPDATE_WAIT": "20"})
    assert r["rc"] == 95 and r["outcome"] == "behind"
    assert "did not change" in r["msg"]
    # bounded poll: 20 s / 10 s steps = 2 sleeps (+ the start-poll sleeps), never unbounded
    assert r["actions"].count("wait") <= 4
    assert "desktop stop" in log.read_text()


def test_docker_engine_that_never_starts_is_stopped_and_reported_behind(tmp_path: Path) -> None:
    docker, log = _fake_docker(tmp_path, start_brings_engine_up=False)
    r = _docker(tmp_path, env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["rc"] == 95 and r["outcome"] == "behind"
    assert "did not bring the engine up" in r["msg"]
    text = log.read_text()
    assert "desktop update" not in text and "desktop stop" in text
    assert r["actions"].count("wait") == 24            # 120 s / 5 s, bounded


def test_docker_failed_cli_update_does_not_poll(tmp_path: Path) -> None:
    """Port of upstream test_docker_failed_cli_update_does_not_poll: exit!=0 from
    `docker desktop update` means nothing will change: no version poll."""
    docker, log = _fake_docker(tmp_path, update_rc=1)
    r = _docker(tmp_path, extra_bash=_version_stub(tmp_path),
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["rc"] == 95 and r["outcome"] == "behind"
    assert "installed 4.91.0 < vendor 4.92.0" in r["msg"]
    assert "docker desktop update -q" in r["msg"] and "exit=1" in r["msg"]
    assert "validating signature" in r["msg"]
    events = (tmp_path / "events.log")
    # exactly one read (the initial installed version); no poll after the failed update
    assert events.read_text().strip().splitlines() == ["app_version:4.91.0"]
    assert r["actions"].count("wait") == 1                            # only the engine-start wait
    assert "desktop stop" in log.read_text()                          # our engine is stopped


def test_docker_stop_is_called_after_version_polling_loop(tmp_path: Path) -> None:
    """Port of upstream test_docker_stop_is_called_after_version_polling_loop: `docker
    desktop stop` is the LAST event, after the version polls reach the new version."""
    events = tmp_path / "events.log"
    docker, log = _fake_docker(tmp_path)
    # Make the fake docker also record its stop in the same events log.
    text = docker.read_text().replace('if [ "$2" = "stop" ]; then',
                                      f'if [ "$2" = "stop" ]; then echo docker_stop >> "{events}";')
    docker.write_text(text)
    r = _docker(tmp_path, extra_bash=_version_stub(tmp_path, polls_before=1),
                env={"ASCENDO_WEB_DOCKER_BIN": str(docker), "ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["rc"] == 0 and r["outcome"] == "updated"
    lines = events.read_text().strip().splitlines()
    assert lines[-1] == "docker_stop", lines
    assert "app_version:4.92.0" in lines[:-1]


def test_docker_without_a_cli_and_without_direct_reports_behind(tmp_path: Path) -> None:
    r = _docker(tmp_path, env={"ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["actions"] == [] and r["rc"] == 95 and r["outcome"] == "behind"
    assert "docker CLI not found" in r["msg"]


def test_docker_hard_install_failure_is_failed(tmp_path: Path) -> None:
    r = _docker(tmp_path, direct_rc=3)
    assert r["rc"] == 3 and r["outcome"] == "install_error"


def test_docker_feed_unreachable_never_opens_app(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    r = h.run("docker", "docker", DOCKER_FEED, None, bundle_id="com.docker.docker",
              env={"ASCENDO_WEB_DOCKER_BIN": str(tmp_path / "no-docker")})
    assert r["actions"] == [] and r["rc"] == 25 and r["outcome"] == "probe_unavailable"


def test_docker_without_vendor_feed_refuses_blind_update(tmp_path: Path) -> None:
    r = Harness(tmp_path).run("docker", "docker", None, None, bundle_id="com.docker.docker",
                              env={"ASCENDO_WEB_DOCKER_BIN": str(tmp_path / "no-docker")})
    assert r["actions"] == [] and r["rc"] == 25 and r["outcome"] == "probe_unavailable"


def test_docker_current_does_not_install(tmp_path: Path) -> None:
    r = _docker(tmp_path, installed="4.92.0")
    assert r["actions"] == [] and r["rc"] == 0 and r["outcome"] == "current"


def test_docker_vendor_direct_kill_switch(tmp_path: Path) -> None:
    r = _docker(tmp_path, env={"ASCENDO_WEB_VENDOR_DIRECT": "0"})
    assert r["actions"] == [] and r["rc"] == 95 and r["outcome"] == "behind"


def test_docker_install_in_safe_mode_is_allowed(tmp_path: Path) -> None:
    """Vendor-direct install of an IDLE app launches no GUI, so safe mode keeps it."""
    r = _docker(tmp_path, env={"ASCENDO_SAFE_MODE": "true"})
    assert r["actions"] == ["direct"] and r["outcome"] == "updated"


# ============================================================================
# _web_feed_backed
# ============================================================================

def _feed_backed(handler: str, cfg: dict | None) -> bool:
    arg = f"'{json.dumps(cfg)}'" if cfg is not None else ""
    r = subprocess.run(
        ["bash", "-c", f'. "{LIB}/ascendo_web.sh"; _web_feed_backed {handler} {arg} && echo y || echo n'],
        capture_output=True, text=True,
        env={"PATH": f"{PYDIR}:/usr/bin:/bin:/usr/sbin:/sbin", "ASCENDO_WEB_CACHE_DIR": "/nonexistent"})
    return r.stdout.strip() == "y"


def test_feed_backed_is_sparkle_always_squirrel_docker_only_with_a_feed_table() -> None:
    assert _feed_backed("sparkle", None)
    assert _feed_backed("sparkle", {"slug": "x"})
    assert _feed_backed("squirrel", {"slug": "x", "vendor_feed": CLAUDE_FEED})
    assert _feed_backed("docker", {"slug": "x", "vendor_feed": DOCKER_FEED})
    assert not _feed_backed("squirrel", {"slug": "x", "vendor_feed": None})
    assert not _feed_backed("squirrel", {"slug": "x"})
    assert not _feed_backed("squirrel", None)
    for handler in ("github_dmg", "release_feed", "keystone", "builtin", "msupdate", "omaha"):
        assert not _feed_backed(handler, {"slug": "x", "vendor_feed": CLAUDE_FEED})
