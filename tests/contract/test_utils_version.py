"""core/ascendo/utils/version.py.

* ``version_gt`` keeps its legacy two-way behaviour (string fallback for
  unparseable pairs) -- shared by Ubuntu/macOS callers, must not change.
* ``version_cmp3`` is the honest three-way helper: unparseable -> None, never a
  string-order guess.
"""
from __future__ import annotations

import pytest

from ascendo.utils.version import version_cmp3, version_gt, version_gte, version_lt


def test_version_gt_legacy_string_fallback_is_preserved() -> None:
    # Unparseable pairs compare as plain strings (legacy, relied on by
    # two-way callers such as '1.2.4-linux' vs '1.2.3-linux').
    assert version_gt("1.2.4-linux", "1.2.3-linux") is True
    assert version_gt("1.2.3-linux", "1.2.4-linux") is False
    assert version_gt("abd", "abc") is True
    assert version_gt("abc", "abd") is False


def test_version_gt_parseable_and_empty() -> None:
    assert version_gt("1.10", "1.2") and not version_gt("1.2", "1.10")
    assert version_gt("2.0.0", "2.0.0rc1")
    assert version_gte("1.0", "1.0") and version_lt("1.0", "1.1")
    assert version_gt("", "1.0") is False and version_gt("1.0", "") is False


@pytest.mark.parametrize("a,b,want", [
    ("1.10", "1.2", 1), ("1.2", "1.10", -1), ("1.2", "1.2", 0), ("125.0", "125.0.0.0", 0),
    ("2.0.0", "2.0.0rc1", 1), ("v1.2", "1.1", 1),
])
def test_version_cmp3_parseable(a: str, b: str, want: int) -> None:
    assert version_cmp3(a, b) == want


@pytest.mark.parametrize("a,b", [
    ("zzz", "1.0"), ("1.0", "zzz"), ("abd", "abc"), ("1.2.4-linux", "1.2.3-linux"),
    ("2.0 (wat)", "1.0"), ("", "1.0"), ("1.0", ""), ("", ""),
])
def test_version_cmp3_unparseable_is_none_never_a_guess(a: str, b: str) -> None:
    assert version_cmp3(a, b) is None
