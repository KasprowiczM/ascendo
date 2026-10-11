#!/usr/bin/env python3
"""Port of macOS_updates tests/test_vendor_feeds.py (v1.5.4) plus Ascendo additions.

The vendor_feeds module is a byte-identical copy of upstream lib/python/vendor_feeds.py.
"""

import json
import os
import sys
import unittest

# adapters/macos/lib holds vendor_feeds.py (Ruling 20: same dir as cli_duplicates.py etc.)
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADAPTER_ROOT = os.path.dirname(TESTS_DIR)
LIB_PYTHON = os.path.join(ADAPTER_ROOT, "lib")
if LIB_PYTHON not in sys.path:
    sys.path.insert(0, LIB_PYTHON)

from vendor_feeds import (
    JSON_SELECTORS,
    evaluate_feed,
    normalize_vendor_version,
    omaha_last_status,
    parse_devolutions_productinfo,
    parse_electron_yml,
    parse_sparkle_appcast,
    select_antigravity_ide,
    select_claude_releases,
    select_cursor_update,
    select_proton_releases,
    select_tauri_latest,
    select_warp_channels,
    version_compare,
    version_history_public,
    version_key,
)


class TestVendorFeeds(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = os.path.join(TESTS_DIR, "fixtures", "vendor_feeds")

    def _read_fixture(self, filename: str) -> str:
        with open(os.path.join(self.fixtures_dir, filename), "r", encoding="utf-8") as f:
            return f.read()

    def test_normalize_warp_stable_suffix(self):
        self.assertEqual(
            normalize_vendor_version("v0.2026.09.16.08.27.stable_02"),
            "0.2026.09.16.08.27.02",
        )

    def test_normalize_cask_build_suffix(self):
        self.assertEqual(
            normalize_vendor_version("2.7032.0,6c468ab6ed862a68c9555cce34f11186c35f526d"),
            "2.7032.0",
        )

    def test_normalize_parenthesised_build(self):
        self.assertEqual(
            normalize_vendor_version("7.2.1 (88329)"),
            "7.2.1",
        )

    def test_version_compare_three_way(self):
        self.assertEqual(version_compare("1.2", "1.10"), -1)
        self.assertEqual(version_compare("1.2", "1.2"), 0)
        self.assertEqual(version_compare("1.10", "1.2"), 1)
        self.assertIsNone(version_compare("abc", "1.2"))
        self.assertIsNone(version_compare("1.2", "xyz"))

    def test_sparkle_picks_max_not_first(self):
        xml_text = self._read_fixture("rdm_ascending.xml")
        res = parse_sparkle_appcast(xml_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2023.1.12.0")

    def test_sparkle_attribute_form(self):
        xml_text = self._read_fixture("docker_attr.xml")
        res = parse_sparkle_appcast(xml_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "4.92.0")

    def test_sparkle_element_form_and_url(self):
        xml_text = self._read_fixture("chatgpt_elements.xml")
        res = parse_sparkle_appcast(xml_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "26.917.62051")
        self.assertTrue(res["url"].endswith(".zip"))

    def test_sparkle_filters_beta_channel(self):
        xml_text = self._read_fixture("protonvpn_channels.xml")
        # default channels allows None or stable
        res = parse_sparkle_appcast(xml_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "6.5.1")

    def test_sparkle_respects_minimum_system_version(self):
        xml_text = self._read_fixture("chatgpt_elements.xml")
        # When os_version is "12.0" but item requires "13.0", item is skipped
        res = parse_sparkle_appcast(xml_text, os_version="12.0")
        self.assertIsNone(res)

    def test_sparkle_parse_error_returns_none(self):
        res = parse_sparkle_appcast("<invalid xml")
        self.assertIsNone(res)

    def test_select_claude_releases(self):
        data = json.loads(self._read_fixture("claude_releases.json"))
        res = select_claude_releases(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2.7032.0")
        self.assertTrue(res["url"].endswith(".zip"))

    def test_select_cursor_update(self):
        data = json.loads(self._read_fixture("cursor_update.json"))
        res = select_cursor_update(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "3.21.18")
        self.assertTrue(res["url"].endswith(".zip"))

    def test_select_warp_channels(self):
        data = json.loads(self._read_fixture("warp_channels.json"))
        res = select_warp_channels(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "0.2026.09.16.08.27.02")
        self.assertIsNone(res["url"])

    def test_select_antigravity_ide_version_from_url(self):
        data = json.loads(self._read_fixture("antigravity_ide.json"))
        res = select_antigravity_ide(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2.5.5")
        self.assertEqual(res["checksum_kind"], "sha256hex")
        self.assertEqual(res["checksum"], "33338ced")

    def test_select_tauri_latest(self):
        data = json.loads(self._read_fixture("tauri_latest.json"))
        res = select_tauri_latest(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "1.18.32")
        self.assertTrue(res["url"].endswith(".tar.gz"))

    def test_select_proton_releases_ignores_alpha(self):
        data = json.loads(self._read_fixture("proton_version.json"))
        res = select_proton_releases(data)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "1.14.0")
        self.assertEqual(res["checksum_kind"], "sha512hex")

    def test_proton_drive_file_object_has_verified_download(self):
        result = select_proton_releases({"Releases": [{
            "CategoryName": "Stable", "Version": "3.0.3",
            "File": {"Url": "https://proton.me/drive.dmg", "Sha512CheckSum": "abc"},
        }]})
        self.assertEqual(result["url"], "https://proton.me/drive.dmg")
        self.assertEqual(result["checksum_kind"], "sha512hex")
        self.assertEqual(result["checksum"], "abc")

    def test_proton_respects_minimum_os_and_preserves_rollout(self):
        body = json.dumps({"Releases": [
            {"CategoryName": "Stable", "Version": "2.0", "MinimumOsVersion": "28.0"},
            {"CategoryName": "Stable", "Version": "1.5", "RolloutProportion": 0},
        ]})
        result = evaluate_feed("json", body, "proton_releases", os_version="27.0.1")
        self.assertEqual(result["version"], "1.5")
        self.assertTrue(result["rollout_hold"])

    def test_parse_electron_yml_zip_and_sha512(self):
        text = self._read_fixture("antigravity.yml")
        res = parse_electron_yml(text)
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2.16.0")
        self.assertTrue(res["url"].endswith(".zip"))
        self.assertEqual(res["checksum_kind"], "sha512b64")
        self.assertTrue(len(res["checksum"]) > 20)

    def test_parse_devolutions_productinfo(self):
        text = self._read_fixture("productinfo.htm")
        res = parse_devolutions_productinfo(text, "RDMMacbin")
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2026.3.0.5")
        self.assertTrue(res["url"].endswith(".dmg"))
        self.assertEqual(res["checksum_kind"], "sha256hex")
        self.assertEqual(res["checksum"], "945B1D1F")

    def test_omaha_ignores_request_lines(self):
        req_line = '[1:2:0923/094646.062632:VERBOSE2:components/update_client/request_sender.cc:119] Sending Omaha request: {"request":{"apps":[{"appid":"com.google.chrome","updatecheck":{}}]}}'
        self.assertIsNone(omaha_last_status(req_line, "com.google.chrome"))

    def test_omaha_last_response_wins(self):
        log_text = self._read_fixture("googleupdater.log")
        status = omaha_last_status(log_text, "com.google.chrome")
        self.assertEqual(status, "noupdate")
        gemini_status = omaha_last_status(log_text, "com.google.geminimacos")
        self.assertEqual(gemini_status, "noupdate")

    def test_omaha_unknown_appid_none(self):
        log_text = self._read_fixture("googleupdater.log")
        self.assertIsNone(omaha_last_status(log_text, "com.google.nonexistent"))

    def test_version_history_public_prefers_full_rollout(self):
        data = json.loads(self._read_fixture("versionhistory.json"))
        ver = version_history_public(data)
        self.assertEqual(ver, "154.0.8037.58")


class TestVendorFeedsAscendoParity(unittest.TestCase):
    """Ascendo-specific cases on top of the upstream port (Spec A/D/E)."""

    def setUp(self):
        self.fixtures_dir = os.path.join(TESTS_DIR, "fixtures", "vendor_feeds")

    def _read_fixture(self, filename: str) -> str:
        with open(os.path.join(self.fixtures_dir, filename), "r", encoding="utf-8") as f:
            return f.read()

    def test_rdm_build_only_items_resolve_to_real_newest_version(self):
        # Newest RDM items carry only sparkle:version (no shortVersionString):
        # the short->build fallback must pick 2026.3.0.5, not 2023.1.12.0.
        res = evaluate_feed("sparkle", self._read_fixture("rdm_build_only.xml"), "-")
        self.assertIsNotNone(res)
        self.assertEqual(res["version"], "2026.3.0.5")
        self.assertIn("%20", res["url"])  # spaces in enclosure url are encoded

    def test_prerelease_ordering_dev_alpha_beta_rc_final(self):
        order = ["1.0-dev1", "1.0-alpha1", "1.0-beta1", "1.0-rc1", "1.0"]
        for lower, higher in zip(order, order[1:]):
            self.assertEqual(version_compare(lower, higher), -1, f"{lower} < {higher}")
            self.assertEqual(version_compare(higher, lower), 1, f"{higher} > {lower}")

    def test_final_beats_beta_when_picking_max_item(self):
        xml_text = (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>'
            '<item><enclosure url="https://e.test/b.zip" sparkle:shortVersionString="3.0b1"/></item>'
            '<item><enclosure url="https://e.test/f.zip" sparkle:shortVersionString="3.0"/></item>'
            '</channel></rss>'
        )
        self.assertEqual(parse_sparkle_appcast(xml_text)["version"], "3.0")

    def test_channel_filter_allows_configured_channel_only_when_asked(self):
        body = self._read_fixture("protonvpn_channels.xml")
        self.assertEqual(evaluate_feed("sparkle", body, "-")["version"], "6.5.1")
        self.assertEqual(evaluate_feed("sparkle", body, "beta")["version"], "6.5.2")

    def test_minimum_system_version_filters_with_os_version(self):
        body = self._read_fixture("sparkle_min_os.xml")
        self.assertEqual(evaluate_feed("sparkle", body, "-", os_version="14.4")["version"], "1.2.0")
        self.assertEqual(evaluate_feed("sparkle", body, "-")["version"], "2.0.0")

    def test_stale_feed_compares_older_than_installed(self):
        res = evaluate_feed("sparkle", self._read_fixture("docker_attr.xml"), "-")
        self.assertEqual(version_compare(res["version"], "4.93.0"), -1)

    def test_unparseable_inputs_are_none_never_a_guess(self):
        self.assertIsNone(version_compare("garbage", "1.0"))
        self.assertIsNone(evaluate_feed("sparkle", "<broken", "-"))
        self.assertIsNone(evaluate_feed("json", "{not json", "claude_releases"))
        self.assertIsNone(evaluate_feed("json", "{}", "no_such_selector"))

    def test_evaluate_feed_threads_os_version_into_proton_releases(self):
        body = json.dumps({"Releases": [
            {"CategoryName": "Stable", "Version": "2.0", "MinimumOsVersion": "28.0"},
            {"CategoryName": "Stable", "Version": "1.5"},
        ]})
        self.assertEqual(evaluate_feed("json", body, "proton_releases", os_version="27.0.1")["version"], "1.5")
        self.assertEqual(evaluate_feed("json", body, "proton_releases", os_version="28.1")["version"], "2.0")
        self.assertFalse(evaluate_feed("json", body, "proton_releases", os_version="28.1").get("rollout_hold"))

    def test_proton_dmg_url_fallback_without_identifier(self):
        result = select_proton_releases({"Releases": [{
            "CategoryName": "Stable", "Version": "3.1",
            "File": [{"Url": "https://proton.me/x/Drive.dmg?cache=1", "Sha512CheckSum": "ff"}],
        }]})
        self.assertEqual(result["url"], "https://proton.me/x/Drive.dmg?cache=1")
        self.assertFalse(result["rollout_hold"])

    def test_kv_and_yml_and_json_selectors_via_evaluate_feed(self):
        kv = evaluate_feed("kv", self._read_fixture("productinfo.htm"), "RDMMacbin")
        self.assertEqual(kv["version"], "2026.3.0.5")
        yml = evaluate_feed("yml", self._read_fixture("antigravity.yml"), "-")
        self.assertEqual(yml["checksum_kind"], "sha512b64")
        tauri = evaluate_feed("json", self._read_fixture("tauri_latest.json"), "tauri_latest")
        self.assertTrue(tauri["url"].endswith(".tar.gz"))
        warp = evaluate_feed("json", self._read_fixture("warp_channels.json"), "warp_channels")
        self.assertEqual(warp["version"], "0.2026.09.16.08.27.02")
        cursor = evaluate_feed("json", self._read_fixture("cursor_update.json"), "cursor_update")
        self.assertEqual(cursor["version"], "3.21.18")


if __name__ == "__main__":
    unittest.main()
