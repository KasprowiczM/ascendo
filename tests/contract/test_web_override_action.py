"""C.3: POST /ai/chat/action/web_override — validate → final-gate probe
→ atomic merge into ~/.config/ascendo/web_apps.toml. The write happens
ONLY when both pass; on failure the file is untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ascendo.dashboard.routes import web_config

_RF_SNIPPET = '''schema = "ascendo-web-apps/v2"

[[app]]
slug = "newapp"
bundle_id = "com.example.NewApp"
display_name = "New App"
handler = "release_feed"

[app.release_feed]
url = "https://example.com/v.json"
version_path = "version"
'''

_BUILTIN_SNIPPET = '''[[app]]
slug = "builtinapp"
bundle_id = "com.example.Builtin"
display_name = "Builtin App"
handler = "builtin"
'''


@pytest.fixture
def user_reg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "web_apps.toml"
    monkeypatch.setenv("ASCENDO_WEB_USER_REGISTRY_PATH", str(p))
    return p


def test_probe_pass_writes_and_validates(user_reg, monkeypatch) -> None:
    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("3.1.4", ""))
    res = web_config.apply_web_override("newapp", _RF_SNIPPET)
    assert res["ok"] is True, res
    assert user_reg.is_file()
    body = user_reg.read_text(encoding="utf-8")
    assert 'schema = "ascendo-web-apps/v2"' in body
    assert "newapp" in body
    # The written file must itself parse + validate.
    from ascendo_macos.web_registry import WebRegistry

    reg = WebRegistry.load(user_reg, None)
    assert reg.find("newapp") is not None
    assert reg.find("newapp").release_feed is not None


def test_probe_fail_does_not_write(user_reg, monkeypatch) -> None:
    user_reg.write_text(
        'schema = "ascendo-web-apps/v2"\n', encoding="utf-8"
    )
    before = user_reg.read_text(encoding="utf-8")
    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("", "404"))
    res = web_config.apply_web_override("newapp", _RF_SNIPPET)
    assert res["ok"] is False
    assert "probe gate" in res["error"]
    assert user_reg.read_text(encoding="utf-8") == before  # untouched


def test_user_wins_by_bundle_id_and_preserves_others(
    user_reg, monkeypatch
) -> None:
    # Pre-existing user file: one unrelated app + an old version of the
    # same bundle_id we are about to upsert.
    user_reg.write_text(
        'schema = "ascendo-web-apps/v2"\n\n'
        '[[app]]\nslug = "keepme"\nbundle_id = "com.keep.Me"\n'
        'display_name = "Keep"\nhandler = "builtin"\n\n'
        '[[app]]\nslug = "oldname"\nbundle_id = "com.example.NewApp"\n'
        'display_name = "Old"\nhandler = "builtin"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("3.1.4", ""))
    res = web_config.apply_web_override("newapp", _RF_SNIPPET)
    assert res["ok"] is True
    from ascendo_macos.web_registry import WebRegistry

    reg = WebRegistry.load(user_reg, None)
    slugs = sorted(a.slug for a in reg.apps)
    assert "keepme" in slugs               # unrelated entry preserved
    assert "newapp" in slugs               # new entry written
    assert "oldname" not in slugs          # replaced by bundle_id (user-wins)
    assert reg.find_by_bundle_id("com.example.NewApp").slug == "newapp"


def test_builtin_skips_probe_gate_and_writes(user_reg, monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(
        web_config, "_probe_handler",
        lambda *_a: (called.__setitem__("n", called["n"] + 1), ("", ""))[1],
    )
    res = web_config.apply_web_override("builtinapp", _BUILTIN_SNIPPET)
    assert res["ok"] is True
    assert called["n"] == 0  # Tier-B: gate skipped, no probe
    assert user_reg.is_file()


def test_slug_mismatch_rejected(user_reg, monkeypatch) -> None:
    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("1", ""))
    res = web_config.apply_web_override("wrongslug", _RF_SNIPPET)
    assert res["ok"] is False
    assert "mismatch" in res["error"]
    assert not user_reg.is_file()


def test_route_web_override_end_to_end(user_reg, monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from ascendo.dashboard import create_app

    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("3.1.4", ""))
    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    with TestClient(app) as client:
        ok = client.post(
            "/ai/chat/action/web_override",
            json={"slug": "newapp", "toml_snippet": _RF_SNIPPET},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["ok"] is True
        # Probe-fail path → 422, file untouched.
        monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("", "x"))
        bad = client.post(
            "/ai/chat/action/web_override",
            json={"slug": "newapp", "toml_snippet": _RF_SNIPPET},
        )
        assert bad.status_code == 422
