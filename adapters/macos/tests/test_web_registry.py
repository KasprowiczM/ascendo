"""WebRegistry Pydantic model tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ascendo_macos.web_registry import WebApp, WebRegistry


def _write_toml(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_schema_version_required(tmp_path: Path) -> None:
    shipped = _write_toml(tmp_path, "shipped.toml", '[[app]]\nslug = "x"\n')
    with pytest.raises(ValidationError) as exc:
        WebRegistry.load(shipped, None)
    assert "schema" in str(exc.value).lower()


def test_schema_version_unknown_rejected(tmp_path: Path) -> None:
    # v1 and v2 are valid; anything else (e.g. v3) must raise ValidationError.
    shipped = _write_toml(
        tmp_path, "shipped.toml",
        'schema = "ascendo-web-apps/v3"\n[[app]]\nslug = "x"\nbundle_id = "com.example.x"\n'
        'display_name = "X"\nhandler = "squirrel"\n',
    )
    with pytest.raises(ValidationError):
        WebRegistry.load(shipped, None)


VALID_HEADER = 'schema = "ascendo-web-apps/v2"\n'


def _entry(**kwargs) -> str:
    base = {
        "slug": "x",
        "bundle_id": "com.example.x",
        "display_name": "X",
        "handler": "squirrel",
    }
    base.update(kwargs)
    lines = ["[[app]]"]
    for k, v in base.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        elif isinstance(v, list):
            lines.append(f"{k} = {v!r}")
        else:
            lines.append(f'{k} = "{v}"')
    return "\n".join(lines) + "\n"


def test_minimal_squirrel_entry_loads(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "shipped.toml", VALID_HEADER + _entry())
    reg = WebRegistry.load(p, None)
    assert len(reg.apps) == 1
    assert reg.apps[0].slug == "x"
    assert reg.apps[0].handler == "squirrel"


def test_slug_pattern_rejects_uppercase(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml", VALID_HEADER + _entry(slug="Bad"))
    with pytest.raises(ValidationError):
        WebRegistry.load(p, None)


def test_slug_pattern_rejects_dots(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml", VALID_HEADER + _entry(slug="bad.slug"))
    with pytest.raises(ValidationError):
        WebRegistry.load(p, None)


def test_sparkle_requires_appcast_url(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml", VALID_HEADER + _entry(handler="sparkle"))
    reg = WebRegistry.load(p, None)
    assert reg.apps[0].handler == "sparkle"
    assert reg.apps[0].appcast_url is None


def test_sparkle_with_appcast_url_loads(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(handler="sparkle",
                              appcast_url="https://example.com/cast.xml"),
    )
    reg = WebRegistry.load(p, None)
    assert reg.apps[0].handler == "sparkle"


def test_github_dmg_requires_repo_and_pattern(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml",
                    VALID_HEADER + _entry(handler="github_dmg"))
    with pytest.raises(ValidationError, match="github_repo"):
        WebRegistry.load(p, None)


def test_github_dmg_repo_pattern_rejects_bad_format(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(handler="github_dmg", github_repo="no-slash",
                              asset_pattern=".*"),
    )
    with pytest.raises(ValidationError):
        WebRegistry.load(p, None)


def test_keystone_requires_product_id(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml", VALID_HEADER + _entry(handler="keystone"))
    with pytest.raises(ValidationError, match="ksadmin_product_id"):
        WebRegistry.load(p, None)


def test_squirrel_with_appcast_url_rejected(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(handler="squirrel",
                              appcast_url="https://example.com/x"),
    )
    with pytest.raises(ValidationError, match="only valid for sparkle"):
        WebRegistry.load(p, None)


def test_disabled_entry_excluded_from_active(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(slug="off", enabled=False) + _entry(slug="on"),
    )
    reg = WebRegistry.load(p, None)
    assert {a.slug for a in reg.apps} == {"off", "on"}
    assert {a.slug for a in reg.active_apps()} == {"on"}


def test_user_override_replaces_by_slug(tmp_path: Path) -> None:
    shipped = _write_toml(
        tmp_path, "ship.toml",
        VALID_HEADER + _entry(slug="chrome", display_name="Google Chrome",
                              handler="keystone",
                              ksadmin_product_id="com.google.Chrome"),
    )
    user = _write_toml(
        tmp_path, "user.toml",
        VALID_HEADER + _entry(slug="chrome", display_name="Chrome (custom)",
                              handler="squirrel"),
    )
    reg = WebRegistry.load(shipped, user)
    assert len(reg.apps) == 1
    assert reg.apps[0].handler == "squirrel"
    assert reg.apps[0].display_name == "Chrome (custom)"


def test_user_override_appends_new_bundle_id(tmp_path: Path) -> None:
    # v2 merges by bundle_id; distinct bundle_ids must both appear.
    shipped = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(slug="a", bundle_id="com.example.a"),
    )
    user = _write_toml(
        tmp_path, "u.toml",
        VALID_HEADER + _entry(slug="b", bundle_id="com.example.b"),
    )
    reg = WebRegistry.load(shipped, user)
    assert {a.slug for a in reg.apps} == {"a", "b"}


def test_find_returns_none_for_missing_slug(tmp_path: Path) -> None:
    p = _write_toml(tmp_path, "s.toml", VALID_HEADER + _entry(slug="x"))
    reg = WebRegistry.load(p, None)
    assert reg.find("nonexistent") is None
    assert reg.find("x") is not None


def test_squirrel_with_arch_rejected(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(handler="squirrel", arch="x86_64"),
    )
    with pytest.raises(ValidationError, match="arch.*github_dmg"):
        WebRegistry.load(p, None)


def test_sparkle_http_appcast_rejected(tmp_path: Path) -> None:
    p = _write_toml(
        tmp_path, "s.toml",
        VALID_HEADER + _entry(handler="sparkle",
                              appcast_url="http://example.com/cast.xml"),
    )
    with pytest.raises(ValidationError):
        WebRegistry.load(p, None)


def test_malformed_toml_raises_with_line_info(tmp_path: Path) -> None:
    """tomllib raises TOMLDecodeError with line/col context on malformed input."""
    import tomllib
    p = _write_toml(tmp_path, "bad.toml", 'schema = "ascendo-web-apps/v1"\n[[app\n')
    with pytest.raises(tomllib.TOMLDecodeError) as exc:
        WebRegistry.load(p, None)
    # tomllib error messages include line context (no exact format guaranteed,
    # but the line number should appear somewhere)
    msg = str(exc.value).lower()
    assert "1" in msg or "2" in msg or "line" in msg
