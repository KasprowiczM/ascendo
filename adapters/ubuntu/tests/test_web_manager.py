"""WebManager — Linux web-app updater unit tests.

Mock-based; runs on any OS. Covers:
  * Pydantic schema (registry parse, validators, override merge)
  * web_discovery.sh against fake AppImage dirs (excludes apt-owned)
  * release_feed_check + github_release_check via fake curl on PATH
  * WebManager smoke (is_available, run_phase dispatch)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_ROOT = REPO_ROOT / "adapters" / "ubuntu"
LIB_DIR = ADAPTER_ROOT / "lib"
SCRIPTS_DIR = ADAPTER_ROOT / "scripts"
CONFIG_DIR = ADAPTER_ROOT / "config"


# ── Registry schema ───────────────────────────────────────────────────────


def test_shipped_registry_parses_cleanly() -> None:
    from ascendo_ubuntu.web_registry import WebRegistry
    reg = WebRegistry.load(CONFIG_DIR / "web_apps.toml", None)
    assert reg.schema_version == "ascendo-web-apps-linux/v1"
    assert len(reg.apps) >= 4
    # Shipped registry must contain at least obsidian + cursor
    ids = {a.app_id for a in reg.apps}
    assert "obsidian" in ids
    assert "cursor" in ids


def test_registry_active_apps_filters_disabled() -> None:
    from ascendo_ubuntu.web_registry import WebRegistry
    reg = WebRegistry.load(CONFIG_DIR / "web_apps.toml", None)
    # vscode-insiders-tarball is shipped enabled=false
    active_ids = {a.app_id for a in reg.active_apps()}
    assert "vscode-insiders-tarball" not in active_ids


def test_user_override_replaces_shipped(tmp_path: Path) -> None:
    from ascendo_ubuntu.web_registry import WebRegistry
    user = tmp_path / "web_apps.toml"
    user.write_text(
        'schema = "ascendo-web-apps-linux/v1"\n'
        '[[app]]\n'
        'app_id = "obsidian"\n'
        'display_name = "Obsidian (custom)"\n'
        'handler = "builtin"\n'
    )
    reg = WebRegistry.load(CONFIG_DIR / "web_apps.toml", user)
    obs = reg.find("obsidian")
    assert obs is not None
    assert obs.display_name == "Obsidian (custom)"
    assert obs.handler == "builtin"


def test_release_feed_validates_format_text_requires_regex() -> None:
    from pydantic import ValidationError
    from ascendo_ubuntu.web_registry import ReleaseFeedConfig
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(url="https://example.com/v",
                          format="text")  # missing version_regex


def test_release_feed_rejects_regex_without_replace() -> None:
    from pydantic import ValidationError
    from ascendo_ubuntu.web_registry import ReleaseFeedConfig
    with pytest.raises(ValidationError):
        ReleaseFeedConfig(
            url="https://example.com/v.json",
            version_path="ver",
            version_regex=r"v(.+)",
            # version_replace missing
        )


def test_github_release_compiles_pattern_at_load() -> None:
    from pydantic import ValidationError
    from ascendo_ubuntu.web_registry import GitHubReleaseConfig
    with pytest.raises(ValidationError):
        GitHubReleaseConfig(repo="owner/repo", asset_pattern="[unclosed")


def test_handler_subtable_must_match_handler() -> None:
    from pydantic import ValidationError
    from ascendo_ubuntu.web_registry import WebApp
    with pytest.raises(ValidationError):
        WebApp(app_id="x", display_name="X", handler="builtin",
               github_release={"repo": "a/b", "asset_pattern": ".*"})


# ── Discovery ─────────────────────────────────────────────────────────────


def _discovery_rows(out: str) -> list[dict]:
    """Parse discovery NDJSON, skipping the W10 DISCOVERY_OK/DISCOVERY_FAILED
    TAB-prefixed sentinel lines (not JSON)."""
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        if line.startswith("DISCOVERY_OK") or line.startswith("DISCOVERY_FAILED"):
            continue
        rows.append(json.loads(line))
    return rows


def test_discovery_emits_appimage(tmp_path: Path) -> None:
    """Fake AppImage in ASCENDO_WEB_APPS_ROOT/Applications is discovered."""
    apps = tmp_path / "Applications"
    apps.mkdir()
    fake = apps / "Obsidian-1.5.3.AppImage"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)

    env = {**os.environ, "ASCENDO_WEB_APPS_ROOT": str(tmp_path)}
    out = subprocess.check_output(
        ["bash", str(LIB_DIR / "web_discovery.sh")],
        env=env, text=True, timeout=30,
    )
    rows = _discovery_rows(out)
    obs = [r for r in rows if "obsidian" in r["app_id"].lower()]
    assert obs, f"no obsidian row in discovery output: {rows}"
    assert obs[0]["handler_hint"] == "appimage"
    assert obs[0]["owned_by"] == "user"
    # W10: clean walk ends with a DISCOVERY_OK sentinel.
    assert any(line.startswith("DISCOVERY_OK") for line in out.splitlines())


def test_discovery_skips_when_no_appimages(tmp_path: Path) -> None:
    """Empty ASCENDO_WEB_APPS_ROOT yields zero discovered AppImages."""
    env = {**os.environ, "ASCENDO_WEB_APPS_ROOT": str(tmp_path)}
    out = subprocess.check_output(
        ["bash", str(LIB_DIR / "web_discovery.sh")],
        env=env, text=True, timeout=30,
    )
    rows = _discovery_rows(out)
    # /opt binaries may still match if they exist on this host; check
    # only that no appimage rows came out of the empty fake root.
    appimage_rows = [r for r in rows if r["handler_hint"] == "appimage"]
    assert appimage_rows == []
    # W10: even with zero apps, the walk reports a DISCOVERY_OK sentinel so
    # check.sh can tell "0 web apps" from a crash.
    assert any(line.startswith("DISCOVERY_OK") for line in out.splitlines())


# ── Handler check probes ──────────────────────────────────────────────────


def _setup_fake_curl(tmp_path: Path, response: str) -> dict:
    """Create a fake `curl` on PATH that always echoes `response`."""
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir(parents=True, exist_ok=True)
    fake_curl = fake_dir / "curl"
    body = response.replace("'", "'\"'\"'")
    fake_curl.write_text(
        "#!/bin/sh\n"
        f"cat <<'CURL_EOF'\n{response}\nCURL_EOF\n"
    )
    fake_curl.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_dir}:{os.environ.get('PATH', '')}"}
    return env


def test_release_feed_check_walks_json_path(tmp_path: Path) -> None:
    env = _setup_fake_curl(tmp_path, '{"version": "1.2.3"}')
    cfg = json.dumps({
        "app_id": "x", "handler": "release_feed",
        "release_feed": {
            "url": "https://example.com/v.json",
            "version_path": "version",
        },
    })
    script = (
        f'source "{LIB_DIR}/handlers/release_feed.sh"\n'
        f"release_feed_check x '{cfg}'\n"
    )
    out = subprocess.check_output(["bash", "-c", script], env=env,
                                   text=True, timeout=15)
    assert out.strip() == "1.2.3"


# ── W2: release_feed version_regex fail-loud (audit §4) ───────────────────


def _run_release_feed_check(cfg: dict, env: dict) -> tuple[str, int]:
    """Run release_feed_check and return (version, rc)."""
    script = (
        f'source "{LIB_DIR}/handlers/release_feed.sh"\n'
        f"v=$(release_feed_check x '{json.dumps(cfg)}'); rc=$?\n"
        'printf "VER=%s RC=%s\\n" "$v" "$rc"\n'
    )
    out = subprocess.check_output(["bash", "-c", script], env=env,
                                   text=True, timeout=15).strip()
    ver = out.split("VER=", 1)[1].split(" RC=", 1)[0]
    rc = int(out.rsplit("RC=", 1)[1])
    return ver, rc


def test_release_feed_regex_no_match_is_probe_broken(tmp_path: Path) -> None:
    """W2: a configured version_regex that does not match the JSON value is a
    broken probe (rc 28 + empty), NOT a silent fallback to the raw value."""
    env = _setup_fake_curl(tmp_path, '{"version": "1.2.3"}')
    cfg = {
        "app_id": "x", "handler": "release_feed",
        "release_feed": {
            "url": "https://example.com/v.json",
            "version_path": "version",
            "version_regex": r"NOMATCH-(\d+)",
            "version_replace": r"\1",
        },
    }
    ver, rc = _run_release_feed_check(cfg, env)
    assert rc == 28
    assert ver == ""


def test_release_feed_regex_match_transforms(tmp_path: Path) -> None:
    """W2: a matching version_regex transforms the value (rc 0)."""
    env = _setup_fake_curl(tmp_path, '{"version": "release-9.9.9"}')
    cfg = {
        "app_id": "x", "handler": "release_feed",
        "release_feed": {
            "url": "https://example.com/v.json",
            "version_path": "version",
            "version_regex": r"release-(.+)",
            "version_replace": r"\1",
        },
    }
    ver, rc = _run_release_feed_check(cfg, env)
    assert rc == 0
    assert ver == "9.9.9"


# ── W10: discovery failure sentinel (audit §4) ────────────────────────────


def test_discovery_failed_sentinel_without_python3(tmp_path: Path) -> None:
    """W10: a crashed discovery must be distinguishable from '0 web apps'. With
    python3 unavailable (empty PATH), discovery emits DISCOVERY_FAILED and
    exits non-zero instead of silently producing zero rows."""
    import shutil

    bash = shutil.which("bash") or "/bin/bash"
    empty = tmp_path / "emptybin"
    empty.mkdir()
    env = {"PATH": str(empty), "ASCENDO_WEB_APPS_ROOT": str(tmp_path)}
    res = subprocess.run(
        [bash, str(LIB_DIR / "web_discovery.sh")],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert res.returncode == 3
    assert "DISCOVERY_FAILED" in res.stdout
    assert "no-python3" in res.stdout


# ── W11: PEP-440 version comparator replaces sort -V (audit §4) ────────────


def test_version_gt_orders_prerelease_below_release() -> None:
    """W11: `sort -V` wrongly ranks 1.0.0-beta above 1.0.0. The Python
    comparator (ascendo.utils.version.version_gt) orders the release above the
    pre-release."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "core")}
    script = (
        f'source "{LIB_DIR}/ascendo_web.sh"\n'
        '_version_gt "1.0.0" "1.0.0-beta"; echo "a=$?"\n'
        '_version_gt "1.0.0-beta" "1.0.0"; echo "b=$?"\n'
        '_version_gt "2.0.0" "10.0.0"; echo "c=$?"\n'
    )
    out = subprocess.check_output(["bash", "-c", script], env=env,
                                   text=True, timeout=15)
    # a: 1.0.0 > 1.0.0-beta  -> exit 0
    assert "a=0" in out
    # b: 1.0.0-beta > 1.0.0  -> exit 1 (false)
    assert "b=1" in out
    # c: 2.0.0 > 10.0.0      -> exit 1 (false; numeric, not lexical)
    assert "c=1" in out


