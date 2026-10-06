"""supported_native_path: proof that a command is a vendor-native install.

Task 8 ports only this function of macOS_updates' lib/python/cli_duplicates.py
(F4 / T8-c). The duplicate planner (npm_duplicates, removal_allowed, ...) is
Task 8b. Ported from upstream tests/test_cli_duplicates.py
(DuplicatePlannerTests.test_native_proof_rejects_every_npm_prefix_and_arbitrary_binary).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cli_duplicates import supported_native_path  # noqa: E402


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
