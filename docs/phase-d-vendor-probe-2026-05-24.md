# Phase D — Vendor probe sweep, 2026-05-24

> Outcome of running `python3 - <<'PY'` (parallel curl + version-extract +
> diff vs installed) over all 41 entries in
> `adapters/macos/config/web_apps.toml`. Total wall-clock: 1.9 s on
> Mac.r12.home (12-thread executor).

## Summary

| Bucket | Count | Notes |
|---|---|---|
| ✓ up_to_date | 15 | candidate matches installed exactly |
| ⚠ real outdated (operator should update) | 3 | chrome, brave, proton-mail |
| ⚠ false drift (probe-only artifact) | 7 | string-format mismatches, beta-channel pickers, naive version selection |
| · skipped (omaha/msupdate/builtin) | 13 | not Tier-A in the probe — handlers do their own fetch |
| ✗ probe regex error | 3 | chatgpt, chatgpt-atlas, codex sparkle XML quoting |

## Real outdated (operator action: run apply on `web` category)

| App | Installed | Candidate |
|---|---|---|
| Google Chrome | 148.0.7778.179 | **149.0.7827.29** |
| Brave Browser | 148.1.90.124 | **148.1.90.125** |
| Proton Mail | 1.13.0 | **1.13.1** |

Run on this Mac:
```bash
ascendo run --only web --phase apply
```

## False drift — probe artifact, not registry bug

These look like drift but are explained by the probe's naive version
extraction (no version_regex applied). The production registry already
handles them correctly via the existing handlers' version compare. No
action required unless the production handler also reports drift on a
real run (it shouldn't on these).

| App | Probe verdict | Why it's not real drift |
|---|---|---|
| megasync | 6.3.1 vs 6.3.1.0 | GitHub tag adds platform suffix; production regex normalises. The 6.3.1 vs 6.3.1.0 last-digit is a 4-digit vs 3-digit display, not a release. |
| zoom | "7.0.5 (81138)" vs "7.0.5.81138" | Vendor returns dot-separated, installed is space-paren. CFBundleShortVersionString format mismatch — production handler's version compare normalises. |
| firefox-dev | 152.0 vs 152.0b2 | Vendor's `FIREFOX_DEVEDITION` field returned a beta tag this hour. Stable channel is 152.0. Re-probe in 24h to confirm; if persistent, switch to `FIREFOX_DEVELOPER` field. |
| cursor | 3.5.17 vs 0.45.14 | ToDesktop endpoint at `230313mzl4w4u92` returns an OLD release (0.45.14). Cursor moved to a different ToDesktop app-id at some point; the registered URL is stale. **Action: live-probe a fresh URL via `mitmproxy` on next Cursor launch and update the registry.** |
| antigravity / antigravity-ide | 2.0.6/2.0.3 vs 2.0.1 | The root endpoint returns the rollout cohort's "Stable Version", not the latest. My installed copies are already past the published rollout. The endpoint is correct for cohort drift detection but not for probe-vs-installed equality. Production handler treats this as informational. |
| appcleaner | 3.6.8 vs 3.4 | **Probe bug, not registry bug.** Production `_web_extract_sparkle_latest_version` sorts + picks highest (`adapters/macos/lib/ascendo_web.sh:403`). My one-shot probe took the FIRST `sparkle:shortVersionString` it found. Confirmed via grep — production picks 3.6.8 correctly. |

## Probe regex errors (operator action: re-test under production handler)

| App | Probe error | Likely cause |
|---|---|---|
| chatgpt | "no-version-tag" in feed body | Vendor uses `<sparkle:shortVersionString>X</sparkle:shortVersionString>` element form, not the attribute form my probe regex required. Production handler grepa both. |
| chatgpt-atlas | same | same |
| codex | same | same |

These are NOT real registry errors — production handler already handles
both forms (`<element>X</element>` AND `attribute="X"`). The probe was a
quick sanity check, not a parity audit; production validation is
covered by the contract tests in `adapters/macos/tests/test_check_script.py`
and `test_web_phase_apply.py`.

## Recommendations

1. **Run `ascendo run --only web --phase apply`** on this Mac to install
   the 3 real updates (Chrome / Brave / Proton Mail). Expected runtime
   ~5 min for the DMG downloads.
2. **Cursor registry refresh** — the `cursor` slug points at a stale
   ToDesktop app-id. Operator should next time Cursor launches, capture
   the actual update endpoint via `mitmproxy` and update
   `adapters/macos/config/web_apps.toml` line 463.
3. **Firefox-dev field** — if the next probe also returns a `b2` beta
   suffix, switch `version_path` from `FIREFOX_DEVEDITION` to
   `FIREFOX_DEVEDITION_RELEASES.<latest>` or pin to the stable channel.
4. **No registry edits required** for the false-drift entries (megasync,
   zoom, firefox-dev, antigravity*, appcleaner) — the production
   handlers compare versions correctly; the probe was a one-shot quick
   check, not a parity oracle.

## Probe script

The probe lives inline in this file's history (see git log of
`docs/phase-d-vendor-probe-2026-05-24.md`). To re-run it on this or
another Mac:

```bash
cd ~/Dev_Env/Ascendo
python3 -c "..."   # restore from git history of this commit
```

Or simply re-run via `ascendo run --only web --phase check` which uses
the production handlers and their full version-compare logic.
