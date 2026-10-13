"""Three-way feed classification (Spec E) in the web check / plan / apply phases.

Sparkle is the feed-backed handler wired in this task. Hermetic: the appcast is
served by a fake curl (ASCENDO_WEB_CURL_BIN), the "app" is a tmp bundle, the
sparkle apply path is replaced by an apply_cli_argv that only edits that tmp
bundle's Info.plist. Nothing touches /Applications or the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from .test_web_phase_check_plan import SCRIPTS, _make_fake_app, _read_sidecar, _run_phase

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE_ID = "com.example.feedcompare"
APPCAST_TMPL = (
    '<?xml version="1.0"?>'
    '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>'
    '<item><enclosure url="https://feed.test/App-{v}.zip" sparkle:shortVersionString="{v}" '
    'sparkle:version="1"/></item></channel></rss>'
)


def _setup(tmp_path: Path, installed: str, vendor: str, extra_reg: str = ""):
    app = _make_fake_app(tmp_path, "FeedApp", installed, bundle_id=BUNDLE_ID)
    appcast = tmp_path / "appcast.xml"
    appcast.write_text(APPCAST_TMPL.format(v=vendor))
    curl = tmp_path / "fakecurl"
    curl.write_text('#!/bin/sh\ncat "$ASCENDO_TEST_APPCAST"\n')
    curl.chmod(0o755)
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        f'[[app]]\nslug = "feedapp"\nbundle_id = "{BUNDLE_ID}"\n'
        f'display_name = "FeedApp"\nhandler = "sparkle"\napp_path = "{app}"\n'
        'appcast_url = "https://feed.test/appcast.xml"\n' + extra_reg,
    )
    env = {"ASCENDO_WEB_CURL_BIN": str(curl), "ASCENDO_TEST_APPCAST": str(appcast),
           "ASCENDO_WEB_HOST_OS_VERSION": "27.0", "ASCENDO_WEB_HOST_ARCH": "arm64"}
    return app, registry, env


def _messages(sc: dict) -> str:
    return " ".join(m.get("text", "") for m in sc.get("messages", []))


# ----------------------------------------------------------------- check.sh

@pytest.mark.parametrize("installed,vendor,status,target", [
    ("1.0.0", "1.1.0", "planned", "1.1.0"),
    ("1.1.0", "1.1.0", "up_to_date", "1.1.0"),
])
def test_check_sparkle_newer_and_equal(tmp_path: Path, installed, vendor, status, target) -> None:
    _, registry, env = _setup(tmp_path, installed, vendor)
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    item = _read_sidecar(out, run_id, "check")["items"][0]
    assert (item["status"], item["target_version"]) == (status, target)


def test_check_sparkle_older_feed_is_stale_skipped_never_up_to_date(tmp_path: Path) -> None:
    _, registry, env = _setup(tmp_path, "2.0.0", "1.1.0")
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "check")
    item = sc["items"][0]
    assert item["status"] == "skipped"
    assert item["current_version"] == "2.0.0"
    assert not item.get("target_version")          # no downgrade candidate leaks into inventory
    msg = _messages(sc)
    assert "feed_stale" in msg and "1.1.0" in msg and "2.0.0" in msg
    assert "feed_stale" in [m.get("code") for m in sc["messages"]]   # machine-readable code, not only text


def test_check_sparkle_unparseable_installed_is_unknown_skipped(tmp_path: Path) -> None:
    _, registry, env = _setup(tmp_path, "dev-build", "1.1.0")
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "check")
    item = sc["items"][0]
    assert item["status"] == "skipped" and not item.get("target_version")
    assert "unknown_version" in _messages(sc)
    assert "unknown_version" in [m.get("code") for m in sc["messages"]]


# ------------------------------------------------------------------ plan.sh

@pytest.mark.parametrize("installed,vendor,expect_planned", [
    ("1.0.0", "1.1.0", True),
    ("1.1.0", "1.1.0", False),
    ("2.0.0", "1.1.0", False),      # stale feed is never a plan
    ("dev-build", "1.1.0", False),  # unknown is never a plan
])
def test_plan_sparkle_only_plans_when_feed_is_newer(tmp_path: Path, installed, vendor, expect_planned) -> None:
    _, registry, env = _setup(tmp_path, installed, vendor)
    out = tmp_path / "out"
    r, run_id = _run_phase("plan", registry, out, env_extra=env)
    assert r.returncode == 0, r.stderr
    items = _read_sidecar(out, run_id, "plan")["items"]
    assert [i["status"] for i in items] == (["planned"] if expect_planned else [])


# ----------------------------------------------------------------- apply.sh

def _fake_lsappinfo(tmp: Path) -> Path:
    """Hermetic LaunchServices stand-in: a session with only the Finder running
    (state `false` for every app under test). The fail-closed probe must not
    depend on the host's real lsappinfo."""
    fake = tmp / "lsappinfo-fake"
    fake.write_text("#!/bin/sh\necho 'bundleID=\"com.apple.finder\"'\n")
    fake.chmod(0o755)
    return fake


