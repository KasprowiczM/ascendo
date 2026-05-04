"""Tests for adapters/macos/lib/ascendo_mas.sh -- pure parsers.

Each test invokes one helper via `bash -c '. lib/ascendo_mas.sh; <fn>'`
and pipes a captured fixture in via stdin. No real mas calls.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_mas.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _bash(snippet: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f". '{LIB}'; {snippet}"],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_lib_exists_and_sources_clean():
    assert LIB.is_file()
    res = _bash(":")
    assert res.returncode == 0, res.stderr


def test_mas_list_json_parses_id_name_version(_require_jq):
    text = (FIX / "mas-list.txt").read_text()
    res = _bash("mas_list_json_from_stdin", stdin=text)
    assert res.returncode == 0, res.stderr
    arr = json.loads(res.stdout)
    assert isinstance(arr, list)
    ids = {e["id"] for e in arr}
    assert "497799835" in ids
    xc = next(e for e in arr if e["id"] == "497799835")
    assert xc["name"] == "Xcode"
    assert xc["version"] == "26.4.1"


def test_mas_outdated_json_parses_arrow(_require_jq):
    text = (FIX / "mas-outdated.txt").read_text()
    res = _bash("mas_outdated_json_from_stdin", stdin=text)
    assert res.returncode == 0, res.stderr
    arr = json.loads(res.stdout)
    assert len(arr) == 2
    keka = next(e for e in arr if e["id"] == "1153157709")
    assert keka["name"] == "Keka"
    assert keka["current_version"] == "1.4.2"
    assert keka["target_version"] == "1.4.3"


def test_mas_classify_exit_known_codes():
    # 0 -> success
    res = _bash("mas_classify_exit 0")
    assert res.returncode == 0
    assert res.stdout.strip() == "success"
    # 1 -> failed
    res = _bash("mas_classify_exit 1")
    assert res.stdout.strip() == "failed"
    # 6 -> failed (not-signed-in semantic preserved via apply.sh error message)
    res = _bash("mas_classify_exit 6")
    assert res.stdout.strip() == "failed"


def test_mas_classify_exit_returns_only_valid_itemstatus_values():
    """All possible mas_classify_exit return values must be valid ItemStatus enum members."""
    import sys
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.result import ItemStatus
    finally:
        sys.path.pop(0)
    valid = {s.value for s in ItemStatus}
    # Test documented exit codes (0=success, 1=generic fail, 6=not-signed-in, 99=unknown)
    for rc in (0, 1, 6, 99):
        res = subprocess.run(
            ["bash", "-c", f". '{LIB}'; mas_classify_exit {rc}"],
            capture_output=True,
            text=True,
            check=False,
        )
        result = res.stdout.strip()
        assert result in valid, (
            f"mas_classify_exit {rc} returned '{result}' which is not in "
            f"ItemStatus enum {valid}"
        )


def test_mas_version_at_least_compares_correctly():
    # mas major from 4.x >= 4 -> 0
    res = _bash("echo '4.3.0' | mas_version_at_least 4 && echo PASS || echo FAIL")
    assert res.stdout.strip() == "PASS"
    # mas major from 3.x >= 4 -> 1
    res = _bash("echo '3.1.0' | mas_version_at_least 4 && echo PASS || echo FAIL")
    assert res.stdout.strip() == "FAIL"


def test_signed_in_probe_runs_without_real_mas(tmp_path):
    """mas_signed_in: returns the exit code of `mas list >/dev/null 2>&1`.

    We can't easily mock `mas` from inside a sourced bash function in pytest,
    so we test the function shape: when MAS_BIN points to a fake script that
    exits 0, mas_signed_in returns 0; when it exits 1, returns 1.
    """
    fake_ok = tmp_path / "_fake_mas_ok.sh"
    fake_fail = tmp_path / "_fake_mas_fail.sh"
    fake_ok.write_text("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(fake_ok, 0o755)
    fake_fail.write_text("#!/usr/bin/env bash\nexit 1\n")
    os.chmod(fake_fail, 0o755)

    res = _bash(f"export MAS_BIN={fake_ok}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "PASS"

    res = _bash(f"export MAS_BIN={fake_fail}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "FAIL"


def test_mas_version_at_least_no_hang_on_empty_stdin(tmp_path):
    """Regression: mas_version_at_least must not block on a closed stdin
    with no data when MAS_BIN is callable."""
    fake_mas = tmp_path / "fake_mas"
    fake_mas.write_text("#!/usr/bin/env bash\necho '4.3.0'\n")
    os.chmod(fake_mas, 0o755)
    # Run with stdin redirected from /dev/null so [ -t 0 ] is false but
    # the read should time out within 2s and fall back to MAS_BIN version.
    res = subprocess.run(
        ["bash", "-c", f"export MAS_BIN={fake_mas}; . '{LIB}'; "
         "mas_version_at_least 4 && echo PASS || echo FAIL"],
        input="",   # closed stdin
        capture_output=True, text=True,
        timeout=10,  # if it hangs, pytest kills via timeout
        check=False,
    )
    assert res.stdout.strip() == "PASS", f"stdout={res.stdout!r} stderr={res.stderr!r}"
