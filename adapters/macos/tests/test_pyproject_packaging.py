"""Static guard: the wheel/sdist must ship the python helpers and TOML registries."""
from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _targets():
    data = tomllib.loads(PYPROJECT.read_text())
    return data["tool"]["hatch"]["build"]["targets"]


def test_wheel_includes_python_helpers_and_config():
    inc = _targets()["wheel"]["include"]
    for pat in ("scripts/**/*.sh", "lib/**/*.sh", "lib/**/*.py", "config/**/*"):
        assert pat in inc, (pat, inc)


def test_sdist_includes_config():
    assert "/config" in _targets()["sdist"]["include"]
