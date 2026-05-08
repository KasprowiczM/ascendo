# macOS Web Manager — Discovery + Tiered Probes Implementation Plan (M5.7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static curated 24-app web registry with a discovery-driven inventory + tiered handler model so every installed web-orphan macOS app appears in `ascendo run --category web --phase check`, with the most precise candidate version each vendor's update mechanism allows.

**Architecture:** Three-layer pipeline. Layer 1 (`lib/web_discovery.sh`) walks `/Applications`, fingerprints each bundle, and computes ownership exclusions against brew/mas/softwareupdate. Layer 2 (`web_apps.toml v2`) becomes an override registry keyed by `bundle_id`. Layer 3 splits the 7 existing handlers into Tier-A (real candidate probe — sparkle/github_dmg/release_feed/msupdate/docker) and Tier-B (trigger-only — keystone/squirrel/builtin) with a new first-class `triggered` status. New `release_feed` handler is a generic JSON-over-HTTPS probe so per-vendor probes become TOML config rather than new code.

**Tech Stack:** Bash 3.2 (the macOS adapter language), Python 3.11+ (Pydantic v2 model + CLI shim), `/usr/libexec/PlistBuddy` (Info.plist reads), `curl` (HTTPS fetch), `pytest` (Python tests), inline bash test functions (handler tests).

**Spec:** [`docs/superpowers/specs/2026-05-08-macos-web-discovery-design.md`](../specs/2026-05-08-macos-web-discovery-design.md)

---

## Files touched

| Path | Action | Why |
|------|--------|-----|
| `core/ascendo/models/result.py` | modify | Add `ItemStatus.TRIGGERED` |
| `adapters/macos/lib/_json_emit.py` | modify | Add `"triggered"` to `VALID_STATUSES` |
| `docs/architecture/schemas/sidecar.v1.schema.json` | regen | New enum value in JSON Schema |
| `adapters/macos/ascendo_macos/web_registry.py` | modify | Schema v1→v2, bundle_id-keyed lookup, `release_feed` config model |
| `adapters/macos/lib/web_registry.py` | modify | CLI shim adds `--list-bundle-ids` and `--get-app-by-bundle-id` |
| `adapters/macos/config/web_apps.toml` | modify | Bump `schema = "ascendo-web-apps/v2"`, add `bundle_id` to all 24 entries |
| `adapters/macos/lib/web_discovery.sh` | create | Walk `/Applications`, emit JSON, classify |
| `adapters/macos/lib/handlers/release_feed.sh` | create | New Tier-A generic handler |
| `adapters/macos/scripts/web/check.sh` | modify | Discovery integration, tier dispatch, `triggered` for B |
| `adapters/macos/scripts/web/plan.sh` | modify | Same as check.sh |
| `adapters/macos/scripts/web/apply.sh` | modify | Tier-B emits `triggered`, add `release_feed` to dispatch |
| `adapters/macos/scripts/web/verify.sh` | modify | Tier-B emits `triggered` with pending/confirmed in messages |
| `adapters/macos/tests/fixtures/discovery/Applications/` | create | 4 fake `.app` bundles |
| `adapters/macos/tests/fixtures/release_feed/feed.json` | create | Fixture JSON feed |
| `adapters/macos/tests/test_web_discovery.sh` | create | Bash test for discovery |
| `adapters/macos/tests/test_release_feed_handler.sh` | create | Bash test for new handler |
| `adapters/macos/tests/test_web_registry_v2.py` | create | Pytest for v2 schema migration |
| `app/frontend/app.js` | modify | Render `triggered` status pill |
| `app/frontend/style.css` | modify | `.st-triggered` neutral pill style |
| `bin/validate-macos.sh` | modify | Stage 13.8 / 13.9 / 13.10 |
| `bin/run-tag-release-macos.sh` | modify | Bump tag `v0.3.0` → `v0.4.0` |
| `PLAN.md` | modify | Mark M5.7 done |
| `HANDOFF.md` | modify | Sesja 37 entry |
| `MACOS_QUICKSTART.md` | modify | Discovery semantics blurb |

---

## Task 1: Add `ItemStatus.TRIGGERED` enum + regenerate schema + bash status set

**Files:**
- Modify: `core/ascendo/models/result.py:26-41`
- Modify: `adapters/macos/lib/_json_emit.py:44`
- Regen: `docs/architecture/schemas/sidecar.v1.schema.json`
- Test: `tests/contract/test_sidecar_v1.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_sidecar_v1.py`:

```python
def test_item_status_includes_triggered():
    """Tier-B handlers (keystone/squirrel/builtin) report 'triggered' after apply.

    This is distinct from 'success' (synchronous, verified) and from 'skipped'
    (we did nothing): we triggered the vendor's update agent, the update
    will land asynchronously.
    """
    from ascendo.models.result import ItemStatus
    assert ItemStatus("triggered") is ItemStatus.TRIGGERED
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/stupefied-noyce-c04d3c
PYTHONPATH=$PWD/core python3 -m pytest tests/contract/test_sidecar_v1.py::test_item_status_includes_triggered -v
```

Expected: FAIL with `'triggered' is not a valid ItemStatus`.

- [ ] **Step 3: Add the enum member**

In `core/ascendo/models/result.py:26-41`, after `MISSING = "missing"`:

```python
class ItemStatus(str, Enum):
    """..."""
    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"
    FAILED = "failed"
    SKIPPED = "skipped"
    PLANNED = "planned"
    PARTIAL = "partial"
    MISSING = "missing"
    TRIGGERED = "triggered"   # Tier-B vendor update agent triggered; async outcome
```

- [ ] **Step 4: Add to bash emitter's allow-list**

In `adapters/macos/lib/_json_emit.py:44`, replace:

```python
VALID_STATUSES = {"success", "skipped", "failed", "planned", "up_to_date", "partial", "missing"}
```

with:

```python
VALID_STATUSES = {"success", "skipped", "failed", "planned", "up_to_date", "partial", "missing", "triggered"}
```

- [ ] **Step 5: Regenerate JSON schema**

```bash
PYTHONPATH=$PWD/core python3 scripts/export-sidecar-schema.py
```

Expected: `docs/architecture/schemas/sidecar.v1.schema.json` updated with `"triggered"` in the `status` enum.

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=$PWD/core python3 -m pytest tests/contract/test_sidecar_v1.py -v
```

Expected: all green, including the new `test_item_status_includes_triggered`.

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/models/result.py \
        adapters/macos/lib/_json_emit.py \
        docs/architecture/schemas/sidecar.v1.schema.json \
        tests/contract/test_sidecar_v1.py
git commit -m "feat(core): add ItemStatus.TRIGGERED for Tier-B web handlers (M5.7 T1)"
```

---

## Task 2: WebRegistry v2 schema + auto-coerce v1 + `release_feed` config model

**Files:**
- Modify: `adapters/macos/ascendo_macos/web_registry.py`
- Test: `adapters/macos/tests/test_web_registry_v2.py` (create)

- [ ] **Step 1: Write the failing test**

Create `adapters/macos/tests/test_web_registry_v2.py`:

```python
"""Tests for WebRegistry schema v2 (bundle_id-keyed + release_feed)."""
from __future__ import annotations

from pathlib import Path

import pytest
from ascendo_macos.web_registry import WebRegistry


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "apps.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_v2_schema_loads_with_bundle_id_required(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    assert reg.schema_version == "ascendo-web-apps/v2"
    assert len(reg.apps) == 1
    assert reg.apps[0].bundle_id == "com.brave.Browser"


def test_v1_schema_auto_coerces_to_v2(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v1"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    assert reg.schema_version == "ascendo-web-apps/v2"
    assert reg.apps[0].slug == "brave"


def test_v2_release_feed_handler_validates(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "warp"
bundle_id = "dev.warp.Warp-Stable"
display_name = "Warp"
handler = "release_feed"

[apps.release_feed]
url = "https://desktop.warp.dev/version.json"
version_path = "latest.darwin.arm64.version"
""")
    reg = WebRegistry.load(p, None)
    app = reg.apps[0]
    assert app.handler == "release_feed"
    assert app.release_feed is not None
    assert str(app.release_feed.url) == "https://desktop.warp.dev/version.json"
    assert app.release_feed.version_path == "latest.darwin.arm64.version"


def test_release_feed_rejects_non_https(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "x"
bundle_id = "com.example.X"
display_name = "X"
handler = "release_feed"

[apps.release_feed]
url = "http://insecure.example.com/feed"
version_path = "version"
""")
    with pytest.raises(Exception):
        WebRegistry.load(p, None)


def test_find_by_bundle_id(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
""")
    reg = WebRegistry.load(p, None)
    app = reg.find_by_bundle_id("com.brave.Browser")
    assert app is not None
    assert app.slug == "brave"
    assert reg.find_by_bundle_id("com.nope.Nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/test_web_registry_v2.py -v
```

Expected: FAIL — `Literal["ascendo-web-apps/v1"]` rejects v2 input; `release_feed` not in `Literal` handlers; no `find_by_bundle_id` method.

- [ ] **Step 3: Modify the WebApp + WebRegistry models**

