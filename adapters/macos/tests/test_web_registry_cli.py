"""CLI shim wrapping WebRegistry."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIM = REPO_ROOT / "adapters" / "macos" / "lib" / "web_registry.py"
ENV = {"PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos"}


def _run(args: list[str], shipped: Path, user: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["python3", str(SHIM), "--shipped", str(shipped)]
    if user is not None:
        cmd += ["--user-override", str(user)]
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True,
                          env={**os.environ, **ENV})


def _toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "s.toml"
    p.write_text(body, encoding="utf-8")
    return p


VH = 'schema = "ascendo-web-apps/v1"\n'


def test_list_slugs_outputs_active_only(tmp_path: Path) -> None:
    body = VH + (
        '[[app]]\nslug = "a"\nbundle_id = "x"\ndisplay_name = "A"\n'
        'handler = "squirrel"\n'
        '[[app]]\nslug = "b"\nbundle_id = "y"\ndisplay_name = "B"\n'
        'handler = "squirrel"\nenabled = false\n'
    )
    p = _toml(tmp_path, body)
    r = _run(["--list-slugs"], p)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines() == ["a"]


def test_get_app_returns_json(tmp_path: Path) -> None:
    body = VH + (
        '[[app]]\nslug = "chrome"\nbundle_id = "com.google.Chrome"\n'
        'display_name = "Google Chrome"\nhandler = "keystone"\n'
        'ksadmin_product_id = "com.google.Chrome"\n'
    )
    p = _toml(tmp_path, body)
    r = _run(["--get-app", "chrome"], p)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["slug"] == "chrome"
    assert data["handler"] == "keystone"
    assert data["ksadmin_product_id"] == "com.google.Chrome"


def test_get_app_unknown_slug_exits_2(tmp_path: Path) -> None:
    body = VH + (
        '[[app]]\nslug = "x"\nbundle_id = "x"\ndisplay_name = "X"\n'
        'handler = "squirrel"\n'
    )
    p = _toml(tmp_path, body)
    r = _run(["--get-app", "nope"], p)
    assert r.returncode == 2
    assert "not found" in r.stderr.lower() or "unknown" in r.stderr.lower()


def test_validate_ok_exits_0(tmp_path: Path) -> None:
    body = VH + (
        '[[app]]\nslug = "x"\nbundle_id = "x"\ndisplay_name = "X"\n'
        'handler = "squirrel"\n'
    )
    p = _toml(tmp_path, body)
    r = _run(["--validate"], p)
    assert r.returncode == 0, r.stderr


def test_validate_bad_exits_2_with_message(tmp_path: Path) -> None:
    p = _toml(tmp_path, '[[app]]\nslug = "x"\n')   # no schema, no required fields
    r = _run(["--validate"], p)
    assert r.returncode == 2
    assert r.stderr.strip()


def test_validate_malformed_toml_exits_2(tmp_path: Path) -> None:
    p = _toml(tmp_path, '[[app\n')
    r = _run(["--validate"], p)
    assert r.returncode == 2
    assert r.stderr.strip()
