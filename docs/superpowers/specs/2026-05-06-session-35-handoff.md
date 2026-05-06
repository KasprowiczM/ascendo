# Session 35 Handoff — M5.6 macOS Web App Updater (mid-milestone)

> Date: 2026-05-06 · Worktree: `.claude/worktrees/kind-shaw-9a9681` ·
> Branch: `claude/kind-shaw-9a9681` · HEAD: `e6354fa`

## Current state

**9 of 14 tasks complete.** All foundation + 7 handlers shipped, 335/335
macOS adapter tests passing, no regressions.

| # | Task | Status | Commit |
|---|------|--------|--------|
| Spec | macOS web updater design | ✅ | `cf6dbda` |
| Plan | 14-task implementation plan | ✅ | `4b57622` |
| 1 | WebRegistry pydantic model + 18 tests | ✅ | `6956000` + `5c78709` (fix-up) |
| 2 | `lib/web_registry.py` CLI shim + 6 tests | ✅ | `70cade4` |
| 3 | Shipped `web_apps.toml` (24 apps) + 6 tests | ✅ | `d9c4c1d` |
| 4 | `lib/ascendo_web.sh` shared helpers + 6 tests | ✅ | `67c7189` |
| 5 | Sparkle handler + 4 tests | ✅ | `8b196f3` |
| 6 | GitHub DMG handler + 4 tests | ✅ | `f98c15c` |
| 7 | Keystone handler + 3 tests | ✅ | `b48a442` |
| 8 | Squirrel + Builtin handlers + 4 tests | ✅ | `333d62d` |
| 9 | msupdate + Docker handlers + 4 tests | ✅ | `e6354fa` |
| **10** | **check.sh + plan.sh phase scripts** | **⏳ NEXT** | — |
| 11 | apply.sh phase script | ⏳ | — |
| 12 | verify.sh + cleanup.sh phase scripts | ⏳ | — |
| 13 | WebManager Python class + adapter wiring | ⏳ | — |
| 14 | validate-macos.sh Stage 13 + tag v0.3.0 | ⏳ | — |
| Final | Milestone-wide code review across M5.6.* | ⏳ | — |

## Untracked draft work to reconcile

`adapters/macos/tests/test_web_phase_check_plan.py` (231 lines) — Task 10
implementer started this before hitting API rate limit. Includes a
clever PATH-shim approach for stubbing `curl` (the WebRegistry validator
rejects non-https URLs, so `file://` test fixtures don't work). Next
session should review this draft, either keep + complete the matching
`check.sh`/`plan.sh`, or scrap and restart.

## Critical context for Task 10 implementer

**The plan file uses wrong API names** for the sidecar emit functions.
The plan says `cmd_init`, `cmd_add_item`, `cmd_finalize` — those don't
exist. The real API in `adapters/macos/lib/ascendo_json.sh` is:

- `json_init <phase> <category> <run_id> <trigger> <profile> <tool_name> <tool_version> <host_name> <host_os> <host_os_version> <host_arch> <host_user> <host_is_elevated>` (13 positional args)
- `json_add_item <id> <current_version> <target_version> <status> [source_type] [source_feed]`
- `json_add_message <level> <text> [code]`
- `trap 'json_save_on_exit "$OUTPUT_DIR"' EXIT`
- `set -o pipefail` (NOT `set -eo pipefail` — phase scripts intentionally don't `-e`)

**Reference implementation:** `adapters/macos/scripts/npm/check.sh` —
copy the full pattern (arg parsing, host info, json_init, EXIT trap,
loop body). The Task 10 prompt I dispatched (in the prior chat turn,
just before rate-limit) had the corrected API but the agent crashed
before completing.

## How tasks 5–9 chose to handle the JSON-from-bash issue

Tests pass `f"... {json.dumps(cfg)!r}"` which produces shell-doubled
escape sequences inside single-quoted JSON. Inline `python3 -c '...'`
fails to parse this. **Every handler defines a private `_<name>_get`
helper using a heredoc + env var pattern** (see `lib/handlers/sparkle.sh`
lines 19-90 for the canonical implementation). Phase scripts (Task 10+)
have a different shape — they receive registry data via the
`web_registry.py --get-app` shim, which emits clean JSON, so they can
use inline `python3 -c` for field extraction.

## How to resume

1. **In the next session, start a fresh chat** — context is too full to
   continue interactively. Reference this handoff doc and the plan.
2. Read this file plus
   `docs/superpowers/plans/2026-05-06-macos-web-updater.md` (commit
   `4b57622`) — Task 10 is at line ~1700 of the plan. Note: the API
   name corrections above OVERRIDE the plan text.
3. Use the `superpowers:subagent-driven-development` skill, dispatching
   implementers task-by-task per the established pattern.
4. Decide upfront: review every task or only integration-critical ones
   (Tasks 11, 13). Sessions 1-9 of this milestone showed full review
   cycle ate substantial token budget; targeted reviews on the riskier
   tasks plus a final milestone-wide review (per HANDOFF Sesja 28's
   pattern) may be a better trade.
5. Delete or incorporate the draft `test_web_phase_check_plan.py`
   before writing Task 10.

## What works on Mac.r12.home today

- 24 apps in shipped registry; 21 bundle IDs verified against installed
  apps via `defaults read CFBundleIdentifier`. 3 marked `# TODO: confirm
  bundle_id` (opera, ledger-live, ms365 — not installed locally).
- Per-app handler classification verified: chrome → keystone, gemini →
  keystone (via live `ksadmin --print` evidence — corrected from the
  plan's "squirrel" guess), brave/opera/atlas → sparkle, the AI desktop
  apps → squirrel.
- All 7 handler scripts unit-tested; the foundation libraries
  (ascendo_web.sh + web_registry.py shim) tested against real bash 3.2 +
  python 3.14 on macOS.

## Known follow-ups (deferred to milestone-final review)

From Task 1 code review:
- M5: `WebRegistry.find()` only searches active apps; consider
  `find_any()` for inspection use cases (defer)
- M6: rich docstrings on every public method (defer)
- M3: test repr-formatting hack is brittle for non-trivial values (defer)
- M2: `_read_toml` return type could be `dict[str, object]` (defer)

Cross-handler:
- **Hoist `_*_get` helper to `ascendo_web.sh`** — currently duplicated
  across 5 handlers (sparkle/github_dmg/keystone/squirrel/builtin). 
  Single shared `_web_get` would cut ~70 LOC. Milestone-final cleanup.

## Test command

```bash
cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/kind-shaw-9a9681
PYTHONPATH=core:adapters/macos python3 -m pytest adapters/macos/tests/ -q | tail -3
```
Expected: `335 passed`.

---

**End of session 35 handoff.** Next session: pick up at Task 10
(check.sh + plan.sh) using the corrected API names above.
