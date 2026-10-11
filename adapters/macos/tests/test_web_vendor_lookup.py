"""ascendo_web.sh vendor-truth helpers (Spec A/D/E): lookup, fetch, three-way compare.

Hermetic: the feed comes from ASCENDO_WEB_VENDOR_FEED_DIR (or a fake curl via
ASCENDO_WEB_CURL_BIN), the host OS / arch are forced by env, nothing touches the
network or /Applications.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"
FIX = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"


def _env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    env.update({"ASCENDO_WEB_HOST_OS_VERSION": "27.0", "ASCENDO_WEB_HOST_ARCH": "arm64",
                "ASCENDO_WEB_CACHE_DIR": "/nonexistent-ascendo-test-cache"})
    env.update(extra or {})
    return env


def _bash(snippet: str, extra: dict | None = None) -> subprocess.CompletedProcess:
    script = f"source {LIB}/ascendo_web.sh\n{textwrap.dedent(snippet)}"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=_env(extra))


def _feed_dir(tmp_path: Path, slug: str, body: str) -> Path:
    d = tmp_path / "feeds"
    d.mkdir(exist_ok=True)
    (d / f"{slug}.feed").write_text(body, encoding="utf-8")
    return d


def _cfg(slug: str, feed: dict) -> str:
    return json.dumps({"slug": slug, "handler": "squirrel", "vendor_feed": feed})


CLAUDE = {"kind": "json", "url": "https://downloads.claude.ai/r.json", "arg": "claude_releases",
          "artifact": "zip", "allowed_host": "downloads.claude.ai", "direct_first": False}
PROTON = {"kind": "json", "url": "https://proton.me/v.json", "arg": "proton_releases",
          "artifact": "dmg", "allowed_host": "proton.me", "direct_first": False}


# ------------------------------------------------------------ _web_version_cmp

def test_version_cmp_three_way_table() -> None:
    cases = [("1.10", "1.2", "newer"), ("1.2", "1.2", "equal"), ("1.2", "1.10", "older"),
             ("125.0.0.0", "125.0", "equal"), ("2026.3.0.5", "2023.1.12.0", "newer"),
             ("6.5.1", "6.5.1-beta2", "newer"), ("abc", "1.2", "unknown"), ("1.2", "xyz", "unknown"),
             ("", "1.2", "unknown"), ("1.2", "", "unknown")]
    for cand, inst, want in cases:
        r = _bash(f'_web_version_cmp "{cand}" "{inst}"')
        assert (r.returncode, r.stdout.strip()) == (0, want), (cand, inst, r.stdout, r.stderr)


def test_version_cmp_never_falls_back_to_string_compare() -> None:
    # '2.0-final' vs 'zzz' used to be decided by plain string order.
    r = _bash('_web_version_cmp "zzz" "1.0"')
    assert r.stdout.strip() == "unknown"


# ------------------------------------------------------------ host helpers

def test_host_os_version_env_override_and_default() -> None:
    assert _bash("_web_host_os_version").stdout.strip() == "27.0"
    r = _bash("_web_host_os_version", {"ASCENDO_WEB_HOST_OS_VERSION": ""})
    assert r.returncode == 0   # falls back to sw_vers (or empty), never fails


# ------------------------------------------------------------ vendor lookup

def test_vendor_lookup_hook_reads_slug_feed_file(tmp_path: Path) -> None:
    d = _feed_dir(tmp_path, "claude", (FIX / "claude_releases.json").read_text())
    r = _bash(f"_web_vendor_lookup claude '{_cfg('claude', CLAUDE)}'", {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    assert r.returncode == 0, r.stderr
    fields = r.stdout.strip().split("|")
    assert len(fields) == 7
    assert fields[0] == "2.7032.0" and fields[1].endswith(".zip")
    assert fields[4:] == ["zip", "downloads.claude.ai", "eligible"]


def test_vendor_lookup_policy_does_not_contaminate_artifact_host(tmp_path: Path) -> None:
    body = json.dumps({"Releases": [{"CategoryName": "Stable", "Version": "1.5", "RolloutProportion": 0.2,
                                     "File": [{"Identifier": "Apple Disk Image",
                                               "Url": "https://proton.me/a.dmg", "Sha512CheckSum": "abc"}]}]})
    d = _feed_dir(tmp_path, "proton-mail", body)
    r = _bash(f"_web_vendor_lookup proton-mail '{_cfg('proton-mail', PROTON)}'",
              {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    assert r.stdout.strip() == "1.5|https://proton.me/a.dmg|sha512hex|abc|dmg|proton.me|rollout_hold"


def test_proton_respects_minimum_os_and_preserves_rollout(tmp_path: Path) -> None:
    body = json.dumps({"Releases": [
        {"CategoryName": "Stable", "Version": "2.0", "MinimumOsVersion": "28.0"},
        {"CategoryName": "Stable", "Version": "1.5", "RolloutProportion": 0},
    ]})
    d = _feed_dir(tmp_path, "proton-mail", body)
    cfg = _cfg("proton-mail", PROTON)
    r = _bash(f"_web_vendor_lookup proton-mail '{cfg}'", {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    f = r.stdout.strip().split("|")
    assert (f[0], f[6]) == ("1.5", "rollout_hold")          # host OS 27.0 < min 28.0
    r = _bash(f"_web_vendor_lookup proton-mail '{cfg}'",
              {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d), "ASCENDO_WEB_HOST_OS_VERSION": "28.2"})
    assert r.stdout.strip().split("|")[0] == "2.0"


def test_proton_drive_file_object_has_verified_download(tmp_path: Path) -> None:
    body = json.dumps({"Releases": [{"CategoryName": "Stable", "Version": "3.0.3",
                                     "File": {"Url": "https://proton.me/drive.dmg", "Sha512CheckSum": "abc"}}]})
    d = _feed_dir(tmp_path, "protondrive", body)
    r = _bash(f"_web_vendor_lookup protondrive '{_cfg('protondrive', PROTON)}'",
              {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    assert r.stdout.strip().split("|")[:4] == ["3.0.3", "https://proton.me/drive.dmg", "sha512hex", "abc"]


def test_vendor_lookup_missing_or_unparseable_feed_fails(tmp_path: Path) -> None:
    d = _feed_dir(tmp_path, "claude", "not json")
    r = _bash(f"_web_vendor_lookup claude '{_cfg('claude', CLAUDE)}'", {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    assert r.returncode != 0 and r.stdout.strip() == ""
    r = _bash(f"_web_vendor_lookup nofile '{_cfg('nofile', CLAUDE)}'", {"ASCENDO_WEB_VENDOR_FEED_DIR": str(d)})
    assert r.returncode != 0 and r.stdout.strip() == ""


def test_vendor_lookup_without_vendor_feed_table_fails() -> None:
    r = _bash("_web_vendor_lookup claude '{\"slug\":\"claude\",\"handler\":\"squirrel\"}'")
    assert r.returncode != 0 and r.stdout.strip() == ""


# ------------------------------------------------------------ network fetch

def _fake_curl(tmp_path: Path, body: str = "{}") -> tuple[Path, Path]:
    log = tmp_path / "curl.argv"
    bin_ = tmp_path / "fakecurl"
    bin_.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\n' "$@" > {log}
        out=""
        while [ $# -gt 0 ]; do
          if [ "$1" = "-o" ]; then out="$2"; shift; fi
          shift
        done
        [ -n "$out" ] && printf '%s' '{body}' > "$out"
        exit 0
        """))
    bin_.chmod(bin_.stat().st_mode | stat.S_IEXEC)
    return bin_, log


