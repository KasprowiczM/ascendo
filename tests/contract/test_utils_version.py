"""core/ascendo/utils/version.py: unparseable input must never compare as newer."""
from __future__ import annotations

import pytest

from ascendo.utils.version import version_gt, version_gte, version_lt


@pytest.mark.parametrize("a,b", [
    ("zzz", "1.0"),          # used to be True via plain string compare
    ("1.0", "zzz"),
    ("abc", "abd"),          # 'abd' > 'abc' as strings -> must stay False both ways
    ("abd", "abc"),
    ("2.0 (wat)", "1.0"),
    ("1.0", "2.0 (wat)"),
])
def test_unparseable_input_is_never_newer(a: str, b: str) -> None:
    assert version_gt(a, b) is False


def test_parseable_versions_still_compare() -> None:
    assert version_gt("1.10", "1.2")
    assert not version_gt("1.2", "1.10")
    assert version_gt("2.0.0", "2.0.0rc1")
    assert version_gte("1.0", "1.0") and version_lt("1.0", "1.1")


def test_empty_inputs_are_false() -> None:
    assert version_gt("", "1.0") is False and version_gt("1.0", "") is False
