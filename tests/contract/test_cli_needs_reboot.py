"""Tests for ascendo.cli._sidecars_need_reboot — both reboot-signal channels.

Channel 1 (canonical, M5.4+): top-level `Sidecar.needs_reboot` boolean flag.
Channel 2 (legacy Windows): "Reboot required" message-text scan.
"""
from __future__ import annotations

import json

from ascendo.cli import _sidecars_need_reboot
from ascendo.models.sidecar import parse_sidecar


def _base_sidecar_json() -> dict:
    """Minimal valid ascendo/v1 sidecar dict — easy to mutate per-test."""
    return {
        "schema": "ascendo/v1",
        "run": {
            "id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
            "trigger": "cli",
            "profile": "default",
            "dry_run": False,
            "started_at": "2026-05-04T12:00:00+00:00",
        },
        "host": {
            "hostname": "x",
            "os": "macos",
            "os_version": "14.5",
            "arch": "arm64",
            "user": "x",
            "is_elevated": False,
            "elevation_method": "none",
        },
        "tool": {"name": "test", "version": "1.0"},
        "phase": "apply",
        "category": "softwareupdate",
        "started_at": "2026-05-04T12:00:00+00:00",
        "finished_at": "2026-05-04T12:00:01+00:00",
        "status": "success",
        "items": [],
        "summary": {
            "total": 0, "success": 0, "failed": 0, "skipped": 0,
            "up_to_date": 0, "planned": 0, "partial": 0,
        },
        "messages": [],
        "needs_reboot": False,
    }


def test_top_level_needs_reboot_flag_returns_true():
    """Channel 1: sc.needs_reboot=True triggers True even with no message."""
    payload = _base_sidecar_json()
    payload["needs_reboot"] = True
    sc = parse_sidecar(json.dumps(payload))
    assert _sidecars_need_reboot([sc]) is True


def test_message_text_reboot_required_returns_true():
    """Channel 2 (legacy Windows): message-text 'Reboot required ...' triggers True."""
    payload = _base_sidecar_json()
    payload["messages"] = [
        {"level": "warn", "text": "Reboot required after winget upgrade"}
    ]
    sc = parse_sidecar(json.dumps(payload))
    assert _sidecars_need_reboot([sc]) is True


def test_neither_channel_returns_false():
    """Empty messages + needs_reboot=False → no reboot signal."""
    sc = parse_sidecar(json.dumps(_base_sidecar_json()))
    assert _sidecars_need_reboot([sc]) is False


def test_unrelated_message_does_not_trigger():
    """Messages without 'Reboot required' prefix are ignored."""
    payload = _base_sidecar_json()
    payload["messages"] = [
        {"level": "info", "text": "All packages up to date."}
    ]
    sc = parse_sidecar(json.dumps(payload))
    assert _sidecars_need_reboot([sc]) is False


def test_any_sidecar_with_flag_triggers_true_in_list():
    """Multi-sidecar list: even one True flag is enough."""
    a = parse_sidecar(json.dumps(_base_sidecar_json()))
    payload_b = _base_sidecar_json()
    payload_b["needs_reboot"] = True
    b = parse_sidecar(json.dumps(payload_b))
    c = parse_sidecar(json.dumps(_base_sidecar_json()))
    assert _sidecars_need_reboot([a, b, c]) is True