def test_github_release_check_extracts_tag_name(tmp_path: Path) -> None:
    payload = json.dumps({
        "tag_name": "v3.4.5",
        "assets": [
            {"name": "Obsidian-3.4.5.AppImage",
             "browser_download_url": "https://gh/example/x.AppImage"},
        ],
    })
    env = _setup_fake_curl(tmp_path, payload)
    cfg = json.dumps({
        "app_id": "obsidian", "handler": "github_release",
        "github_release": {
            "repo": "obsidianmd/obsidian-releases",
            "asset_pattern": "Obsidian-.*\\.AppImage$",
            "prerelease": False,
            "strip_v_prefix": True,
        },
    })
    script = (
        f'source "{LIB_DIR}/handlers/github_release.sh"\n'
        f"github_release_check obsidian '{cfg}'\n"
    )
    out = subprocess.check_output(["bash", "-c", script], env=env,
                                   text=True, timeout=15)
    assert out.strip() == "3.4.5"


def test_builtin_check_returns_empty() -> None:
    script = (
        f'source "{LIB_DIR}/handlers/builtin.sh"\n'
        "builtin_check x '{}'\n"
    )
    out = subprocess.check_output(["bash", "-c", script], text=True, timeout=10)
    assert out.strip() == ""


# ── WebManager smoke ──────────────────────────────────────────────────────


