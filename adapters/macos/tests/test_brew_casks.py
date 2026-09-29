"""Pure-function tests for adapters/macos/lib/brew_casks.py.

Ported from macOS_updates ``tests/test_brew_casks.py`` (orphans, sudo, guard
facts) plus the version compare / app-vs-package relation and the ``quit:``
bundle-id extraction used to recognise a running cask app. No brew, no fakes:
everything here is JSON in, strings out.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
MODULE = ADAPTER_ROOT / "lib" / "brew_casks.py"
sys.path.insert(0, str(ADAPTER_ROOT / "lib"))

from brew_casks import (  # noqa: E402
    app_targets,
    app_vs_package_version_relation,
    bundle_version,
    cask_facts_lines,
    cask_guard_facts,
    cask_primary_app,
    cask_requires_sudo,
    find_orphan_casks,
    pkg_app_hints,
    quit_bundle_ids,
    version_compare,
    version_relation,
)

ZOOM = {
    "token": "zoom",
    "version": "6.2.11.43576",
    "installed": "6.2.11.43576",
    "artifacts": [
        {"pkg": ["zoomus.pkg"]},
        {
            "uninstall": [
                {
                    "quit": "us.zoom.xos",
                    "delete": [
                        "/Applications/zoom.us.app",
                        "/Library/Audio/Plug-Ins/HAL/ZoomAudioDevice.driver",
                        "~/Library/Application Support/zoom.us",
                    ],
                }
            ]
        },
    ],
}


# ── app targets / guard facts ────────────────────────────────────────────────

def test_app_targets_string_and_dict_forms():
    assert app_targets({"artifacts": [{"app": ["Google Chrome.app"]}]}) == ["Google Chrome.app"]
    assert app_targets({"artifacts": [{"app": [{"target": "Custom App.app"}]}]}) == ["Custom App.app"]
    mixed = {"artifacts": [{"binary": ["cli"]}, {"app": ["First.app", {"target": "Second.app"}]}]}
    assert app_targets(mixed) == ["First.app", "Second.app"]


def test_app_targets_takes_basename_of_nested_target():
    assert app_targets({"artifacts": [{"app": [{"target": "Sub/Dir/Deep.app"}]}]}) == ["Deep.app"]


def test_cask_guard_facts_joins_version_installed_and_targets():
    raw = json.dumps({"casks": [{"token": "t", "version": "2.0", "installed": "1.0",
                                 "artifacts": [{"app": ["MyApp.app", "Helper.app"]}]}]})
    assert cask_guard_facts(raw) == "2.0|1.0|MyApp.app;Helper.app"


def test_cask_guard_facts_pkg_cask_uses_delete_hint():
    facts = cask_guard_facts(json.dumps({"casks": [ZOOM]}))
    assert facts == "6.2.11.43576|6.2.11.43576|zoom.us.app"


def test_pkg_app_hints_only_accepts_applications_app_paths():
    hints = pkg_app_hints(ZOOM)
    assert hints == ["zoom.us.app"]
    assert pkg_app_hints({"artifacts": [{"uninstall": [{"delete": ["/Applications/Sub/X.app"]}]}]}) == []


@pytest.mark.parametrize("raw", ["", "{bad json", '{"casks": []}'])
def test_cask_guard_facts_bad_input_is_empty(raw):
    assert cask_guard_facts(raw) == ""


def test_cask_primary_app():
    raw = '{"casks": [{"token": "a", "artifacts": [{"app": ["Primary.app", "Secondary.app"]}]}]}'
    assert cask_primary_app(raw) == "Primary.app"
    assert cask_primary_app('{"casks": [{"artifacts": []}]}') == ""
    assert cask_primary_app("not-json") == ""


# ── orphans ──────────────────────────────────────────────────────────────────

def test_orphan_when_all_targets_missing():
    info = {"casks": [
        {"token": "missing-app", "artifacts": [{"app": ["Missing.app"]}]},
        {"token": "present-app", "artifacts": [{"app": ["Present.app"]}]},
    ]}
    orphans = find_orphan_casks(info, ["/Applications", "/Users/fake/Applications"],
                                exists=lambda p: "Present.app" in p)
    assert orphans == ["missing-app"]


def test_not_orphan_when_one_of_several_targets_exists():
    info = {"casks": [{"token": "multi", "artifacts": [{"app": ["A.app", "B.app"]}]}]}
    assert find_orphan_casks(info, ["/x"], exists=lambda p: p.endswith("B.app")) == []


def test_not_orphan_without_app_artifact():
    info = {"casks": [
        {"token": "cli-only", "artifacts": [{"binary": ["mycli"]}]},
        {"token": "pkg-only", "artifacts": [{"pkg": ["installer.pkg"]}]},
    ]}
    assert find_orphan_casks(info, ["/Applications"], exists=lambda p: False) == []


def test_orphan_detection_ignores_delete_hints():
    assert find_orphan_casks({"casks": [ZOOM]}, ["/Applications"], exists=lambda p: False) == []


def test_orphan_uses_real_filesystem_by_default(tmp_path):
    (tmp_path / "Here.app").mkdir()
    info = {"casks": [
        {"token": "here", "artifacts": [{"app": ["Here.app"]}]},
        {"token": "gone", "artifacts": [{"app": ["Gone.app"]}]},
    ]}
    assert find_orphan_casks(info, [str(tmp_path)]) == ["gone"]


# ── sudo ─────────────────────────────────────────────────────────────────────

def test_requires_sudo_pkg_installer_launchctl_pkgutil_kext():
    assert cask_requires_sudo({"artifacts": [{"pkg": ["tool.pkg"]}]})
    assert cask_requires_sudo({"artifacts": [{"installer": {"manual": "setup.app"}}]})
    for key in ("launchctl", "pkgutil", "kext"):
        assert cask_requires_sudo({"artifacts": [{"app": ["S.app"]}, {"uninstall": [{key: "x"}]}]})


def test_plain_app_cask_does_not_require_sudo():
    assert not cask_requires_sudo({"artifacts": [{"app": ["Plain.app"]}, {"zap": [{"trash": ["~/x"]}]}]})
    assert not cask_requires_sudo({"artifacts": [{"uninstall": [{"quit": "com.x"}]}]})
    assert not cask_requires_sudo({})


# ── quit bundle ids ──────────────────────────────────────────────────────────

def test_quit_bundle_ids_from_uninstall_and_zap_string_and_list():
    cask = {"artifacts": [
        {"uninstall": [{"quit": "com.example.a"}]},
        {"zap": [{"quit": ["com.example.b", "com.example.a"], "trash": ["~/x"]}]},
        {"uninstall": {"quit": ["com.example.c"]}},
    ]}
    assert quit_bundle_ids(cask) == ["com.example.a", "com.example.b", "com.example.c"]


def test_quit_bundle_ids_ignores_junk():
    assert quit_bundle_ids({"artifacts": [{"uninstall": [{"quit": ["", "not an id", 5]}]}, "x", {"app": ["A.app"]}]}) == []


# ── facts lines (what bash consumes) ─────────────────────────────────────────

def test_cask_facts_lines_format():
    lines = cask_facts_lines({"casks": [
        {"token": "plain", "version": "2.0", "installed": "1.0",
         "artifacts": [{"app": ["Plain.app"]}, {"uninstall": [{"quit": "com.plain"}]}]},
        ZOOM,
        {"token": "cli", "version": "1", "installed": None, "artifacts": [{"binary": ["x"]}]},
        {"version": "no-token"},
    ]})
    assert lines == [
        "plain|2.0|1.0|Plain.app|com.plain|0",
        "zoom|6.2.11.43576|6.2.11.43576|zoom.us.app|us.zoom.xos|1",
        "cli|1||||0",
    ]


# ── three-way compare + relations ────────────────────────────────────────────

@pytest.mark.parametrize("a,b,expected", [
    ("2.0", "1.0", 1), ("1.0", "2.0", -1), ("1.0", "1.0.0", 0),
    ("1.10", "1.9", 1), ("2.0rc1", "2.0", -1), ("2.0", "2.0b3", 1),
    ("latest", "1.0", None), ("", "1.0", None), (None, None, None),
])
def test_version_compare_three_way(a, b, expected):
    assert version_compare(a, b) == expected


def test_version_relation_only_newer_when_provably_greater():
    assert version_relation("2.0", "1.0") == "newer"
    assert version_relation("1.0", "2.0") == "current"
    assert version_relation("1.0", "1.0") == "current"
    assert version_relation("latest", "1.0") == "unknown"


@pytest.mark.parametrize("installed,candidate,expected", [
    # Brave: Chromium major prefixed to the cask's own version.
    ("151.1.93.138", "1.93.138.0", "current"),
    ("151.1.93.139", "1.93.138.0", "newer"),
    ("151.1.93.137", "1.93.138.0", "current"),
    # Same scheme, plain numeric comparison.
    ("4.17.1", "4.15", "newer"),
    ("4.15", "4.17.1", "current"),
    ("1.0", "1.0.0", "current"),
    # Unrelated schemes / unparseable never invent a downgrade.
    ("20260101", "3.2.1", "unknown"),
    ("", "1.0", "unknown"),
    ("1.0", "latest", "unknown"),
])
def test_app_vs_package_version_relation(installed, candidate, expected):
    assert app_vs_package_version_relation(installed, candidate) == expected


# ── bundle version ───────────────────────────────────────────────────────────

def _make_app(root: Path, name: str, short: str | None, build: str | None = None) -> Path:
    import plistlib

    contents = root / name / "Contents"
    contents.mkdir(parents=True)
    data = {}
    if short is not None:
        data["CFBundleShortVersionString"] = short
    if build is not None:
        data["CFBundleVersion"] = build
    (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    return root / name


def test_bundle_version_prefers_short_string_then_build(tmp_path):
    assert bundle_version(str(_make_app(tmp_path, "A.app", "1.2.3", "99"))) == "1.2.3"
    assert bundle_version(str(_make_app(tmp_path, "B.app", None, "42"))) == "42"
    assert bundle_version(str(tmp_path / "Nope.app")) == ""


# ── CLI wrapper used from bash ───────────────────────────────────────────────

def _cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MODULE), *args], input=stdin,
                          capture_output=True, text=True)


def test_cli_facts_and_orphans_over_batched_info_json(tmp_path):
    (tmp_path / "Present.app").mkdir()
    info = json.dumps({"casks": [
        {"token": "present", "version": "1", "installed": "1", "artifacts": [{"app": ["Present.app"]}]},
        {"token": "gone", "version": "1", "installed": "1", "artifacts": [{"app": ["Gone.app"]}]},
    ]})
    facts = _cli("facts", stdin=info)
    assert facts.returncode == 0
    assert facts.stdout.splitlines() == ["present|1|1|Present.app||0", "gone|1|1|Gone.app||0"]
    orphans = _cli("orphans", str(tmp_path), stdin=info)
    assert orphans.stdout.split() == ["gone"]


def test_cli_facts_tolerates_garbage_stdin():
    res = _cli("facts", stdin="not json at all")
    assert res.returncode == 0 and res.stdout == ""


def test_cli_relation_cmp_and_bundle_version(tmp_path):
    assert _cli("relation", "151.1.93.138", "1.93.138.0").stdout.strip() == "current"
    assert _cli("relation", "5.0", "4.0").stdout.strip() == "newer"
    assert _cli("cmp", "2.0", "1.0").stdout.strip() == "newer"
    assert _cli("cmp", "1.0", "1.0").stdout.strip() == "equal"
    assert _cli("cmp", "x", "1.0").stdout.strip() == "unknown"
    app = _make_app(tmp_path, "C.app", "7.7")
    assert _cli("bundle-version", str(app)).stdout.strip() == "7.7"
    assert _cli("bogus").returncode == 2


def test_module_is_python39_syntax_compatible():
    """macOS system python is 3.9: no match statements / PEP 604 at runtime."""
    import ast

    ast.parse(MODULE.read_text(), feature_version=(3, 9))