def test_fetch_feed_refuses_non_https_without_calling_curl(tmp_path: Path) -> None:
    curl, log = _fake_curl(tmp_path)
    for url in ("http://example.test/f", "file:///etc/hosts", "ftp://example.test/f", ""):
        r = _bash(f"_web_fetch_feed '{url}'", {"ASCENDO_WEB_CURL_BIN": str(curl)})
        assert r.returncode != 0, url
    assert not log.exists()


def test_fetch_feed_uses_https_only_bounded_curl(tmp_path: Path) -> None:
    curl, log = _fake_curl(tmp_path, body='{"a":1}')
    r = _bash("_web_fetch_feed 'https://example.test/feed.json'", {"ASCENDO_WEB_CURL_BIN": str(curl)})
    assert r.returncode == 0 and r.stdout == '{"a":1}'
    argv = log.read_text().split("\n")
    assert "--proto" in argv and argv[argv.index("--proto") + 1] == "=https"
    assert "--proto-redir" in argv and argv[argv.index("--proto-redir") + 1] == "=https"
    assert "--max-time" in argv
    assert "--max-filesize" in argv and argv[argv.index("--max-filesize") + 1] == "5242880"
    assert argv[-2] == "https://example.test/feed.json"


def test_fetch_feed_caps_body_at_5_mib(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (5 * 1024 * 1024 + 4096))
    curl = tmp_path / "bigcurl"
    curl.write_text(f"#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && cp {big} \"$2\"; shift; done\n")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run(["bash", "-c", f"source {LIB}/ascendo_web.sh; _web_fetch_feed https://x.test/f | wc -c"],
                       capture_output=True, text=True, env=_env({"ASCENDO_WEB_CURL_BIN": str(curl)}))
    assert int(r.stdout.strip()) == 5 * 1024 * 1024


def test_vendor_lookup_network_path_goes_through_fetch(tmp_path: Path) -> None:
    body = (FIX / "claude_releases.json").read_text().replace("'", "")
    curl, log = _fake_curl(tmp_path, body=body)
    r = _bash(f"_web_vendor_lookup claude '{_cfg('claude', CLAUDE)}'", {"ASCENDO_WEB_CURL_BIN": str(curl)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().split("|")[0] == "2.7032.0"
    assert CLAUDE["url"] in log.read_text()
