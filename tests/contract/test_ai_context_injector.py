"""Context injector tests — base context + 10 resolvers."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import pytest


# ============================== Fakes ==============================

class FakeAdapter:
    def health_check(self) -> dict:
        return {
            "components": [
                {"name": "claude", "status": "ok", "message": "1.0.0"},
                {"name": "inventory_db", "status": "ok", "message": "451 rows"},
                {"name": "schedules", "status": "degraded", "message": "1 broken"},
            ]
        }

    @property
    def capabilities(self):
        return "PACKAGE_MANAGEMENT|INVENTORY|SNAPSHOTS|SCHEDULING|ELEVATION"

    def scheduler(self):
        return None


class FakeInventoryDB:
    def __init__(self, rows=None, totals=None):
        self._rows = rows if rows is not None else [
            {"category": "winget", "name": "VSCode", "installed": "1.119.1",
             "candidate": "1.120.0", "status": "outdated"},
            {"category": "msstore", "name": "Edge", "installed": "120.0",
             "candidate": "121.0", "status": "outdated"},
            {"category": "winget", "name": "Firefox", "installed": "120.0",
             "candidate": "120.0", "status": "up_to_date"},
        ]
        self._totals = totals if totals is not None else {"winget": 221, "msstore": 95, "npm": 14}

    def query(self, *, status=None):
        if status:
            return [r for r in self._rows if r["status"] == status]
        return self._rows

    def totals_by_category(self):
        return self._totals


# ============================== build_context ==============================

def test_base_context_includes_doctor_inventory_totals(tmp_path):
    from ascendo.ai.context import build_context
    ctx = build_context(
        message="What's outdated?",
        template_id=None,
        locale="en",
        adapter=FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None,
        conversation_id=None,
    )
    assert "<ascendo_context>" in ctx
    assert "</ascendo_context>" in ctx
    assert "Doctor" in ctx
    assert "winget" in ctx
    assert "Respond in English" in ctx


def test_base_context_pl_locale_includes_polish_hint(tmp_path):
    from ascendo.ai.context import build_context
    ctx = build_context(
        message="Co jest nieaktualne?",
        template_id=None,
        locale="pl",
        adapter=FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None,
        conversation_id=None,
    )
    assert "Polish" in ctx or "polsku" in ctx.lower()


def test_extra_tags_inject_resolver_output(tmp_path):
    from ascendo.ai.context import build_context
    ctx = build_context(
        message="Why?",
        template_id=None,
        locale="en",
        adapter=FakeAdapter(),
        runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None,
        conversation_id=None,
        extra_tags=["outdated_apps", "adapter_capabilities"],
    )
    assert "VSCode" in ctx
    assert "Edge" in ctx
    assert "PACKAGE_MANAGEMENT" in ctx


def test_resolver_registry_has_10_tags():
    from ascendo.ai.resolvers import get_registry
    keys = set(get_registry().keys())
    assert keys == {
        "doctor_full", "outdated_apps", "adapter_capabilities",
        "latest_failed_sidecar", "latest_report_md",
        "churn_history_30d", "skip_list_current",
        "schedules_current", "web_registry_schema", "recent_apply_history",
    }


def test_budget_truncates_huge_resolver(tmp_path, monkeypatch):
    """Inject a fake resolver that produces 20k tokens — should be truncated."""
    from ascendo.ai import resolvers as res_mod

    big_text = "x" * 80_000  # ~20k tokens
    def big_resolver(*, adapter, inventory_db, runs_dir):
        return big_text, 9

    real_get = res_mod.get_registry

    def patched():
        r = real_get()
        r["__big"] = big_resolver
        return r

    monkeypatch.setattr(res_mod, "get_registry", patched)
    from ascendo.ai.context import build_context, MAX_CONTEXT_TOKENS
    ctx = build_context(
        message="hi", template_id=None, locale="en",
        adapter=FakeAdapter(), runs_dir=tmp_path,
        inventory_db=FakeInventoryDB(),
        chats_db=None, conversation_id=None,
        extra_tags=["__big"],
    )
    assert len(ctx) // 4 < MAX_CONTEXT_TOKENS + 1000


# ============================== individual resolvers ==============================

def test_outdated_apps_resolver_returns_outdated_only(tmp_path):
    from ascendo.ai.resolvers.outdated_apps import resolve
    ctx, priority = resolve(
        adapter=FakeAdapter(), inventory_db=FakeInventoryDB(), runs_dir=tmp_path,
    )
    assert "VSCode" in ctx
    assert "Edge" in ctx
    assert "Firefox" not in ctx  # up_to_date, filtered
    assert priority > 0


def test_doctor_full_resolver_lists_components():
    from ascendo.ai.resolvers.doctor_full import resolve
    ctx, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=None)
    assert "claude" in ctx
    assert "inventory_db" in ctx
    assert "degraded" in ctx


def test_adapter_capabilities_resolver_returns_flag():
    from ascendo.ai.resolvers.adapter_capabilities import resolve
    ctx, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=None)
    assert "PACKAGE_MANAGEMENT" in ctx


def test_latest_failed_sidecar_finds_failed(tmp_path):
    run_dir = tmp_path / "abc-123"
    run_dir.mkdir()
    sidecar = run_dir / "apply__winget.json"
    sidecar.write_text(json.dumps({
        "schema": "ascendo/v1",
        "phase": "apply",
        "category": "winget",
        "status": "failed",
        "items": [
            {"name": "VSCode", "status": "failed",
             "messages": [{"level": "error", "text": "boom"}]},
        ],
    }))
    from ascendo.ai.resolvers.latest_failed_sidecar import resolve
    ctx, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=tmp_path)
    assert "VSCode" in ctx or "boom" in ctx


def test_latest_failed_sidecar_returns_empty_when_no_failed(tmp_path):
    from ascendo.ai.resolvers.latest_failed_sidecar import resolve
    text, _ = resolve(adapter=None, inventory_db=None, runs_dir=tmp_path)
    assert text == ""


def test_latest_report_md_finds_most_recent(tmp_path):
    run_dir = tmp_path / "abc-123"
    run_dir.mkdir()
    (run_dir / "REPORT.md").write_text("# Report\n3 upgraded, 0 failed.")
    from ascendo.ai.resolvers.latest_report_md import resolve
    text, _ = resolve(adapter=None, inventory_db=None, runs_dir=tmp_path)
    assert "3 upgraded" in text


def test_web_registry_schema_describes_toml():
    from ascendo.ai.resolvers.web_registry_schema import resolve
    text, _ = resolve(adapter=None, inventory_db=None, runs_dir=None)
    assert "web_apps.toml" in text or "schema = 2" in text


def test_skip_list_no_file_returns_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("HOME", str(tmp_path / "alsonope"))
    from ascendo.ai.resolvers.skip_list_current import resolve
    text, _ = resolve(adapter=None, inventory_db=None, runs_dir=None)
    assert "Skip list" in text


def test_churn_history_returns_empty_without_table(tmp_path):
    """If update_history table doesn't exist (older deployments), gracefully empty."""
    from ascendo.ai.resolvers.churn_history_30d import resolve

    class StubDB:
        def _connect(self):
            return sqlite3.connect(":memory:")

    text, priority = resolve(adapter=None, inventory_db=StubDB(), runs_dir=None)
    # Missing table -> empty string, priority 0
    assert text == ""
    assert priority == 0


def test_schedules_current_no_scheduler_returns_note():
    from ascendo.ai.resolvers.schedules_current import resolve
    text, _ = resolve(adapter=FakeAdapter(), inventory_db=None, runs_dir=None)
    assert "Schedules" in text


def test_recent_apply_history_empty_table(tmp_path):
    from ascendo.ai.resolvers.recent_apply_history import resolve

    class StubDB:
        def _connect(self):
            c = sqlite3.connect(":memory:")
            c.execute(
                "CREATE TABLE update_history (category TEXT, name TEXT, "
                "from_version TEXT, to_version TEXT, status TEXT, applied_at TEXT)"
            )
            return c

    text, _ = resolve(adapter=None, inventory_db=StubDB(), runs_dir=None)
    assert "Recent apply history" in text
    assert "empty" in text