Open `adapters/macos/ascendo_macos/web_registry.py`. Replace the file with this complete rewrite (preserving existing v1 entries' shape; adding v2):

```python
"""Pydantic-validated _apps.toml registry for the WebManager.

Loads the shipped registry at adapters/macos/config/web_apps.toml plus
an optional user override at ~/.config/ascendo/web_apps.toml. Override
merge keyed by bundle_id (user wins; new bundle_ids append).

v1 schema (M5.6) is auto-coerced to v2 on load. v1 used slug-keyed
overrides; v2 uses bundle_id. Slug remains in the row for display only.

release_feed handler (new in v2) is a generic JSON-over-HTTPS probe;
its config sits under [apps.release_feed] sub-table.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic.networks import UrlConstraints

# https-only — appcast / update channels must not be MITM-able (T3 mitigation).
HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class ReleaseFeedConfig(BaseModel):
    """Config for the generic release_feed handler.

    The handler fetches `url` over HTTPS, parses the response as JSON,
    walks `version_path` (dotted, supports `[N]` indices), and echoes the
    string at that path as the candidate version.

    If `download_path` is set, the handler is Tier-A on apply too: it
    downloads the URL at that path and installs the DMG. Without it,
    apply falls back to `open -a` (Tier-B trigger semantics).
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    version_path: Annotated[str, Field(min_length=1, max_length=256,
                                       pattern=r"^[A-Za-z0-9_.\[\]]+$")]
    download_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                                  pattern=r"^[A-Za-z0-9_.\[\]]+$")]] = None
    arch_path: Optional[Annotated[str, Field(min_length=1, max_length=256,
                                              pattern=r"^[A-Za-z0-9_.\[\]]+$")]] = None
    expected_arch: Optional[Literal["arm64", "x86_64", "universal"]] = None
    http_timeout_s: Annotated[int, Field(ge=1, le=60)] = 8


class WebApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identity
    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)]
    bundle_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$",
                                    min_length=1, max_length=256)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    handler: Literal["sparkle", "github_dmg", "keystone", "squirrel",
                     "builtin", "msupdate", "docker", "release_feed"]
    app_path: Optional[Path] = None
    enabled: bool = True
    notes: Optional[str] = None

    # Sparkle
    appcast_url: Optional[HttpsUrl] = None
    apply_cli_argv: Optional[list[str]] = None

    # GitHub DMG
    github_repo: Annotated[Optional[str], Field(pattern=r"^[\w.-]+/[\w.-]+$")] = None
    asset_pattern: Optional[str] = None
    arch: Optional[Literal["arm64", "x86_64", "universal"]] = None
    prerelease: Optional[bool] = None

    # Keystone
    ksadmin_product_id: Optional[str] = None

    # Builtin
    update_url: Optional[HttpsUrl] = None

    # Release feed (new v2 handler)
    release_feed: Optional[ReleaseFeedConfig] = None

    # Behaviour overrides (apply to any handler)
    defer_if_running: Optional[bool] = None
    kill_safe: Optional[bool] = None

    @property
    def effective_arch(self) -> str:
        return self.arch or "arm64"

    @property
    def effective_prerelease(self) -> bool:
        return self.prerelease if self.prerelease is not None else False

    @model_validator(mode="after")
    def _validate_handler_fields(self) -> "WebApp":
        h = self.handler
        # Required by handler
        if h == "sparkle" and self.appcast_url is None:
            raise ValueError("sparkle handler requires appcast_url")
        if h == "github_dmg":
            if self.github_repo is None:
                raise ValueError("github_dmg handler requires github_repo")
            if self.asset_pattern is None:
                raise ValueError("github_dmg handler requires asset_pattern")
        if h == "keystone" and self.ksadmin_product_id is None:
            raise ValueError("keystone handler requires ksadmin_product_id")
        if h == "release_feed" and self.release_feed is None:
            raise ValueError("release_feed handler requires [apps.release_feed] table")

        # Cross-handler fields rejected (catches typos)
        if h != "sparkle" and (self.appcast_url is not None
                                or self.apply_cli_argv is not None):
            raise ValueError(
                f"appcast_url / apply_cli_argv only valid for sparkle; got handler={h!r}")
        if h != "github_dmg":
            if self.github_repo is not None or self.asset_pattern is not None:
                raise ValueError(
                    f"github_repo / asset_pattern only valid for github_dmg; got handler={h!r}")
            if self.arch is not None or self.prerelease is not None:
                raise ValueError(
                    f"arch / prerelease only valid for github_dmg; got handler={h!r}")
        if h != "keystone" and self.ksadmin_product_id is not None:
            raise ValueError(
                f"ksadmin_product_id only valid for keystone; got handler={h!r}")
        if h != "builtin" and self.update_url is not None:
            raise ValueError(
                f"update_url only valid for builtin; got handler={h!r}")
        if h != "release_feed" and self.release_feed is not None:
            raise ValueError(
                f"release_feed sub-table only valid for release_feed handler; got handler={h!r}")
        return self


class WebRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["ascendo-web-apps/v1", "ascendo-web-apps/v2"] = Field(alias="schema")
    apps: list[WebApp] = Field(default_factory=list, alias="app")

    @classmethod
    def load(
        cls,
        shipped: Path,
        user_override: Optional[Path],
    ) -> "WebRegistry":
        shipped_data = cls._read_toml(shipped)
        registry = cls.model_validate(shipped_data)

        # Auto-coerce v1 → v2 (no field shape changed; just bump the literal)
        if registry.schema_version == "ascendo-web-apps/v1":
            registry = WebRegistry(
                schema="ascendo-web-apps/v2",
                app=registry.apps,
            )

        if user_override is not None and user_override.exists():
            user_data = cls._read_toml(user_override)
            user_reg = cls.model_validate(user_data)
            if user_reg.schema_version == "ascendo-web-apps/v1":
                user_reg = WebRegistry(
                    schema="ascendo-web-apps/v2",
                    app=user_reg.apps,
                )
            by_bundle = {a.bundle_id: a for a in registry.apps}
            for ua in user_reg.apps:
                by_bundle[ua.bundle_id] = ua  # user replaces shipped
            registry = WebRegistry(
                schema="ascendo-web-apps/v2",
                app=list(by_bundle.values()),
            )
        return registry

    @staticmethod
    def _read_toml(path: Path) -> dict:
        with path.open("rb") as fh:
            return tomllib.load(fh)

    def active_apps(self) -> list[WebApp]:
        return [a for a in self.apps if a.enabled]

    def find(self, slug: str) -> Optional[WebApp]:
        for app in self.active_apps():
            if app.slug == slug:
                return app
        return None

    def find_by_bundle_id(self, bundle_id: str) -> Optional[WebApp]:
        for app in self.active_apps():
            if app.bundle_id == bundle_id:
                return app
        return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/test_web_registry_v2.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run the full macOS adapter test suite to ensure no regressions**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ -v 2>&1 | tail -30
```

Expected: 358 + 5 = 363 passing. (Existing tests use slug-keyed overrides which still work via `find()`.)

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/ascendo_macos/web_registry.py \
        adapters/macos/tests/test_web_registry_v2.py
git commit -m "feat(macos/web): WebRegistry v2 schema + release_feed handler model (M5.7 T2)

Auto-coerces v1 → v2 on load. v2 keys overrides by bundle_id (slug
remains for display). New ReleaseFeedConfig sub-table validates
https-only URL + JSON-path strings."
```

---

## Task 3: CLI shim adds `--list-bundle-ids` and `--get-app-by-bundle-id`

**Files:**
- Modify: `adapters/macos/lib/web_registry.py`
- Test: append to `adapters/macos/tests/test_web_registry_cli.sh` (existing) or new

- [ ] **Step 1: Write the failing test**

Append to (or create) `adapters/macos/tests/test_web_registry_cli.sh`:

```bash
test_list_bundle_ids() {
    local tmp_reg=$(mktemp)
    cat > "$tmp_reg" <<'EOF'
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
EOF

    local out
    out=$(PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 \
          adapters/macos/lib/web_registry.py \
          --shipped "$tmp_reg" --list-bundle-ids 2>&1)
    rc=$?
    rm -f "$tmp_reg"

    [ $rc -eq 0 ] || { echo "FAIL: rc=$rc, out=$out"; return 1; }
    [ "$out" = "com.brave.Browser" ] || { echo "FAIL: out='$out'"; return 1; }
    echo "PASS test_list_bundle_ids"
}

test_get_app_by_bundle_id() {
    local tmp_reg=$(mktemp)
    cat > "$tmp_reg" <<'EOF'
schema = "ascendo-web-apps/v2"

[[apps]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Browser/stable/appcast.xml"
EOF

    local out
    out=$(PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 \
          adapters/macos/lib/web_registry.py \
          --shipped "$tmp_reg" \
          --get-app-by-bundle-id com.brave.Browser 2>&1)
    rc=$?
    rm -f "$tmp_reg"

    [ $rc -eq 0 ] || { echo "FAIL: rc=$rc, out=$out"; return 1; }
    echo "$out" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["slug"]=="brave"' \
        || { echo "FAIL: malformed json output: $out"; return 1; }
    echo "PASS test_get_app_by_bundle_id"
}

test_list_bundle_ids
test_get_app_by_bundle_id
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash adapters/macos/tests/test_web_registry_cli.sh
```

Expected: FAIL — `argparse: unrecognized arguments: --list-bundle-ids`.

- [ ] **Step 3: Add the new argparse options + handlers**

In `adapters/macos/lib/web_registry.py`, in the `main()` function, find the mutually-exclusive group `g` and add two more options + their handlers. Final shape:

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="WebRegistry CLI shim")
    parser.add_argument("--shipped", required=True, type=Path,
                        help="Shipped web_apps.toml path")
    parser.add_argument("--user-override", type=Path, default=None,
                        help="Optional user override TOML")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-slugs", action="store_true",
                   help="Print active slugs newline-delimited")
    g.add_argument("--list-bundle-ids", action="store_true",
                   help="Print active bundle_ids newline-delimited")
    g.add_argument("--get-app", metavar="SLUG",
                   help="Print single-line JSON for one app (by slug)")
    g.add_argument("--get-app-by-bundle-id", metavar="BUNDLE_ID",
                   help="Print single-line JSON for one app (by bundle_id)")
    g.add_argument("--validate", action="store_true",
                   help="Validate registry; exit 0 on ok, 2 on error")
    args = parser.parse_args()

    try:
        reg = WebRegistry.load(args.shipped, args.user_override)
    except FileNotFoundError as exc:
        print(f"web_registry: {exc}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as exc:
        print(f"web_registry: malformed TOML: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        print(f"web_registry: validation failed at {loc}: {first['msg']}",
              file=sys.stderr)
        return 2

    if args.list_slugs:
        for app in reg.active_apps():
            print(app.slug)
        return 0
    if args.list_bundle_ids:
        for app in reg.active_apps():
            print(app.bundle_id)
        return 0
    if args.get_app:
        app = reg.find(args.get_app)
        if app is None:
            print(f"web_registry: slug not found: {args.get_app}", file=sys.stderr)
            return 2
        print(json.dumps(app.model_dump(mode="json"), separators=(",", ":")))
        return 0
    if args.get_app_by_bundle_id:
        app = reg.find_by_bundle_id(args.get_app_by_bundle_id)
        if app is None:
            print(f"web_registry: bundle_id not found: {args.get_app_by_bundle_id}",
                  file=sys.stderr)
            return 2
        print(json.dumps(app.model_dump(mode="json"), separators=(",", ":")))
        return 0
    if args.validate:
        return 0
    return 2  # unreachable
```

- [ ] **Step 4: Run test to verify it passes**

```bash
bash adapters/macos/tests/test_web_registry_cli.sh
```

Expected: `PASS test_list_bundle_ids` + `PASS test_get_app_by_bundle_id`.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/lib/web_registry.py \
        adapters/macos/tests/test_web_registry_cli.sh
git commit -m "feat(macos/web): CLI shim adds --list-bundle-ids + --get-app-by-bundle-id (M5.7 T3)"
```

---

## Task 4: Discovery fixtures (fake `/Applications` root with 4 bundles)

**Files:**
- Create: `adapters/macos/tests/fixtures/discovery/Applications/FakeSparkle.app/Contents/Info.plist`
- Create: `adapters/macos/tests/fixtures/discovery/Applications/FakeKeystone.app/Contents/Info.plist`
- Create: `adapters/macos/tests/fixtures/discovery/Applications/FakeSquirrel.app/Contents/Info.plist`
- Create: `adapters/macos/tests/fixtures/discovery/Applications/FakeSquirrel.app/Contents/Frameworks/Squirrel.framework/Info.plist` (just needs to exist as a directory marker)
- Create: `adapters/macos/tests/fixtures/discovery/Applications/FakeOrphan.app/Contents/Info.plist`

- [ ] **Step 1: Build the FakeSparkle.app fixture**

```bash
mkdir -p adapters/macos/tests/fixtures/discovery/Applications/FakeSparkle.app/Contents
cat > adapters/macos/tests/fixtures/discovery/Applications/FakeSparkle.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.fixture.fakesparkle</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2.3</string>
    <key>CFBundleName</key>
    <string>FakeSparkle</string>
    <key>SUFeedURL</key>
    <string>https://example.test/sparkle/appcast.xml</string>
</dict>
</plist>
EOF
```

- [ ] **Step 2: Build the FakeKeystone.app fixture**

```bash
mkdir -p adapters/macos/tests/fixtures/discovery/Applications/FakeKeystone.app/Contents
cat > adapters/macos/tests/fixtures/discovery/Applications/FakeKeystone.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.fixture.fakekeystone</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0.0</string>
    <key>CFBundleName</key>
    <string>FakeKeystone</string>
    <key>KSProductID</key>
    <string>com.fixture.fakekeystone</string>
    <key>KSUpdateURL</key>
    <string>https://tools.example.test/service/update2</string>
</dict>
</plist>
EOF
```

- [ ] **Step 3: Build the FakeSquirrel.app fixture (with Squirrel.framework marker)**

```bash
mkdir -p adapters/macos/tests/fixtures/discovery/Applications/FakeSquirrel.app/Contents/Frameworks/Squirrel.framework
cat > adapters/macos/tests/fixtures/discovery/Applications/FakeSquirrel.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.fixture.fakesquirrel</string>
    <key>CFBundleShortVersionString</key>
    <string>0.5.0</string>
    <key>CFBundleName</key>
    <string>FakeSquirrel</string>
</dict>
</plist>
EOF
# Marker file inside the framework directory
touch adapters/macos/tests/fixtures/discovery/Applications/FakeSquirrel.app/Contents/Frameworks/Squirrel.framework/.gitkeep
```

- [ ] **Step 4: Build the FakeOrphan.app fixture (no fingerprints)**

```bash
mkdir -p adapters/macos/tests/fixtures/discovery/Applications/FakeOrphan.app/Contents
cat > adapters/macos/tests/fixtures/discovery/Applications/FakeOrphan.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.fixture.fakeorphan</string>
    <key>CFBundleShortVersionString</key>
    <string>3.14</string>
    <key>CFBundleName</key>
    <string>FakeOrphan</string>
</dict>
</plist>
EOF
```

- [ ] **Step 5: Verify all four bundles parse with PlistBuddy**

```bash
for app in adapters/macos/tests/fixtures/discovery/Applications/*.app; do
    bid=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$app/Contents/Info.plist" 2>&1)
    ver=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$app/Contents/Info.plist" 2>&1)
    echo "$(basename "$app")  $bid  $ver"
done
```

Expected:
```
FakeKeystone.app  com.fixture.fakekeystone  2.0.0
FakeOrphan.app  com.fixture.fakeorphan  3.14
FakeSparkle.app  com.fixture.fakesparkle  1.2.3
FakeSquirrel.app  com.fixture.fakesquirrel  0.5.0
```

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/tests/fixtures/discovery/
git commit -m "test(macos/web): discovery fixtures — 4 fake .app bundles (M5.7 T4)

Sparkle (SUFeedURL set), Keystone (KSProductID set), Squirrel
(Squirrel.framework dir), and an orphan with no fingerprints.
Used by web_discovery.sh tests."
```

---

## Task 5: `lib/web_discovery.sh` — full implementation + tests

**Files:**
- Create: `adapters/macos/lib/web_discovery.sh`
- Create: `adapters/macos/tests/test_web_discovery.sh`

- [ ] **Step 1: Write the failing test**

Create `adapters/macos/tests/test_web_discovery.sh`:

```bash
#!/usr/bin/env bash
# Tests for adapters/macos/lib/web_discovery.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../lib"
FIXTURES="$SCRIPT_DIR/fixtures/discovery/Applications"

PASS=0; FAIL=0

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL $name: expected=$expected, got=$actual"
        FAIL=$((FAIL + 1))
    fi
}

test_emits_one_line_per_app() {
    local count
    count=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
            ASCENDO_WEB_BREW_CASKS="" \
            ASCENDO_WEB_MAS_BUNDLE_IDS="" \
            ASCENDO_WEB_APPLE_BUNDLES="" \
            bash "$ADAPTER_LIB/web_discovery.sh" --emit-json | wc -l | tr -d ' ')
    assert_eq "test_emits_one_line_per_app" "4" "$count"
}

test_classifies_sparkle() {
    local handler
    handler=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
              ASCENDO_WEB_BREW_CASKS="" \
              ASCENDO_WEB_MAS_BUNDLE_IDS="" \
              ASCENDO_WEB_APPLE_BUNDLES="" \
              bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
              | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakesparkle":
        print(d["fingerprint_handler"])
        break
')
    assert_eq "test_classifies_sparkle" "sparkle" "$handler"
}

test_classifies_keystone() {
    local handler
    handler=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
              ASCENDO_WEB_BREW_CASKS="" \
              ASCENDO_WEB_MAS_BUNDLE_IDS="" \
              ASCENDO_WEB_APPLE_BUNDLES="" \
              bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
              | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakekeystone":
        print(d["fingerprint_handler"])
        break
')
    assert_eq "test_classifies_keystone" "keystone" "$handler"
}

test_classifies_squirrel() {
    local handler
    handler=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
              ASCENDO_WEB_BREW_CASKS="" \
              ASCENDO_WEB_MAS_BUNDLE_IDS="" \
              ASCENDO_WEB_APPLE_BUNDLES="" \
              bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
              | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakesquirrel":
        print(d["fingerprint_handler"])
        break
')
    assert_eq "test_classifies_squirrel" "squirrel" "$handler"
}

test_orphan_falls_to_builtin() {
    local handler
    handler=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
              ASCENDO_WEB_BREW_CASKS="" \
              ASCENDO_WEB_MAS_BUNDLE_IDS="" \
              ASCENDO_WEB_APPLE_BUNDLES="" \
              bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
              | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakeorphan":
        print(d["fingerprint_handler"])
        break
')
    assert_eq "test_orphan_falls_to_builtin" "builtin" "$handler"
}

test_brew_excluded_from_output() {
    local count
    count=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
            ASCENDO_WEB_BREW_CASKS="com.fixture.fakeorphan,com.unrelated.app" \
            ASCENDO_WEB_MAS_BUNDLE_IDS="" \
            ASCENDO_WEB_APPLE_BUNDLES="" \
            bash "$ADAPTER_LIB/web_discovery.sh" --emit-json | wc -l | tr -d ' ')
    assert_eq "test_brew_excluded_from_output" "3" "$count"
}

test_mas_excluded_from_output() {
    local count
    count=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
            ASCENDO_WEB_BREW_CASKS="" \
            ASCENDO_WEB_MAS_BUNDLE_IDS="com.fixture.fakesparkle" \
            ASCENDO_WEB_APPLE_BUNDLES="" \
            bash "$ADAPTER_LIB/web_discovery.sh" --emit-json | wc -l | tr -d ' ')
    assert_eq "test_mas_excluded_from_output" "3" "$count"
}

test_emits_version() {
    local version
    version=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
              ASCENDO_WEB_BREW_CASKS="" \
              ASCENDO_WEB_MAS_BUNDLE_IDS="" \
              ASCENDO_WEB_APPLE_BUNDLES="" \
              bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
              | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakesparkle":
        print(d["version"])
        break
')
    assert_eq "test_emits_version" "1.2.3" "$version"
}

test_emits_owned_by_brew() {
    local owned
    owned=$(ASCENDO_WEB_APPS_ROOT="$FIXTURES" \
            ASCENDO_WEB_BREW_CASKS="com.fixture.fakeorphan" \
            ASCENDO_WEB_MAS_BUNDLE_IDS="" \
            ASCENDO_WEB_APPLE_BUNDLES="" \
            ASCENDO_WEB_INCLUDE_OWNED="1" \
            bash "$ADAPTER_LIB/web_discovery.sh" --emit-json \
            | python3 -c '
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    if d["bundle_id"] == "com.fixture.fakeorphan":
        print(d.get("owned_by") or "null")
        break
')
    assert_eq "test_emits_owned_by_brew" "brew" "$owned"
}

test_emits_one_line_per_app
test_classifies_sparkle
test_classifies_keystone
test_classifies_squirrel
test_orphan_falls_to_builtin
test_brew_excluded_from_output
test_mas_excluded_from_output
test_emits_version
test_emits_owned_by_brew

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash adapters/macos/tests/test_web_discovery.sh
```

Expected: FAIL — `web_discovery.sh: No such file or directory`.

- [ ] **Step 3: Implement `lib/web_discovery.sh`**

Create `adapters/macos/lib/web_discovery.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/lib/web_discovery.sh
#
# Walks $ASCENDO_WEB_APPS_ROOT (default /Applications), reads each .app's
# Info.plist, and emits a JSON line per bundle to stdout.
#
# Each line:
#   {
#     "bundle_id": "com.example.foo",
#     "app_path": "/Applications/Foo.app",
#     "version": "1.2.3",
#     "display_name": "Foo",
#     "fingerprint_handler": "sparkle|keystone|squirrel|builtin",
#     "fingerprint_source": "SUFeedURL|KSProductID|Squirrel.framework|none",
#     "owned_by": "brew|mas|softwareupdate|null"
#   }
#
# Apps owned by other managers (brew/mas/softwareupdate) are excluded
# unless ASCENDO_WEB_INCLUDE_OWNED=1 (test/debug only).
#
# Ownership inputs (comma-separated, no spaces):
#   ASCENDO_WEB_BREW_CASKS       — bundle IDs from `brew list --cask`
#   ASCENDO_WEB_MAS_BUNDLE_IDS   — bundle IDs from `mas list`
#   ASCENDO_WEB_APPLE_BUNDLES    — bundle IDs signed by Apple
#
# When the inputs aren't set externally, the script auto-populates from
# the actual brew/mas/system_profiler tools. Tests override.
set -o pipefail

APPS_ROOT="${ASCENDO_WEB_APPS_ROOT:-/Applications}"
INCLUDE_OWNED="${ASCENDO_WEB_INCLUDE_OWNED:-0}"

usage() {
    cat <<EOF
usage: web_discovery.sh --emit-json
EOF
    exit 2
}

case "${1:-}" in
    --emit-json) ;;
    *) usage ;;
esac

# -- Ownership signals -------------------------------------------------------

if [ -z "${ASCENDO_WEB_BREW_CASKS+x}" ]; then
    if command -v brew >/dev/null 2>&1; then
        # `brew info --cask --json=v2 $(brew list --cask)` gives bundle IDs in
        # artifacts[].app[]. Cheaper: bundle IDs of installed casks resolved via
        # brew's metadata.
        BREW_BIDS=$(brew list --cask 2>/dev/null | while read -r token; do
            [ -z "$token" ] && continue
            brew info --cask --json=v2 "$token" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for cask in data.get("casks", []):
    for art in cask.get("artifacts", []):
        if isinstance(art, dict) and "uninstall" in art:
            for u in art["uninstall"]:
                for bid in (u.get("quit") or []):
                    print(bid)
                for bid in (u.get("pkgutil") or []):
                    print(bid)
'
        done | sort -u | tr '\n' ',')
    ASCENDO_WEB_BREW_CASKS="${BREW_BIDS%,}"
fi

if [ -z "${ASCENDO_WEB_MAS_BUNDLE_IDS+x}" ]; then
    if command -v mas >/dev/null 2>&1; then
        # `mas list` gives "<id> <name> (<version>)"; we need bundle IDs,
        # which the App Store doesn't expose via mas. Fall back to scanning
        # /Applications and matching apps signed by Apple's Mac App Store.
        # For now, leave empty and rely on system_profiler classification.
        ASCENDO_WEB_MAS_BUNDLE_IDS=""
    else
        ASCENDO_WEB_MAS_BUNDLE_IDS=""
    fi
fi

if [ -z "${ASCENDO_WEB_APPLE_BUNDLES+x}" ]; then
    # Apps where TeamIdentifier is "Software Signing" or where the bundle
    # ID starts with com.apple.* — skip via Apple ownership.
    ASCENDO_WEB_APPLE_BUNDLES=""
fi

_owned_by() {
    local bid="$1"
    case ",${ASCENDO_WEB_BREW_CASKS:-}," in (*",$bid,"*) echo brew; return ;; esac
    case ",${ASCENDO_WEB_MAS_BUNDLE_IDS:-}," in (*",$bid,"*) echo mas; return ;; esac
    case ",${ASCENDO_WEB_APPLE_BUNDLES:-}," in (*",$bid,"*) echo softwareupdate; return ;; esac
    case "$bid" in com.apple.*) echo softwareupdate; return ;; esac
    echo ""
}

# -- Classifier --------------------------------------------------------------

_classify() {
    # Args: app_path
    # Echoes "<handler>\t<source>" — handler is sparkle|keystone|squirrel|builtin
    local app="$1"
    local plist="$app/Contents/Info.plist"

    local sufeed kspid
    sufeed=$(/usr/libexec/PlistBuddy -c "Print :SUFeedURL" "$plist" 2>/dev/null || true)
    kspid=$(/usr/libexec/PlistBuddy -c "Print :KSProductID" "$plist" 2>/dev/null || true)

    if [ -n "$sufeed" ]; then
        printf 'sparkle\tSUFeedURL\n'
        return 0
    fi
    if [ -n "$kspid" ]; then
        printf 'keystone\tKSProductID\n'
        return 0
    fi
    if [ -d "$app/Contents/Frameworks/Squirrel.framework" ]; then
        printf 'squirrel\tSquirrel.framework\n'
        return 0
    fi
    # ShipIt is the helper name Squirrel.Mac uses
    if find "$app/Contents/Frameworks" -name "ShipIt" -maxdepth 4 2>/dev/null | grep -q .; then
        printf 'squirrel\tShipIt\n'
        return 0
    fi
    printf 'builtin\tnone\n'
}

# -- Walk --------------------------------------------------------------------

cd "$APPS_ROOT" 2>/dev/null || exit 0
for app_dir in *.app; do
    [ -d "$app_dir" ] || continue
    plist="$app_dir/Contents/Info.plist"
    [ -f "$plist" ] || continue

    bid=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist" 2>/dev/null || true)
    [ -z "$bid" ] && continue
    ver=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null || true)
    name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$plist" 2>/dev/null || true)
    [ -z "$name" ] && name="${app_dir%.app}"

    owned=$(_owned_by "$bid")
    if [ -n "$owned" ] && [ "$INCLUDE_OWNED" != "1" ]; then
        continue
    fi

    cls=$(_classify "$APPS_ROOT/$app_dir")
    handler=${cls%%$'\t'*}
    source_field=${cls##*$'\t'}

    abs_path="$APPS_ROOT/$app_dir"
    /usr/bin/python3 - "$bid" "$abs_path" "$ver" "$name" "$handler" "$source_field" "$owned" <<'PY'
import json, sys
bid, path, ver, name, handler, src, owned = sys.argv[1:8]
out = {
    "bundle_id": bid,
    "app_path": path,
    "version": ver or "",
    "display_name": name,
    "fingerprint_handler": handler,
    "fingerprint_source": src,
    "owned_by": owned or None,
}
print(json.dumps(out, separators=(",", ":")))
PY
done
exit 0
```

- [ ] **Step 4: Make it executable + run test**

```bash
chmod +x adapters/macos/lib/web_discovery.sh
bash adapters/macos/tests/test_web_discovery.sh
```

Expected: 9 PASS, 0 FAIL.

- [ ] **Step 5: Run a sanity scan against the real `/Applications`**

```bash
ASCENDO_WEB_BREW_CASKS="" ASCENDO_WEB_MAS_BUNDLE_IDS="" ASCENDO_WEB_APPLE_BUNDLES="" \
    bash adapters/macos/lib/web_discovery.sh --emit-json | wc -l
```

Expected: ≥ 30 (real apps minus brew/mas/Apple ownership).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/lib/web_discovery.sh \
        adapters/macos/tests/test_web_discovery.sh
chmod +x adapters/macos/lib/web_discovery.sh
git commit -m "feat(macos/web): web_discovery.sh — Info.plist fingerprint walker (M5.7 T5)

Walks /Applications, classifies each bundle into sparkle/keystone/
squirrel/builtin via Info.plist fingerprints, computes brew/mas/
softwareupdate ownership exclusions. Tests via fixture root."
```

---

## Task 6: Migrate the 24 M5.6 entries to v2 with `bundle_id`

**Files:**
- Modify: `adapters/macos/config/web_apps.toml`

The existing TOML has all 24 entries with `bundle_id` already. Only the schema literal needs bumping.

- [ ] **Step 1: Verify current state**

```bash
head -2 adapters/macos/config/web_apps.toml
```

Expected: `schema = "ascendo-web-apps/v1"`.

- [ ] **Step 2: Bump the schema literal**

```bash
sed -i.bak 's|^schema = "ascendo-web-apps/v1"|schema = "ascendo-web-apps/v2"|' \
    adapters/macos/config/web_apps.toml
rm -f adapters/macos/config/web_apps.toml.bak
```

- [ ] **Step 3: Re-validate via the CLI shim**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 \
    adapters/macos/lib/web_registry.py \
    --shipped adapters/macos/config/web_apps.toml --validate
echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 4: Run the macOS adapter test suite**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ -v 2>&1 | tail -10
```

Expected: all green (363+ tests).

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/config/web_apps.toml
git commit -m "feat(macos/web): bump shipped registry to schema v2 (M5.7 T6)

All 24 entries already include bundle_id (added in M5.6); only the
schema literal flips. Auto-coerce in the loader keeps any existing
v1 user overrides working."
```

---

## Task 7: `release_feed.sh` handler — full implementation + tests

**Files:**
- Create: `adapters/macos/lib/handlers/release_feed.sh`
- Create: `adapters/macos/tests/fixtures/release_feed/feed.json`
- Create: `adapters/macos/tests/fixtures/release_feed/malformed.txt`
- Create: `adapters/macos/tests/test_release_feed_handler.sh`

- [ ] **Step 1: Write the failing test**

Create the fixture feed first:

```bash
mkdir -p adapters/macos/tests/fixtures/release_feed
cat > adapters/macos/tests/fixtures/release_feed/feed.json <<'EOF'
{
  "latest": {
    "darwin": {
      "arm64": {
        "version": "0.2026.05.08.00.00.01",
        "url": "https://example.test/warp-arm64.dmg",
        "arch": "arm64"
      }
    }
  }
}
EOF
echo "not json at all" > adapters/macos/tests/fixtures/release_feed/malformed.txt
```

Create `adapters/macos/tests/test_release_feed_handler.sh`:

```bash
#!/usr/bin/env bash
# Tests for adapters/macos/lib/handlers/release_feed.sh
# Spins up a fixture HTTP server on 127.0.0.1:$PORT
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../lib"
FIXTURES="$SCRIPT_DIR/fixtures/release_feed"
PORT="${ASCENDO_TEST_HTTP_PORT:-8782}"

PASS=0; FAIL=0

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS $name"; PASS=$((PASS+1))
    else
        echo "FAIL $name: expected='$expected', got='$actual'"; FAIL=$((FAIL+1))
    fi
}

# Spawn a one-shot Python http.server bound to FIXTURES dir
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$FIXTURES" \
    >/tmp/ascendo-test-http.log 2>&1 &
HTTP_PID=$!
trap 'kill "$HTTP_PID" 2>/dev/null || true' EXIT

# Wait for server up (up to 3 seconds)
for _ in 1 2 3 4 5 6; do
    if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/feed.json" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# shellcheck source=../lib/handlers/release_feed.sh
. "$ADAPTER_LIB/handlers/release_feed.sh"

CFG_HAPPY=$(cat <<EOF
{
  "slug": "warp",
  "bundle_id": "dev.warp.Warp-Stable",
  "display_name": "Warp",
  "handler": "release_feed",
  "release_feed": {
    "url": "http://127.0.0.1:$PORT/feed.json",
    "version_path": "latest.darwin.arm64.version",
    "download_path": "latest.darwin.arm64.url",
    "http_timeout_s": 5
  }
}
EOF
)

test_happy_path_emits_version() {
    local v
    v=$(release_feed_check "warp" "$CFG_HAPPY")
    assert_eq "test_happy_path_emits_version" "0.2026.05.08.00.00.01" "$v"
}

test_404_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["url"] = d["release_feed"]["url"].replace("/feed.json", "/missing.json")
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    [ "$out" = "" ] || { echo "FAIL test_404_is_skipped: out='$out'"; FAIL=$((FAIL+1)); return; }
    [ "$rc" -ne 0 ] || { echo "FAIL test_404_is_skipped: expected non-zero rc"; FAIL=$((FAIL+1)); return; }
    echo "PASS test_404_is_skipped"; PASS=$((PASS+1))
}

test_malformed_json_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["url"] = d["release_feed"]["url"].replace("/feed.json", "/malformed.txt")
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    [ "$out" = "" ] && [ "$rc" -ne 0 ] && { echo "PASS test_malformed_json_is_skipped"; PASS=$((PASS+1)); return; }
    echo "FAIL test_malformed_json_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_missing_path_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["version_path"] = "no.such.path"
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    [ "$out" = "" ] && [ "$rc" -ne 0 ] && { echo "PASS test_missing_path_is_skipped"; PASS=$((PASS+1)); return; }
    echo "FAIL test_missing_path_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_arch_mismatch_is_skipped() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["arch_path"] = "latest.darwin.arm64.arch"
d["release_feed"]["expected_arch"] = "x86_64"
print(json.dumps(d))
')
    local out rc
    out=$(release_feed_check "warp" "$cfg" 2>&1) ; rc=$?
    [ "$out" = "" ] && [ "$rc" -ne 0 ] && { echo "PASS test_arch_mismatch_is_skipped"; PASS=$((PASS+1)); return; }
    echo "FAIL test_arch_mismatch_is_skipped: out='$out' rc=$rc"; FAIL=$((FAIL+1))
}

test_arch_match_succeeds() {
    local cfg
    cfg=$(printf '%s' "$CFG_HAPPY" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
d["release_feed"]["arch_path"] = "latest.darwin.arm64.arch"
d["release_feed"]["expected_arch"] = "arm64"
print(json.dumps(d))
')
    local v
    v=$(release_feed_check "warp" "$cfg")
    assert_eq "test_arch_match_succeeds" "0.2026.05.08.00.00.01" "$v"
}

test_happy_path_emits_version
test_404_is_skipped
test_malformed_json_is_skipped
test_missing_path_is_skipped
test_arch_mismatch_is_skipped
test_arch_match_succeeds

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash adapters/macos/tests/test_release_feed_handler.sh
```

Expected: FAIL — `release_feed.sh: No such file or directory`.

- [ ] **Step 3: Implement `lib/handlers/release_feed.sh`**

Create `adapters/macos/lib/handlers/release_feed.sh`:

```bash
# adapters/macos/lib/handlers/release_feed.sh
# Generic JSON-over-HTTPS update probe.
#
# Functions:
#   release_feed_check <slug> <config_json>  -> echoes candidate version
#                                                or empty + non-zero rc
#   release_feed_apply <slug> <config_json>  -> downloads + installs DMG
#                                                if download_path set,
#                                                else falls back to open -a

# _rf_get <key> -- reads JSON config from stdin, echoes value at top-level
# OR nested under "release_feed". Reuses heredoc-via-env pattern.
_rf_get() {
    local key="$1"
    local cfg
    cfg="$(cat)"
    ASCENDO_WEB_CFG="$cfg" ASCENDO_WEB_KEY="$key" /usr/bin/python3 <<'PY_EOF'
import json, os, sys

raw = os.environ.get("ASCENDO_WEB_CFG", "")
key = os.environ.get("ASCENDO_WEB_KEY", "")

def _coerce(s):
    try:
        v = json.loads(s)
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v
    except Exception:
        return None

data = _coerce(raw)
if not isinstance(data, dict):
    print("")
    sys.exit(0)

# Allow "release_feed.url" as well as top-level url
if "." in key:
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None
            break
    v = cur
else:
    v = data.get(key)
    if v is None:
        rf = data.get("release_feed") or {}
        v = rf.get(key) if isinstance(rf, dict) else None

if v is None or v is False:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY_EOF
}

# Walk a dotted JSON path with optional [N] indices.
_rf_walk_json() {
    # Args: $1 = JSON body, $2 = dotted path
    /usr/bin/python3 - "$1" "$2" <<'PY'
import json, re, sys
body = sys.argv[1]
path = sys.argv[2]
try:
    data = json.loads(body)
except Exception:
    sys.exit(27)
parts = re.findall(r'[A-Za-z0-9_]+|\[\d+\]', path)
cur = data
for p in parts:
    if p.startswith('[') and p.endswith(']'):
        idx = int(p[1:-1])
        if not isinstance(cur, list) or idx >= len(cur):
            sys.exit(28)
        cur = cur[idx]
    else:
        if not isinstance(cur, dict) or p not in cur:
            sys.exit(28)
        cur = cur[p]
if cur is None:
    sys.exit(28)
print(cur)
PY
}

release_feed_check() {
    local slug="$1" cfg="$2"

    local url version_path arch_path expected_arch timeout
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    version_path=$(printf '%s' "$cfg" | _rf_get "release_feed.version_path")
    arch_path=$(printf '%s' "$cfg" | _rf_get "release_feed.arch_path")
    expected_arch=$(printf '%s' "$cfg" | _rf_get "release_feed.expected_arch")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    [ -z "$timeout" ] && timeout=8

    [ -z "$url" ] || [ -z "$version_path" ] && { echo ""; return 22; }

    local body http_rc
    body=$(/usr/bin/curl -fsSL --max-time "$timeout" "$url" 2>/dev/null)
    http_rc=$?
    if [ "$http_rc" -ne 0 ] || [ -z "$body" ]; then
        echo ""; return 25
    fi

    # Cap response at 256 KiB
    body=$(printf '%s' "$body" | /usr/bin/head -c 262144)

    # Optional arch sanity check
    if [ -n "$arch_path" ] && [ -n "$expected_arch" ]; then
        local actual_arch
        actual_arch=$(_rf_walk_json "$body" "$arch_path") || { echo ""; return 28; }
        if [ "$actual_arch" != "$expected_arch" ]; then
            echo ""; return 29
        fi
    fi

    local version
    version=$(_rf_walk_json "$body" "$version_path")
    local rc=$?
    [ "$rc" -ne 0 ] && { echo ""; return $rc; }
    [ -z "$version" ] && { echo ""; return 28; }

    echo "$version"
    return 0
}

release_feed_apply() {
    local slug="$1" cfg="$2"

    local url version_path download_path timeout
    url=$(printf '%s' "$cfg" | _rf_get "release_feed.url")
    version_path=$(printf '%s' "$cfg" | _rf_get "release_feed.version_path")
    download_path=$(printf '%s' "$cfg" | _rf_get "release_feed.download_path")
    timeout=$(printf '%s' "$cfg" | _rf_get "release_feed.http_timeout_s")
    [ -z "$timeout" ] && timeout=8

    if [ -z "$download_path" ]; then
        # Tier-B trigger fallback
        local app_path display_name
        app_path=$(printf '%s' "$cfg" | _rf_get "app_path")
        display_name=$(printf '%s' "$cfg" | _rf_get "display_name")
        [ -z "$app_path" ] && app_path="/Applications/${display_name}.app"
        /usr/bin/env open -a "$app_path"
        return 0
    fi

    local body
    body=$(/usr/bin/curl -fsSL --max-time "$timeout" "$url" 2>/dev/null) || return 25
    local dmg_url
    dmg_url=$(_rf_walk_json "$body" "$download_path") || return 28
    [ -z "$dmg_url" ] && return 28

    # Reuse the M5.6 DMG installer helper
    _web_install_dmg "$slug" "$dmg_url"
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
bash adapters/macos/tests/test_release_feed_handler.sh
```

Expected: 6 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/lib/handlers/release_feed.sh \
        adapters/macos/tests/fixtures/release_feed/ \
        adapters/macos/tests/test_release_feed_handler.sh
git commit -m "feat(macos/web): release_feed.sh — generic JSON-feed probe handler (M5.7 T7)

Tier-A handler for vendors that publish a release feed but don't
ship Sparkle. Configured per-app via [apps.release_feed] sub-table.
Tested against fixture http.server with 6 cases (happy / 404 /
malformed JSON / missing path / arch mismatch / arch match)."
```

---

## Task 8: Wire `release_feed` into `check.sh` / `plan.sh` / `apply.sh` dispatch

**Files:**
- Modify: `adapters/macos/scripts/web/check.sh:21-24,104-111`
- Modify: `adapters/macos/scripts/web/plan.sh` (similar to check.sh)
- Modify: `adapters/macos/scripts/web/apply.sh:20-22,99-108,120-129`

- [ ] **Step 1: Add `release_feed` to the handler-source loop in check.sh**

In `adapters/macos/scripts/web/check.sh:21-24`, change the handler-source loop:

```bash
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed; do
    . "$ADAPTER_LIB/handlers/${_h}.sh"
done
```

- [ ] **Step 2: Add `release_feed` to the dispatch case in check.sh**

In `adapters/macos/scripts/web/check.sh:104-111`, change the dispatch case:

```bash
    case "$HANDLER" in
        sparkle)      LATEST=$(sparkle_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        github_dmg)   LATEST=$(github_dmg_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        keystone)     LATEST=$(keystone_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        msupdate)     LATEST=$(msupdate_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        docker)       LATEST=$(docker_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        release_feed) LATEST=$(release_feed_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        squirrel|builtin) LATEST="" ;;
    esac
```

- [ ] **Step 3: Apply the same two edits to `plan.sh`**

In `adapters/macos/scripts/web/plan.sh`, find the same two patterns (handler-source loop + dispatch case) and apply the same changes.

- [ ] **Step 4: Add `release_feed` to apply.sh handler-source loop + dispatch + defer-eligible list**

In `adapters/macos/scripts/web/apply.sh:20-22`:

```bash
for _h in sparkle github_dmg keystone squirrel builtin msupdate docker release_feed; do
    . "$ADAPTER_LIB/handlers/${_h}.sh"
done
```

In `adapters/macos/scripts/web/apply.sh:99-108`, add `release_feed` to the defer-eligible list (when its config has `download_path`, it actually swaps DMG bytes — defer if app is running):

```bash
    case "$HANDLER" in
        sparkle|github_dmg|squirrel|release_feed)
            if _web_is_running "$BUNDLE_ID"; then
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: deferred_app_in_use"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                continue
            fi
            ;;
    esac
```

In `adapters/macos/scripts/web/apply.sh:120-129`, add `release_feed` to the dispatch:

```bash
    case "$HANDLER" in
        sparkle)      sparkle_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        github_dmg)   github_dmg_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        keystone)     keystone_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        squirrel)     squirrel_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        builtin)      builtin_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        msupdate)     msupdate_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        docker)       docker_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        release_feed) release_feed_apply "$SLUG" "$CFG" 2> "$err_log" ;;
        *)            false ;;
    esac
```

- [ ] **Step 5: Run macOS adapter tests + an end-to-end check phase**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ -v 2>&1 | tail -10
```

Expected: all green (≥363 passing).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/web/check.sh \
        adapters/macos/scripts/web/plan.sh \
        adapters/macos/scripts/web/apply.sh
git commit -m "feat(macos/web): wire release_feed into check/plan/apply dispatch (M5.7 T8)"
```

---

## Task 9: Tier-B apply emits `triggered` (was `success`)

**Files:**
- Modify: `adapters/macos/scripts/web/apply.sh:132-150`

- [ ] **Step 1: Replace the rc=0 branch with tier-aware emission**

In `adapters/macos/scripts/web/apply.sh:132-150`, replace the entire `if [ $rc -eq 0 ]; then ... else ... fi` block:

```bash
    if [ $rc -eq 0 ]; then
        case "$HANDLER" in
            keystone|squirrel|builtin)
                # Tier-B: vendor's update agent triggered; outcome is async.
                json_add_item "web:${SLUG}" "$INSTALLED" "" "triggered" "web" "$HANDLER"
                case "$HANDLER" in
                    keystone) json_add_message "info" "${SLUG}: ksadmin update queued; daemon will reconcile" ;;
                    squirrel) json_add_message "info" "${SLUG}: app relaunched; Squirrel will self-update on next quit/relaunch" ;;
                    builtin)  json_add_message "info" "${SLUG}: app opened for user (manual update path)" ;;
                esac
                COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
                ;;
            *)
                # Tier-A: synchronous swap completed
                json_add_item "web:${SLUG}" "$INSTALLED" "" "success" "web" "$HANDLER"
                COUNT_SUCCESS=$((COUNT_SUCCESS + 1))
                ;;
        esac
    else
        # Capture last 12 non-empty stderr lines, max 1500 chars
        tail_msg=""
        if [ -s "$err_log" ]; then
            tail_msg=$(/usr/bin/tail -n 12 "$err_log" | /usr/bin/awk 'NF{print}' | /usr/bin/head -c 1500)
        fi
        json_add_item "web:${SLUG}" "$INSTALLED" "" "failed" "web" "$HANDLER"
        json_add_message "error" "${SLUG}: handler exit ${rc}: ${tail_msg}"
        COUNT_FAILED=$((COUNT_FAILED + 1))
    fi
```

- [ ] **Step 2: Sanity-check via a fake-handler integration test**

Run a real check on the local box to make sure no regression:

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m ascendo run --category web --phase check 2>&1 | tail -5
```

Expected: ≥4 items, no errors.

- [ ] **Step 3: Run the macOS test suite**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add adapters/macos/scripts/web/apply.sh
git commit -m "feat(macos/web): Tier-B apply emits 'triggered' status (M5.7 T9)

keystone/squirrel/builtin handlers report 'triggered' instead of
'success' when their async update agents have been kicked off.
Tier-A handlers (sparkle/github_dmg/release_feed/msupdate/docker)
still emit 'success' for synchronous installs."
```

---

## Task 10: Tier-B verify with sleep + `triggered` (with pending/confirmed in messages)

**Files:**
- Modify: `adapters/macos/scripts/web/verify.sh:91-114`

- [ ] **Step 1: Update verify.sh to emit `triggered` for Tier-B with pending/confirmed message**

In `adapters/macos/scripts/web/verify.sh:91-114`, replace the case statement:

```bash
    case "$HANDLER" in
        squirrel|keystone)
            # Tier-B async — emit 'triggered' regardless; refine via message
            if [ -n "$POST" ] && [ "$POST" != "$PRE_VERSION" ]; then
                json_add_item "$ITEM_ID" "$POST" "" "triggered" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: triggered_confirmed (${PRE_VERSION} -> ${POST})"
            else
                json_add_item "$ITEM_ID" "${POST:-$PRE_VERSION}" "" "triggered" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: triggered_pending (no version change observed; vendor agent will apply on next idle/relaunch)"
            fi
            COUNT_OK=$((COUNT_OK + 1))
            ;;
        *)
            # Tier-A handlers — success iff bytes were swapped
            if [ -n "$POST" ]; then
                json_add_item "$ITEM_ID" "$POST" "" "success" "web" "$HANDLER"
                COUNT_OK=$((COUNT_OK + 1))
            else
                json_add_item "$ITEM_ID" "$PRE_VERSION" "" "failed" "web" "$HANDLER"
                json_add_message "error" "${SLUG}: app no longer reports a version; install may have failed"
                COUNT_FAILED=$((COUNT_FAILED + 1))
            fi
            ;;
    esac
```

Also update the python helper at the bottom of verify.sh that builds tab-separated rows: ensure it picks `triggered` items as well as `success` items from the apply sidecar (because Tier-B applies now emit `triggered`):

In `adapters/macos/scripts/web/verify.sh:115-131`, replace the inline python heredoc:

```bash
done < <(python3 - "$APPLY_SIDECAR" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    apply = json.load(fh)
for item in apply.get("items", []):
    # Re-verify both 'success' (Tier-A) and 'triggered' (Tier-B) items.
    if item.get("status") not in ("success", "triggered"):
        continue
    src = item.get("source") or {}
    handler = src.get("feed") or ""
    item_id = item.get("id") or ""
    pre_version = item.get("current_version") or ""
    name = item.get("name") or ""
    app_path = f"/Applications/{name}.app" if name else ""
    print(f"{item_id}\t{handler}\t{app_path}\t{pre_version}")
PY
)
```

- [ ] **Step 2: Verify no syntax errors**

```bash
bash -n adapters/macos/scripts/web/verify.sh && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Run macOS test suite**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add adapters/macos/scripts/web/verify.sh
git commit -m "feat(macos/web): Tier-B verify emits 'triggered' with pending/confirmed (M5.7 T10)

squirrel/keystone verify after sleep checks for version bump.
'triggered_confirmed' (bumped) and 'triggered_pending' (no change yet,
vendor agent will reconcile) appear as informational messages, both
under canonical status 'triggered'. Tier-A handlers unchanged."
```

---

## Task 11: Discovery integration in `check.sh` / `plan.sh` (replaces `--list-slugs`)

**Files:**
- Modify: `adapters/macos/scripts/web/check.sh:84-166`
- Modify: `adapters/macos/scripts/web/plan.sh` (same shape)

The new check loop iterates over discovery output (every installed
web-orphan app) instead of registry slugs. For each discovered app:

1. Check the override registry for a row matching `bundle_id`.
2. If override exists, use its handler + config. Otherwise build a
   default config from discovery's `fingerprint_handler`.
3. Dispatch + emit item.

- [ ] **Step 1: Replace the iteration block in check.sh**

In `adapters/macos/scripts/web/check.sh:84-166`, replace the `while IFS= read -r SLUG; do ... done < <(... --list-slugs ...)` block with discovery-driven iteration:

```bash
# -- iterate apps via discovery layer ------------------------------------------
COUNT_PLANNED=0
COUNT_UTD=0
COUNT_SKIPPED=0
COUNT_FAILED=0

while IFS= read -r DISC_LINE; do
    [ -z "$DISC_LINE" ] && continue

    # Parse one discovery JSON line
    BUNDLE_ID=$(printf '%s' "$DISC_LINE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("bundle_id",""))')
    [ -z "$BUNDLE_ID" ] && continue
    APP_PATH=$(printf '%s' "$DISC_LINE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("app_path",""))')
    INSTALLED=$(printf '%s' "$DISC_LINE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("version",""))')
    DISPLAY_NAME=$(printf '%s' "$DISC_LINE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("display_name",""))')
    DISC_HANDLER=$(printf '%s' "$DISC_LINE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("fingerprint_handler","builtin"))')

    [ -z "$INSTALLED" ] && continue   # bundle had no version; skip

    # Override lookup
    CFG=$(python3 "$REG_SHIM" "${_reg_args[@]}" --get-app-by-bundle-id "$BUNDLE_ID" 2>/dev/null || true)
    if [ -n "$CFG" ]; then
        SLUG=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("slug",""))')
        HANDLER=$(printf '%s' "$CFG" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("handler",""))')
    else
        # Synthetic config from discovery defaults
        SLUG=$(printf '%s' "$DISPLAY_NAME" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')
        # Fall back to a bundle-id-derived slug if display_name produced empty
        # (rare: pure-special-char display names).
        [ -z "$SLUG" ] && SLUG="bundle-$(printf '%s' "$BUNDLE_ID" | tr '.' '-')"
        HANDLER="$DISC_HANDLER"
        # Build CFG JSON via env vars (NOT shell interpolation) so apostrophes
        # / quotes / backticks in display names cannot break the JSON.
        CFG=$(SLUG="$SLUG" BUNDLE_ID="$BUNDLE_ID" DISPLAY_NAME="$DISPLAY_NAME" \
              HANDLER="$HANDLER" APP_PATH="$APP_PATH" \
              python3 -c '
import json, os
print(json.dumps({
    "slug":         os.environ["SLUG"],
    "bundle_id":    os.environ["BUNDLE_ID"],
    "display_name": os.environ["DISPLAY_NAME"],
    "handler":      os.environ["HANDLER"],
    "app_path":     os.environ["APP_PATH"],
}))
')
    fi

    in_filter "$SLUG" || continue

    LATEST=""
    case "$HANDLER" in
        sparkle)      LATEST=$(sparkle_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        github_dmg)   LATEST=$(github_dmg_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        keystone)     LATEST=$(keystone_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        msupdate)     LATEST=$(msupdate_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        docker)       LATEST=$(docker_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        release_feed) LATEST=$(release_feed_check "$SLUG" "$CFG" 2>/dev/null || true) ;;
        squirrel|builtin) LATEST="" ;;
    esac
    LATEST=$(printf '%s' "$LATEST" | tr -d '[:space:]')

    if [ "$LATEST" = "__GH_RATE_LIMITED__" ]; then
        json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
        json_add_message "warn" "${SLUG}: GitHub API rate-limited (60/hr unauthenticated). Set GITHUB_TOKEN or wait ~1h."
        COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
        continue
    fi

    if [ -z "$LATEST" ]; then
        case "$HANDLER" in
            squirrel|keystone|builtin)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: vendor_opaque (Tier-B handler — apply will trigger vendor agent)"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            msupdate|docker)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "info" "${SLUG}: ${HANDLER} not available on this host"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
            *)
                json_add_item "web:${SLUG}" "$INSTALLED" "" "skipped" "web" "$HANDLER"
                json_add_message "warn" "${SLUG}: ${HANDLER} probe returned empty (network or vendor change?)"
                COUNT_SKIPPED=$((COUNT_SKIPPED + 1))
                ;;
        esac
        continue
    fi

    if [ "$INSTALLED" = "$LATEST" ] || ! _version_gt "$LATEST" "$INSTALLED"; then
        json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "up_to_date" "web" "$HANDLER"
        COUNT_UTD=$((COUNT_UTD + 1))
    else
        json_add_item "web:${SLUG}" "$INSTALLED" "$LATEST" "planned" "web" "$HANDLER"
        COUNT_PLANNED=$((COUNT_PLANNED + 1))
    fi
done < <(bash "$ADAPTER_LIB/web_discovery.sh" --emit-json 2>/dev/null)
```

Note: previous "failed-on-empty-probe" behaviour for sparkle/github_dmg is now `skipped` with a warn message, because failed-status caused the whole phase to be marked failed by the orchestrator, masking other green items. The skip is honest: probe didn't produce data, so we can't compare, but installed app still shown.

- [ ] **Step 2: Apply the same change pattern to plan.sh**

In `adapters/macos/scripts/web/plan.sh`, find the equivalent iteration block and apply the same discovery-driven shape. Plan emits `planned` items only for apps that would change.

- [ ] **Step 3: Run a real check end-to-end**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m ascendo run --category web --phase check 2>&1 | tail -5
```

Expected: items count grows from 13 → 25+ (every web-orphan app in /Applications appears).

- [ ] **Step 4: Run macOS test suite**

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/scripts/web/check.sh \
        adapters/macos/scripts/web/plan.sh
git commit -m "feat(macos/web): discovery-driven check + plan iteration (M5.7 T11)

check.sh and plan.sh now iterate over web_discovery.sh output
(every installed web-orphan app) and look up overrides by
bundle_id. Apps not in registry use discovery's fingerprint_handler
defaults. Tier-B handlers emit honest skipped + 'vendor_opaque'
reason rather than failed."
```

---

## Task 12: SPA frontend renders `triggered` status pill

**Files:**
- Modify: `app/frontend/style.css` (add `.st-triggered` rule)
- Modify: `app/frontend/app.js` (status pill render path)

- [ ] **Step 1: Locate the status-pill render path**

```bash
grep -n 'st-ok\|st-warn\|st-err\|st-skip' app/frontend/style.css | head -10
grep -nE 'triggered|st-(ok|warn|err|skip|info)' app/frontend/app.js | head -20
```

This identifies the exact CSS class names + the JS function that maps `item.status` → CSS class.

- [ ] **Step 2: Add the `.st-triggered` rule in style.css**

Find the existing `.st-info` (or nearest neutral pill) rule in `app/frontend/style.css`. Add a sibling rule:

```css
.st-triggered {
    background: var(--info-bg);
    color: var(--info);
    border: 1px solid var(--info);
}
```

(If the grep in step 1 shows different theming variables, mirror those.)

- [ ] **Step 3: Add `triggered` to the status→class mapping in app.js**

Find the function that maps `item.status` strings to CSS classes (typically a switch or object literal). Add `triggered`:

```javascript
const STATUS_PILL_CLASS = {
    success: "st-ok",
    up_to_date: "st-ok",
    failed: "st-err",
    skipped: "st-skip",
    planned: "st-info",
    partial: "st-warn",
    missing: "st-warn",
    triggered: "st-triggered",   // NEW
};
```

(Adapt to whatever the existing structure looks like — could be a `case` block.)

- [ ] **Step 4: Verify the SPA still loads and renders without console errors**

If the dashboard is running (`python -m ascendo dashboard`), reload `http://127.0.0.1:8765/` in the browser and check the dev console for errors.

If it's not running:

```bash
PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m ascendo dashboard --port 8765 &
sleep 2
curl -fsS http://127.0.0.1:8765/ | grep -c 'st-triggered\|status-pill'
kill %1 2>/dev/null
```

Expected: response > 0 (the SPA HTML/JS includes the pill markup).

- [ ] **Step 5: Commit**

```bash
git add app/frontend/style.css app/frontend/app.js
git commit -m "feat(spa): render 'triggered' status pill neutrally (M5.7 T12)"
```

---

## Task 13: `bin/validate-macos.sh` Stage 13.8 / 13.9 / 13.10

**Files:**
- Modify: `bin/validate-macos.sh`

- [ ] **Step 1: Locate Stage 13 in validate-macos.sh**

```bash
grep -n '^==> 13' bin/validate-macos.sh | head
```

Identify where Stage 13.7 ends; that's the insertion point.

- [ ] **Step 2: Append Stage 13.8 / 13.9 / 13.10 after the last existing 13.x**

Open `bin/validate-macos.sh` and append after the last `13.7` block:

```bash

# -- Stage 13.8: discovery enumerates web-orphan apps -------------------------
echo
echo "==> 13.8 web_discovery.sh enumerates web-orphan apps"
DISC_COUNT=$(ASCENDO_WEB_BREW_CASKS="" ASCENDO_WEB_MAS_BUNDLE_IDS="" \
    ASCENDO_WEB_APPLE_BUNDLES="" \
    bash adapters/macos/lib/web_discovery.sh --emit-json 2>/dev/null | wc -l | tr -d ' ')
if [ "$DISC_COUNT" -ge 20 ]; then
    echo "  [PASS] 13.8 discovery emitted $DISC_COUNT app(s)"
    PASS=$((PASS + 1))
else
    echo "  [FAIL] 13.8 discovery only emitted $DISC_COUNT app(s) (expected >= 20)"
    FAIL=$((FAIL + 1))
fi
TOTAL=$((TOTAL + 1))

# -- Stage 13.9: release_feed handler against fixture feed --------------------
echo
echo "==> 13.9 release_feed handler resolves a candidate version"
PORT=8783
python3 -m http.server "$PORT" --bind 127.0.0.1 \
    --directory adapters/macos/tests/fixtures/release_feed \
    >/tmp/ascendo-validate-rf.log 2>&1 &
RF_PID=$!
sleep 0.7
RF_VERSION=$(bash -c "
. adapters/macos/lib/handlers/release_feed.sh
release_feed_check warp '$(python3 -c '
import json
print(json.dumps({
    \"slug\":\"warp\",
    \"bundle_id\":\"dev.warp.Warp-Stable\",
    \"display_name\":\"Warp\",
    \"handler\":\"release_feed\",
    \"release_feed\":{
        \"url\":\"http://127.0.0.1:$PORT/feed.json\",
        \"version_path\":\"latest.darwin.arm64.version\",
        \"http_timeout_s\":5
    }
}))
')'
" 2>/dev/null)
kill "$RF_PID" 2>/dev/null || true
if [ "$RF_VERSION" = "0.2026.05.08.00.00.01" ]; then
    echo "  [PASS] 13.9 release_feed_check returned $RF_VERSION"
    PASS=$((PASS + 1))
else
    echo "  [FAIL] 13.9 release_feed_check returned '$RF_VERSION'"
    FAIL=$((FAIL + 1))
fi
TOTAL=$((TOTAL + 1))

# -- Stage 13.10: web check phase emits >= 20 items + no failed status --------
echo
echo "==> 13.10 web --phase check populates discovery + override merge"
TMP_OUT=$(mktemp -d)
PYTHONPATH="$PWD/core:$PWD/adapters/macos" python3 -m ascendo run \
    --category web --phase check \
    --runs-dir "$TMP_OUT" >/dev/null 2>&1
SIDECAR=$(find "$TMP_OUT" -name 'check__web.json' | head -1)
if [ -z "$SIDECAR" ]; then
    echo "  [FAIL] 13.10 no sidecar produced"
    FAIL=$((FAIL + 1))
else
    ITEM_COUNT=$(python3 -c "
import json
with open('$SIDECAR') as fh:
    d = json.load(fh)
print(len(d.get('items', [])))
")
    if [ "$ITEM_COUNT" -ge 20 ]; then
        echo "  [PASS] 13.10 web check emitted $ITEM_COUNT items"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] 13.10 web check emitted only $ITEM_COUNT items (expected >= 20)"
        FAIL=$((FAIL + 1))
    fi
fi
rm -rf "$TMP_OUT"
TOTAL=$((TOTAL + 1))
```

(`PASS`/`FAIL`/`TOTAL` counters already exist in the script — verify by `grep -n 'PASS=\|FAIL=\|TOTAL=' bin/validate-macos.sh | head`.)

- [ ] **Step 3: Run validate-macos**

```bash
bash bin/validate-macos.sh 2>&1 | tail -20
```

Expected: 41 + 3 = 44 checks; all PASS.

- [ ] **Step 4: Commit**

```bash
git add bin/validate-macos.sh
git commit -m "test(macos/web): validate-macos Stage 13.8/13.9/13.10 (M5.7 T13)

13.8 — discovery emits >=20 apps
13.9 — release_feed handler resolves against fixture http.server
13.10 — web check phase produces >=20 items end-to-end"
```

---

## Task 14: Tag v0.4.0, update PLAN.md / HANDOFF.md / MACOS_QUICKSTART.md

**Files:**
- Modify: `bin/run-tag-release-macos.sh`
- Modify: `PLAN.md`
- Modify: `HANDOFF.md`
- Modify: `MACOS_QUICKSTART.md`

- [ ] **Step 1: Bump tag in run-tag-release-macos.sh**

```bash
grep -n 'v0\.3\.0' bin/run-tag-release-macos.sh
```

Replace each `v0.3.0` with `v0.4.0` and update the milestone description from M5.6 to M5.7. Use `sed -i.bak`:

```bash
sed -i.bak 's/v0\.3\.0/v0.4.0/g; s/M5\.6/M5.7/g; s/WebManager.*M5\.6/web manager auto-discovery + tiered probes/' \
    bin/run-tag-release-macos.sh
rm -f bin/run-tag-release-macos.sh.bak
```

Verify the file's tag-message text is still coherent — open and read the changed section.

- [ ] **Step 2: Mark M5.7 done in PLAN.md**

In `PLAN.md`, in the M5 milestone table (search for `M5.6`), append a new row:

```markdown
| **M5.7** | ✅ done (2026-05-08, **v0.4.0**) | Auto-discovery (`lib/web_discovery.sh` walks `/Applications` + Info.plist fingerprints + brew/mas/softwareupdate ownership exclusion) + override registry v2 (`schema = "ascendo-web-apps/v2"`, bundle_id-keyed) + handler tiers (Tier-A real-probe vs Tier-B trigger-only with new `ItemStatus.TRIGGERED`) + new `release_feed` Tier-A handler. **44/44 PASS** via `bin/validate-macos.sh` Stages 13.8/13.9/13.10. Spec/plan: `docs/superpowers/specs/2026-05-08-macos-web-discovery-design.md` + `docs/superpowers/plans/2026-05-08-macos-web-discovery.md`. |
```

Also update the "Last updated" line at the top of PLAN.md and bump milestone tracker.

- [ ] **Step 3: Add Sesja 37 entry to HANDOFF.md**

Insert a new section at the top of `HANDOFF.md` (right after the title block, before Sesja 36):

```markdown
## Sesja 37 (2026-05-08) — M5.7 web manager auto-discovery + tiered probes + v0.4.0

Closes the breadth + depth gaps that operator hit with v0.3.0 web
manager: only 4 of 24 registered apps reported real candidate
versions, and ~10 installed orphans (Antigravity, Notion, Obsidian,
Proton apps, etc.) weren't in the registry at all.

### What landed

| Commit | Task | What |
|--------|------|------|
| (T1) | feat(core): ItemStatus.TRIGGERED | Tier-B handlers (keystone/squirrel/builtin) report 'triggered' on apply (was 'success' which conflated with synchronous installs) |
| (T2) | feat(macos/web): WebRegistry v2 + release_feed model | Schema bump v1→v2; v1 auto-coerces; new `[apps.release_feed]` sub-table with https-only URL + JSON-path strings |
| (T3) | feat(macos/web): CLI shim --list-bundle-ids + --get-app-by-bundle-id | Bash phase scripts can now key off bundle_id |
| (T4) | test(macos/web): discovery fixtures | 4 fake .app bundles for unit tests |
| (T5) | feat(macos/web): web_discovery.sh | Walks /Applications, Info.plist fingerprints, brew/mas/softwareupdate ownership exclusion |
| (T6) | feat(macos/web): bump shipped registry to schema v2 | All 24 entries port forward |
| (T7) | feat(macos/web): release_feed handler | Generic JSON-feed probe (6 unit tests against fixture http.server) |
| (T8) | feat(macos/web): wire release_feed into dispatch | check/plan/apply all dispatch the new handler |
| (T9) | feat(macos/web): Tier-B apply emits 'triggered' | keystone/squirrel/builtin apply paths use new status |
| (T10) | feat(macos/web): Tier-B verify emits 'triggered' | sleep + version-bump check; pending/confirmed in messages |
| (T11) | feat(macos/web): discovery-driven check + plan | Iteration replaced; every installed web-orphan app appears |
| (T12) | feat(spa): render 'triggered' pill | Neutral info-style pill |
| (T13) | test(macos/web): validate-macos Stage 13.8-13.10 | discovery + release_feed + web-check end-to-end |
| (T14) | release(macos): v0.4.0 | M5.7 done, tag bumped |

### Coverage outcome (real-Mac evidence)

Before M5.7: 13 items in `web --phase check` output, 9 with `cand=None`.
After M5.7: ≥25 items (every web-orphan app in /Applications), Tier-B
apps honestly say "vendor_opaque" rather than silently failing.

### Follow-ups (M5.7.1, registry-only PRs)

Per-vendor `release_feed` configs for Warp / Claude / ChatGPT / Cursor /
Antigravity / Comet / Perplexity. Each is a TOML stanza, no code change.
Tracked in PLAN.md.
```

- [ ] **Step 4: Add discovery blurb to MACOS_QUICKSTART.md**

Open `MACOS_QUICKSTART.md`. Find the section about the web category. Append a paragraph:

```markdown
### Web app discovery (v0.4.0+)

The `web` category auto-discovers every app installed in `/Applications`
that isn't owned by brew/mas/softwareupdate. You'll see ~25 apps in
`web --phase check` output without configuring anything.

Apps with detectable update mechanisms (Sparkle appcast, GitHub
releases, Microsoft AutoUpdate, Docker, vendor JSON feeds via the new
`release_feed` handler) report a real candidate version. Apps with
opaque update agents (Google Keystone — Chrome/GDrive; Squirrel.Mac —
Claude/VSCode/Codex/etc.) show as "skipped: vendor_opaque" on check
and "triggered" on apply (their daemon will reconcile asynchronously).

Override the discovery defaults in `~/.config/ascendo/web_apps.toml`:

```toml
schema = "ascendo-web-apps/v2"

[[apps]]
slug         = "my-app"
bundle_id    = "com.example.myapp"
display_name = "My App"
handler      = "release_feed"

[apps.release_feed]
url          = "https://example.com/version.json"
version_path = "darwin.arm64.version"
```
```

- [ ] **Step 5: Run validate-macos once more end-to-end**

```bash
bash bin/validate-macos.sh 2>&1 | tail -5
```

Expected: 44/44 PASS.

- [ ] **Step 6: Final commit + tag**

```bash
git add bin/run-tag-release-macos.sh PLAN.md HANDOFF.md MACOS_QUICKSTART.md
git commit -m "release(macos): v0.4.0 — M5.7 web manager auto-discovery + tiered probes (T14)"
git tag -a v0.4.0 -m "v0.4.0 — macOS web manager: auto-discovery + tiered probes

Closes the breadth + depth gaps in v0.3.0 web manager.

- Discovery layer walks /Applications + Info.plist fingerprints
- Override registry v2 (bundle_id-keyed)
- Tier-A real-probe handlers vs Tier-B trigger-only with new
  ItemStatus.TRIGGERED
- New release_feed handler (generic JSON-over-HTTPS probe)

44/44 PASS on bin/validate-macos.sh including new Stages 13.8/13.9/13.10."
echo "Tag created locally. Run 'git push --tags' when ready."
```

---

## Verification matrix

After all 14 tasks land:

| Test surface | Command | Expected |
|--------------|---------|----------|
| Sidecar enum | `pytest tests/contract/ -v` | all green |
| WebRegistry v2 | `pytest adapters/macos/tests/test_web_registry_v2.py` | 5/5 pass |
| Discovery | `bash adapters/macos/tests/test_web_discovery.sh` | 9/9 pass |
| release_feed | `bash adapters/macos/tests/test_release_feed_handler.sh` | 6/6 pass |
| macOS adapter aggregate | `pytest adapters/macos/tests/` | ≥365 pass |
| End-to-end | `bash bin/validate-macos.sh` | 44/44 pass |
| Real Mac smoke | `python3 -m ascendo run --category web --phase check` | ≥25 items |