def _run_apply(registry: Path, out: Path, env_extra: dict) -> tuple[subprocess.CompletedProcess, str]:
    run_id = str(uuid.uuid4())
    env = {**os.environ,
           "ASCENDO_LSAPPINFO_BIN": str(_fake_lsappinfo(out.parent)),
           "ASCENDO_WEB_REGISTRY_PATH": str(registry), "ASCENDO_WEB_USER_REGISTRY_PATH": "",
           "ASCENDO_SUDO_WARM_DISABLE": "1",
           "PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos", **env_extra}
    cmd = ["bash", str(SCRIPTS / "apply.sh"), "--run-id", run_id, "--trigger", "cli",
           "--profile", "full", "--output-dir", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env), run_id


def _apply_sidecar(out: Path, run_id: str) -> dict:
    return json.loads((out / run_id / "apply__web.json").read_text())


def _cli_that_bumps_version(app: Path, old: str, new: str) -> str:
    plist = app / "Contents" / "Info.plist"
    return (f'apply_cli_argv = ["/usr/bin/sed", "-i", "", "s/<string>{old}<\\\\/string>/<string>{new}<\\\\/string>/g", '
            f'"{plist}"]\n')


def test_apply_sparkle_older_feed_is_skipped_stale_and_installs_nothing(tmp_path: Path) -> None:
    app, registry, env = _setup(tmp_path, "2.0.0", "1.1.0", _cli_that_bumps_version(
        _make_fake_app(tmp_path, "FeedApp", "2.0.0", bundle_id=BUNDLE_ID), "2.0.0", "9.9.9"))
    out = tmp_path / "out"
    r, run_id = _run_apply(registry, out, env)
    assert r.returncode == 0, r.stderr
    sc = _apply_sidecar(out, run_id)
    item = sc["items"][0]
    assert item["status"] == "skipped" and not item.get("target_version")
    assert "feed_stale" in _messages(sc)
    assert "<string>2.0.0</string>" in (app / "Contents" / "Info.plist").read_text()  # untouched


def test_apply_sparkle_unknown_relation_is_skipped_not_up_to_date(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path, "FeedApp", "dev-build", bundle_id=BUNDLE_ID)
    _, registry, env = _setup(tmp_path, "dev-build", "1.1.0")
    out = tmp_path / "out"
    r, run_id = _run_apply(registry, out, env)
    assert r.returncode == 0, r.stderr
    sc = _apply_sidecar(out, run_id)
    assert sc["items"][0]["status"] == "skipped"
    assert "unknown_version" in _messages(sc)
    assert all(i["status"] != "up_to_date" for i in sc["items"])


def test_apply_sparkle_equal_is_up_to_date(tmp_path: Path) -> None:
    _, registry, env = _setup(tmp_path, "1.1.0", "1.1.0")
    out = tmp_path / "out"
    r, run_id = _run_apply(registry, out, env)
    assert r.returncode == 0, r.stderr
    assert _apply_sidecar(out, run_id)["items"][0]["status"] == "up_to_date"


def test_apply_sparkle_newer_proceeds_and_is_measured_after(tmp_path: Path) -> None:
    app = _make_fake_app(tmp_path, "FeedApp", "1.0.0", bundle_id=BUNDLE_ID)
    _, registry, env = _setup(tmp_path, "1.0.0", "1.1.0", _cli_that_bumps_version(app, "1.0.0", "1.1.0"))
    out = tmp_path / "out"
    r, run_id = _run_apply(registry, out, env)
    assert r.returncode == 0, r.stderr
    item = _apply_sidecar(out, run_id)["items"][0]
    assert item["status"] == "success"
    assert item["current_version"] == "1.1.0"
    assert "<string>1.1.0</string>" in (app / "Contents" / "Info.plist").read_text()
