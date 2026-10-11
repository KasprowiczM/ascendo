"""Tests for adapters/macos/lib/web_feed.py (bash<->vendor_feeds glue).

Covers Spec A (7-field vendor line, policy last), Spec D (Sparkle parser
parity + Ascendo's arm64/x86 filter kept as a separate wrapper) and the
fail-closed behaviour of the CLI used by ascendo_web.sh.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
FIX = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"
sys.path.insert(0, str(LIB))

import web_feed  # noqa: E402


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- arch filter

def test_arch_filter_arm_host_drops_x86_hardware_requirement():
    cand = web_feed.sparkle_candidate(_fx("sparkle_arch_mixed.xml"), is_arm=True)
    assert cand["version"] == "2.0.0"
    assert cand["url"].endswith("arm64.zip")


def test_arch_filter_intel_host_drops_arm_hardware_requirement():
    cand = web_feed.sparkle_candidate(_fx("sparkle_arch_mixed.xml"), is_arm=False)
    assert cand["version"] == "3.0.0"
    assert cand["url"].endswith("x86_64.zip")


def test_arch_filter_url_substrings_when_no_hardware_requirement():
    xml = _fx("sparkle_arch_url_only.xml")
    assert web_feed.sparkle_candidate(xml, is_arm=True)["version"] == "4.0.0"
    assert web_feed.sparkle_candidate(xml, is_arm=False)["version"] == "5.0.0"


def test_arch_filter_off_keeps_every_item():
    cand = web_feed.sparkle_candidate(_fx("sparkle_arch_mixed.xml"), arch_filter=False, is_arm=True)
    assert cand["version"] == "3.0.0"   # raw newest, arch ignored (vendor-feed path)


def test_arch_filter_leaves_unparseable_xml_to_the_parser():
    assert web_feed.filter_sparkle_arch("<broken", is_arm=True) == "<broken"
    assert web_feed.sparkle_candidate("<broken", is_arm=True) is None  # no regex guessing


def test_sparkle_candidate_uses_short_to_build_fallback_for_rdm():
    cand = web_feed.sparkle_candidate(_fx("rdm_build_only.xml"), is_arm=True)
    assert cand["version"] == "2026.3.0.5"


def test_sparkle_candidate_honours_minimum_system_version():
    xml = _fx("sparkle_min_os.xml")
    assert web_feed.sparkle_candidate(xml, os_version="14.4", is_arm=True)["version"] == "1.2.0"
    assert web_feed.sparkle_candidate(xml, os_version=None, is_arm=True)["version"] == "2.0.0"


def test_host_is_arm_env_override():
    assert web_feed.host_is_arm({"ASCENDO_WEB_HOST_ARCH": "arm64"}) is True
    assert web_feed.host_is_arm({"ASCENDO_WEB_HOST_ARCH": "x86_64"}) is False


# -------------------------------------------------------------- vendor lookup

CLAUDE_FEED = {"kind": "json", "url": "https://downloads.claude.ai/r.json", "arg": "claude_releases",
               "artifact": "zip", "allowed_host": "downloads.claude.ai"}


def test_vendor_line_has_seven_fields_policy_last():
    line = web_feed.vendor_lookup(CLAUDE_FEED, _fx("claude_releases.json"), "27.0")
    fields = line.split("|")
    assert len(fields) == 7
    assert fields[0] == "2.7032.0"
    assert fields[1].endswith(".zip")
    assert fields[4:] == ["zip", "downloads.claude.ai", "eligible"]


def test_vendor_line_policy_does_not_contaminate_artifact_host():
    cfg = {"kind": "json", "url": "https://proton.me/v.json", "arg": "proton_releases",
           "artifact": "dmg", "allowed_host": "proton.me"}
    body = json.dumps({"Releases": [{"CategoryName": "Stable", "Version": "1.5", "RolloutProportion": 0,
                                     "File": [{"Identifier": "Apple Disk Image", "Url": "https://proton.me/a.dmg",
                                               "Sha512CheckSum": "abc"}]}]})
    fields = web_feed.vendor_lookup(cfg, body, "27.0").split("|")
    assert fields == ["1.5", "https://proton.me/a.dmg", "sha512hex", "abc", "dmg", "proton.me", "rollout_hold"]


def test_vendor_line_dash_for_absent_fields():
    cfg = {"kind": "json", "url": "https://releases.warp.dev/c.json", "arg": "warp_channels",
           "artifact": "-", "allowed_host": "-"}
    fields = web_feed.vendor_lookup(cfg, _fx("warp_channels.json"), "27.0").split("|")
    assert fields == ["0.2026.09.16.08.27.02", "-", "-", "-", "-", "-", "eligible"]


def test_vendor_lookup_proton_min_os_uses_host_os():
    cfg = {"kind": "json", "url": "https://proton.me/v.json", "arg": "proton_releases",
           "artifact": "dmg", "allowed_host": "proton.me"}
    body = json.dumps({"Releases": [{"CategoryName": "Stable", "Version": "2.0", "MinimumOsVersion": "28.0"},
                                    {"CategoryName": "Stable", "Version": "1.5"}]})
    assert web_feed.vendor_lookup(cfg, body, "27.0").split("|")[0] == "1.5"
    assert web_feed.vendor_lookup(cfg, body, "28.0").split("|")[0] == "2.0"


def test_vendor_lookup_sparkle_kind_does_not_arch_filter():
    cfg = {"kind": "sparkle", "url": "https://x.test/a.xml", "arg": "-", "artifact": "zip", "allowed_host": "x.test"}
    assert web_feed.vendor_lookup(cfg, _fx("sparkle_arch_mixed.xml"), "27.0").split("|")[0] == "3.0.0"


def test_vendor_lookup_unparseable_or_hostile_is_none():
    assert web_feed.vendor_lookup(CLAUDE_FEED, "not json", "27.0") is None
    assert web_feed.vendor_lookup(CLAUDE_FEED, "", "27.0") is None
    evil = {"currentRelease": "1.0|x", "releases": []}
    assert web_feed.vendor_lookup(CLAUDE_FEED, json.dumps(evil), "27.0") is None  # '|' would shift fields


# ------------------------------------------------------------------------ CLI

def _cli(*args: str, stdin: str = "", env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {"PATH": os.environ["PATH"], **(env or {})}
    return subprocess.run([sys.executable, str(LIB / "web_feed.py"), *args], input=stdin,
                          capture_output=True, text=True, env=full_env)


def test_cli_sparkle_version_and_url_with_arch_override():
    r = _cli("sparkle", "--field", "version", stdin=_fx("sparkle_arch_mixed.xml"),
             env={"ASCENDO_WEB_HOST_ARCH": "x86_64"})
    assert (r.returncode, r.stdout.strip()) == (0, "3.0.0")
    r = _cli("sparkle", "--field", "url", stdin=_fx("sparkle_arch_mixed.xml"),
             env={"ASCENDO_WEB_HOST_ARCH": "arm64"})
    assert r.stdout.strip().endswith("App-2.0.0-arm64.zip")


def test_cli_sparkle_no_candidate_prints_nothing_rc0():
    r = _cli("sparkle", "--field", "version", stdin="<broken")
    assert (r.returncode, r.stdout) == (0, "")


def test_cli_vendor_line_and_failure_rc():
    r = _cli("vendor", "--os-version", "27.0", stdin=_fx("claude_releases.json"),
             env={"ASCENDO_WEB_VENDOR_FEED_JSON": json.dumps(CLAUDE_FEED)})
    assert r.returncode == 0 and r.stdout.strip().split("|")[0] == "2.7032.0"
    r = _cli("vendor", "--os-version", "27.0", stdin="garbage",
             env={"ASCENDO_WEB_VENDOR_FEED_JSON": json.dumps(CLAUDE_FEED)})
    assert r.returncode == 1 and r.stdout == ""
    r = _cli("vendor", stdin="x", env={"ASCENDO_WEB_VENDOR_FEED_JSON": "{not json"})
    assert r.returncode == 2


def test_cli_cmp_three_way_table():
    table = [("1.10", "1.2", "newer"), ("1.2", "1.2", "equal"), ("1.2", "1.10", "older"),
             ("abc", "1.2", "unknown"), ("1.2", "", "unknown"), ("", "", "unknown")]
    for cand, inst, want in table:
        r = _cli("cmp", cand, inst)
        assert r.stdout.strip() == want, (cand, inst, r.stdout, r.stderr)
