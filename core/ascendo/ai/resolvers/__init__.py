"""Resolver registry: context tag name -> callable returning (text, priority)."""
from __future__ import annotations
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Resolver(Protocol):
    def __call__(self, *, adapter, inventory_db, runs_dir) -> tuple[str, int]: ...


def get_registry() -> dict[str, Resolver]:
    """Return tag -> resolver mapping. Imported lazily to avoid cycles."""
    from . import (
        adapter_capabilities,
        churn_history_30d,
        doctor_full,
        latest_failed_sidecar,
        latest_report_md,
        outdated_apps,
        recent_apply_history,
        schedules_current,
        skip_list_current,
        web_registry_schema,
    )
    return {
        "doctor_full": doctor_full.resolve,
        "outdated_apps": outdated_apps.resolve,
        "adapter_capabilities": adapter_capabilities.resolve,
        "latest_failed_sidecar": latest_failed_sidecar.resolve,
        "latest_report_md": latest_report_md.resolve,
        "churn_history_30d": churn_history_30d.resolve,
        "skip_list_current": skip_list_current.resolve,
        "schedules_current": schedules_current.resolve,
        "web_registry_schema": web_registry_schema.resolve,
        "recent_apply_history": recent_apply_history.resolve,
    }