def test_webmanager_identity() -> None:
    from ascendo.models.package import SourceType
    from ascendo_ubuntu.managers.web import WebManager
    m = WebManager(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, repo_root=REPO_ROOT)
    assert m.category is SourceType.WEB
    assert "AppImage" in m.display_name or "web" in m.display_name.lower()


def test_webmanager_is_available_requires_curl() -> None:
    from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
    from ascendo_ubuntu.managers.web import WebManager
    from unittest.mock import patch

    m = WebManager(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, repo_root=REPO_ROOT)
    host = HostInfo(
        hostname="test", os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04", arch="x86_64", user="t",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )
    # When curl is missing, is_available is False.
    with patch("shutil.which", return_value=None):
        assert m.is_available(host) is False
    # When curl present + registry parses, is_available is True.
    with patch("shutil.which", return_value="/usr/bin/curl"):
        assert m.is_available(host) is True


def test_webmanager_is_available_false_off_linux() -> None:
    from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
    from ascendo_ubuntu.managers.web import WebManager

    m = WebManager(scripts_dir=SCRIPTS_DIR, lib_dir=LIB_DIR, repo_root=REPO_ROOT)
    host = HostInfo(
        hostname="mac.local", os=OperatingSystem.MACOS,
        os_version="14.0", arch="arm64", user="t",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )
    assert m.is_available(host) is False


def test_webmanager_in_adapter_package_managers() -> None:
    from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
    from ascendo_ubuntu import UbuntuAdapter
    from ascendo_ubuntu.managers.web import WebManager

    a = UbuntuAdapter()
    host = HostInfo(
        hostname="t", os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04", arch="x86_64", user="t",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )
    mgrs = a.package_managers(host)
    web_mgrs = [m for m in mgrs if isinstance(m, WebManager)]
    assert len(web_mgrs) == 1, "WebManager must be wired into adapter"


# ── End-to-end check phase (real scripts, mocked curl) ────────────────────


def test_check_phase_runs_against_empty_host(tmp_path: Path) -> None:
    """check.sh exits cleanly with 0 items when nothing is discovered."""
    env = {
        **os.environ,
        "ASCENDO_WEB_APPS_ROOT": str(tmp_path),  # no AppImages
        "JSON_OUT": str(tmp_path / "check.json"),
        "LOG_FILE": str(tmp_path / "check.log"),
        "ORCH_RUN_ID": "test-run",
        "ORCH_RUN_DIR": str(tmp_path),
        "ORCH_DRY_RUN": "0",
        "ORCH_PROFILE": "quick",
        # Block real network: replace curl with a fake that errors out.
        "PATH": f"{tmp_path}/bin:{os.environ.get('PATH', '')}",
    }
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 7\n")
    (fake_bin / "curl").chmod(0o755)

    rc = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "web" / "check.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    # Exit 0 even when network probes fail (handlers return empty → skipped).
    assert rc.returncode == 0, (
        f"check.sh exited {rc.returncode}\nstdout: {rc.stdout}\nstderr: {rc.stderr}"
    )
    sidecar = tmp_path / "check.json"
    assert sidecar.exists(), "sidecar not written"
    data = json.loads(sidecar.read_text())
    # legacy bash emitter writes ubuntu-aktualizacje/v1; canonical writers emit ascendo/v1
    assert data.get("schema") in ("ascendo/v1", "ubuntu-aktualizacje/v1")
    assert data.get("category") == "web"
