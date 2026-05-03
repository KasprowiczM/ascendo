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


@pytest.fixture(scope="module", autouse=True)
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


def test_mas_list_json_parses_id_name_version():
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


def test_mas_outdated_json_parses_arrow():
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
    # 6 -> failed-not-signed-in
    res = _bash("mas_classify_exit 6")
    assert res.stdout.strip() == "failed-not-signed-in"


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
    fake_ok = FIX / "_fake_mas_ok.sh"
    fake_fail = FIX / "_fake_mas_fail.sh"
    fake_ok.write_text("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(fake_ok, 0o755)
    fake_fail.write_text("#!/usr/bin/env bash\nexit 1\n")
    os.chmod(fake_fail, 0o755)

    res = _bash(f"export MAS_BIN={fake_ok}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "PASS"

    res = _bash(f"export MAS_BIN={fake_fail}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "FAIL"
