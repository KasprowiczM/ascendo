"""github_dmg.sh handler tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/github_dmg.sh
        {snippet}
    """)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def _fake_gh_response(tmp_path: Path, version: str = "1.2.3",
                     asset_name: str = "App-1.2.3-arm64.dmg") -> Path:
    body = {
        "tag_name": f"v{version}",
        "name": f"App {version}",
        "prerelease": False,
        "assets": [
            {"name": asset_name,
             "browser_download_url": f"https://github.com/x/y/releases/download/v{version}/{asset_name}"},
            {"name": "App-1.2.3-x86_64.dmg",
             "browser_download_url": "https://github.com/x/y/releases/download/v1.2.3/App-1.2.3-x86_64.dmg"},
        ],
    }
    p = tmp_path / "release.json"
    p.write_text(json.dumps(body))
    return p


def test_github_dmg_check_extracts_version_from_tag(tmp_path: Path) -> None:
    fake = _fake_gh_response(tmp_path)
    cfg = json.dumps({
        "slug": "x",
        "github_repo": "x/y",
        "asset_pattern": "App-.+-arm64\\.dmg$",
    })
    snippet = (
        f"export ASCENDO_WEB_GH_RELEASE_OVERRIDE='{fake}'\n"
        f"github_dmg_check 'x' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == "1.2.3"


def test_github_dmg_check_returns_empty_on_no_match(tmp_path: Path) -> None:
    fake = _fake_gh_response(tmp_path, asset_name="App-1.2.3-windows.exe")
    cfg = json.dumps({
        "slug": "x", "github_repo": "x/y",
        "asset_pattern": "App-.+-arm64\\.dmg$",
    })
    snippet = (
        f"export ASCENDO_WEB_GH_RELEASE_OVERRIDE='{fake}'\n"
        f"github_dmg_check 'x' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.stdout.strip() == ""


def test_github_dmg_check_skips_prereleases_by_default(tmp_path: Path) -> None:
    body = {
        "tag_name": "v2.0.0-beta", "prerelease": True,
        "assets": [{"name": "App-2.0.0-arm64.dmg",
                    "browser_download_url": "https://x/y.dmg"}],
    }
    fake = tmp_path / "r.json"
    fake.write_text(json.dumps(body))
    cfg = json.dumps({
        "slug": "x", "github_repo": "x/y",
        "asset_pattern": "App-.+-arm64\\.dmg$",
    })
    snippet = (
        f"export ASCENDO_WEB_GH_RELEASE_OVERRIDE='{fake}'\n"
        f"github_dmg_check 'x' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.stdout.strip() == ""


def test_github_dmg_check_includes_prereleases_when_opted_in(tmp_path: Path) -> None:
    body = {
        "tag_name": "v2.0.0-beta", "prerelease": True,
        "assets": [{"name": "App-2.0.0-arm64.dmg",
                    "browser_download_url": "https://x/y.dmg"}],
    }
    fake = tmp_path / "r.json"
    fake.write_text(json.dumps(body))
    cfg = json.dumps({
        "slug": "x", "github_repo": "x/y",
        "asset_pattern": "App-.+-arm64\\.dmg$",
        "prerelease": True,
    })
    snippet = (
        f"export ASCENDO_WEB_GH_RELEASE_OVERRIDE='{fake}'\n"
        f"github_dmg_check 'x' {json.dumps(cfg)!r}"
    )
    r = _run(snippet)
    assert r.stdout.strip() == "2.0.0-beta"
