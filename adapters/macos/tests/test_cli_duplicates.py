"""cli_duplicates: native-install proof + conservative npm-duplicate planner.

Task 8 ported ``supported_native_path`` (F4 / T8-c); Task 8b ports the planner
(``known_npm_prefixes``, ``npm_duplicates``, ``removal_allowed``). Ported from
upstream tests/test_cli_duplicates.py (DuplicatePlannerTests); the upstream
``vendor_feeds.version_compare`` is replaced by Ascendo's ``brew_casks``
three-way compare.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cli_duplicates import (  # noqa: E402
    brew_copies,
    known_npm_prefixes,
    main,
    npm_duplicates,
    removal_allowed,
    supported_native_path,
)


def _write(path: Path, text: str = "binary") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_native_proof_rejects_every_npm_prefix_and_arbitrary_binary(tmp_path: Path) -> None:
    home = tmp_path
    for target in (
        ".local/share/mac-update/npm-global/lib/node_modules/opencode-ai/bin/opencode",
        ".nvm/versions/node/v24/lib/node_modules/opencode-ai/bin/opencode",
        ".local/bin/arbitrary",
    ):
        exe = _write(home / target)
        assert not supported_native_path(str(home), "opencode", str(exe))
    native = _write(home / ".opencode/bin/opencode")
    assert supported_native_path(str(home), "opencode", str(native))


def test_symlink_into_supported_root_is_native(tmp_path: Path) -> None:
    native = _write(tmp_path / ".opencode/bin/opencode")
    link = tmp_path / ".local/bin/opencode"
    link.parent.mkdir(parents=True)
    link.symlink_to(native)
    assert supported_native_path(str(tmp_path), "opencode", str(link))


def test_symlink_to_npm_wrapper_is_not_native(tmp_path: Path) -> None:
    wrapper = _write(tmp_path / ".local/share/mac-update/npm-global/lib/node_modules/"
                     "opencode-ai/bin/opencode")
    link = tmp_path / ".local/bin/opencode"
    link.parent.mkdir(parents=True)
    link.symlink_to(wrapper)
    assert not supported_native_path(str(tmp_path), "opencode", str(link))


def test_dangling_symlink_and_missing_file_are_not_native(tmp_path: Path) -> None:
    link = tmp_path / ".local/bin/opencode"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / ".opencode/bin/opencode")  # target absent
    assert not supported_native_path(str(tmp_path), "opencode", str(link))
    assert not supported_native_path(str(tmp_path), "opencode", str(tmp_path / "nope"))


@pytest.mark.parametrize("command,root", [
    ("claude", ".local/share/claude/versions"),
    ("codex", ".codex/packages/standalone/releases"),
])
def test_other_supported_commands_and_roots(tmp_path: Path, command: str, root: str) -> None:
    exe = _write(tmp_path / root / "1.2.3" / command)
    assert supported_native_path(str(tmp_path), command, str(exe))
    elsewhere = _write(tmp_path / "elsewhere" / command)
    assert not supported_native_path(str(tmp_path), command, str(elsewhere))


def test_unknown_command_is_never_native(tmp_path: Path) -> None:
    exe = _write(tmp_path / ".local/bin/agy")
    assert not supported_native_path(str(tmp_path), "agy", str(exe))


def test_path_under_node_modules_inside_root_is_rejected(tmp_path: Path) -> None:
    exe = _write(tmp_path / ".opencode/bin/node_modules/x/opencode")
    assert not supported_native_path(str(tmp_path), "opencode", str(exe))


# -- DuplicatePlannerTests (upstream, 5 cases) ----------------------------------

def test_only_explicit_harness_package_in_known_prefix(tmp_path: Path) -> None:
    prefix = tmp_path / ".nvm/versions/node/v24.0.0"
    package = prefix / "lib/node_modules/opencode-ai"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "opencode-ai", "version": "1.2.3"}))
    prefixes = known_npm_prefixes(str(tmp_path), str(tmp_path / "managed"))
    assert npm_duplicates(prefixes, "opencode") == [(prefix, "1.2.3")]
    assert npm_duplicates(prefixes, "cline") == []


def test_native_proof_rejects_npm_prefixes_and_accepts_vendor_root(tmp_path: Path) -> None:
    for target in (".local/share/mac-update/npm-global/lib/node_modules/opencode-ai/bin/opencode",
                   ".nvm/versions/node/v24/lib/node_modules/opencode-ai/bin/opencode",
                   ".local/bin/arbitrary"):
        assert not supported_native_path(str(tmp_path), "opencode", str(_write(tmp_path / target)))
    native = _write(tmp_path / ".opencode/bin/opencode")
    assert supported_native_path(str(tmp_path), "opencode", str(native))


def test_symlink_package_is_retained(tmp_path: Path) -> None:
    other = tmp_path / "user-package"
    other.mkdir()
    (other / "package.json").write_text(json.dumps({"name": "opencode-ai", "version": "1.2.3"}))
    prefix = tmp_path / "prefix"
    modules = prefix / "lib/node_modules"
    modules.mkdir(parents=True)
    (modules / "opencode-ai").symlink_to(other, target_is_directory=True)
    assert npm_duplicates([prefix], "opencode") == []


@pytest.mark.parametrize("native,package,processes", [
    ("1.2.2", "1.2.3", ""),       # older native
    ("?", "1.2.3", ""),           # unparseable native
    ("1.2.3", "?", ""),           # unparseable duplicate
    ("1.2.3", "1.2.3", None),     # process state unknown
])
def test_uncertain_or_older_native_never_removes_fallback(native, package, processes) -> None:
    assert not removal_allowed(native, package, processes, "opencode")


def test_equal_native_with_unrelated_process_allows_removal() -> None:
    assert removal_allowed("1.2.3", "1.2.3", "/usr/bin/other", "opencode")


@pytest.mark.parametrize("processes", [
    "/some/bin/opencode run",
    "node /prefix/lib/node_modules/opencode-ai/bin/opencode",
    "node --enable-source-maps /prefix/lib/node_modules/opencode-ai/bin/opencode",
    "/prefix/opencode.exe",
    "bash -- /prefix/bin/opencode",
])
def test_active_native_or_npm_wrapper_retains_duplicates(processes) -> None:
    assert not removal_allowed("1.2.3", "1.2.2", processes, "opencode")


@pytest.mark.parametrize("processes", [
    "/Applications/OpenCode.app/Contents/MacOS/OpenCode",
    "bash -c type -a opencode; opencode --version",
])
def test_unrelated_process_mentioning_command_does_not_block(processes) -> None:
    assert removal_allowed("1.2.3", "1.2.2", processes, "opencode")


def test_known_prefixes_are_fixed_roots_plus_nvm_and_brew(tmp_path: Path) -> None:
    (tmp_path / ".nvm/versions/node/v22.1.0").mkdir(parents=True)
    got = known_npm_prefixes(str(tmp_path), str(tmp_path / "tc"), "/opt/hb:/usr/hb")
    assert got[:2] == [tmp_path / "tc/npm-global", tmp_path / "tc/node"]
    assert tmp_path / ".nvm/versions/node/v22.1.0" in got
    assert Path("/opt/hb") in got and Path("/usr/hb") in got
    assert len(got) == len(set(got))


# -- Homebrew copies are reported, never planned for removal --------------------

def test_brew_copy_name_and_version_come_from_cellar_or_caskroom(tmp_path: Path) -> None:
    brew = tmp_path / "hb"
    _write(brew / "Cellar/opencode/1.2.3/bin/opencode")
    _write(brew / "Caskroom/claude-code/2.0.1/claude")
    (brew / "bin").mkdir()
    (brew / "bin/opencode").symlink_to(brew / "Cellar/opencode/1.2.3/bin/opencode")
    (brew / "bin/claude").symlink_to(brew / "Caskroom/claude-code/2.0.1/claude")
    assert brew_copies([str(brew / "bin")], "opencode") == [("formula", "opencode", "1.2.3")]
    assert brew_copies([str(brew / "bin")], "claude") == [("cask", "claude-code", "2.0.1")]


def test_brew_copy_outside_a_keg_uses_command_name_unknown_version(tmp_path: Path) -> None:
    _write(tmp_path / "hb/bin/codex")
    assert brew_copies([str(tmp_path / "hb/bin")], "codex") == [("formula", "codex", "")]
    assert brew_copies([str(tmp_path / "hb/bin"), ""], "claude") == []
    assert brew_copies([], "codex") == []


def test_cli_rejects_bad_usage(capsys) -> None:
    assert main([]) == 2
    assert main(["list", "only-one"]) == 2
    assert main(["bogus"]) == 2
