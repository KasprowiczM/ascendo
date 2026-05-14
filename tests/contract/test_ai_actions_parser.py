"""Fence parser + ALLOWED_ACTIONS dispatcher tests."""
from __future__ import annotations
import json


def test_parse_extracts_single_action_fence():
    from ascendo.ai.actions import parse_actions
    text = """Run a check:
```ascendo-action
{"id":"run_check","label_en":"Run check","verb":"POST","path":"/runs/async","body":{"categories":["winget"],"phases":["check"]},"confirm":false,"risk":"low"}
```
"""
    actions, cleaned = parse_actions(text)
    assert len(actions) == 1
    assert actions[0]["id"] == "run_check"
    assert "ascendo-action" not in cleaned


def test_parse_extracts_multiple_action_fences():
    from ascendo.ai.actions import parse_actions
    text = """First:
```ascendo-action
{"id":"run_check","label_en":"A"}
```
Then:
```ascendo-action
{"id":"run_apply","label_en":"B"}
```
"""
    actions, _ = parse_actions(text)
    assert len(actions) == 2
    assert actions[0]["id"] == "run_check"
    assert actions[1]["id"] == "run_apply"


def test_parse_skips_invalid_json_fence():
    from ascendo.ai.actions import parse_actions
    text = """Bad:
```ascendo-action
{not valid json
```
"""
    actions, cleaned = parse_actions(text)
    assert actions == []
    assert "ascendo-action" not in cleaned


def test_parse_preserves_surrounding_prose():
    from ascendo.ai.actions import parse_actions
    text = """Your last run failed.

```ascendo-action
{"id":"run_check","label_en":"Run check"}
```

Try this instead."""
    actions, cleaned = parse_actions(text)
    assert "Your last run failed" in cleaned
    assert "Try this instead" in cleaned
    assert "ascendo-action" not in cleaned
    assert len(actions) == 1


def test_allowed_actions_has_12_entries():
    from ascendo.ai.actions import ALLOWED_ACTIONS
    assert len(ALLOWED_ACTIONS) == 12
    assert "run_check" in ALLOWED_ACTIONS
    assert "open_view" in ALLOWED_ACTIONS


def test_allowed_actions_shape():
    from ascendo.ai.actions import ALLOWED_ACTIONS
    verb, path, schema = ALLOWED_ACTIONS["run_check"]
    assert verb == "POST"
    assert path == "/runs/async"


def test_dispatch_unknown_action_returns_error():
    from ascendo.ai.actions import dispatch_action
    result = dispatch_action(action_id="not_a_real_action", body={})
    assert result["ok"] is False
    assert "unknown_action" in result["error"]


def test_dispatch_known_action_validates_body():
    from ascendo.ai.actions import dispatch_action
    result = dispatch_action(action_id="run_check", body={})
    assert result["ok"] is False
    assert "invalid_body" in result["error"]


def test_dispatch_run_check_returns_plan():
    from ascendo.ai.actions import dispatch_action
    result = dispatch_action(
        action_id="run_check",
        body={"categories": ["winget"], "phases": ["check"]},
    )
    assert result["ok"] is True
    assert result["verb"] == "POST"
    assert result["path"] == "/runs/async"
    assert result["body"]["categories"] == ["winget"]


def test_dispatch_install_schedule_validates_name_pattern():
    from ascendo.ai.actions import dispatch_action
    bad = dispatch_action(
        action_id="install_schedule",
        body={"name": "Has Spaces", "expression": "DAILY 03:00", "profile": "safe"},
    )
    assert bad["ok"] is False

    good = dispatch_action(
        action_id="install_schedule",
        body={"name": "ascendo-daily", "expression": "DAILY 03:00", "profile": "safe"},
    )
    assert good["ok"] is True


def test_dispatch_open_view_rejects_unknown_view():
    from ascendo.ai.actions import dispatch_action
    bad = dispatch_action(action_id="open_view", body={"view": "bogus"})
    assert bad["ok"] is False
    good = dispatch_action(action_id="open_view", body={"view": "categories"})
    assert good["ok"] is True


def test_dispatch_skip_list_supports_add_and_remove():
    from ascendo.ai.actions import dispatch_action
    r = dispatch_action(
        action_id="edit_skip_list",
        body={"adapter": "macos", "add": ["docker"], "remove": ["firefox"]},
    )
    assert r["ok"] is True
    assert r["body"]["add"] == ["docker"]


def test_run_apply_phase_validator_rejects_unknown_phase():
    from ascendo.ai.actions import dispatch_action
    r = dispatch_action(
        action_id="run_apply",
        body={"categories": ["winget"], "phases": ["bogus"]},
    )
    assert r["ok"] is False
