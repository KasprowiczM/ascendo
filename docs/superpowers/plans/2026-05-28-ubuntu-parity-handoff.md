# Sesja 83 — Ubuntu parity + run-log triage

> Date: 2026-05-28
> Branch: `main`
> Reference machine: mk-uP5520 (Ubuntu 24.04, kernel 6.17.0-29-generic, Python 3.14.5)
> Result: `validate-ubuntu.sh` 32/32 PASS; full 5-phase × 7-category run reports `success` for every phase

## Goal

Bring the Ubuntu adapter to the same operator-readiness bar that macOS hit at
v0.2.0 and Windows hit at v0.0.7. Two threads:

1. Audit Ubuntu vs macOS parity, fix anything Linux-blocking.
2. Triage the operator's most recent full-profile run, fix every diagnostic
   that wasn't acting on a real apply failure.

## What shipped

### Root-cause fix — bash sidecar emitter mis-tagged its schema

The rebrand commit (`96d5167`) collapsed `_LEGACY_SCHEMA` (canonical legacy
literal `"ubuntu-aktualizacje/v1"`) into the new canonical `"ascendo/v1"`.
The follow-up patch (`624ba3f`) restored the literal on the reader side
but left `lib/_json_emit.py` stamping fresh sidecars with `"ascendo/v1"`
even though the bash emitter writes the legacy SHAPE (`kind`, `ended_at`,
`exit_code`, `summary={ok,warn,err}`, `diagnostics`, `log_path`).

Net effect: every Ubuntu run since the rebrand failed with `extra_forbidden`
validation errors because `is_legacy_v1()` checks the schema string and
never invoked the translator. Symptom in the field:

```
ManagerError: brew check failed
  String should have at most 4096 characters [type=string_too_long]
  ...
  kind: Extra inputs are not permitted [type=extra_forbidden, input_value='check']
  ended_at: Extra inputs are not permitted [type=extra_forbidden, input_value='2026-05-28T15:15:32Z']
  exit_code: Extra inputs are not permitted [type=extra_forbidden, input_value=0]
  summary.ok: Extra inputs are not permitted [type=extra_forbidden, input_value=0]
  ...
```

Fix: `lib/_json_emit.py:30` — `SCHEMA_ID = "ubuntu-aktualizacje/v1"`. The
bash emitter now stamps the schema string that matches the SHAPE it emits;
`parse_sidecar()` routes through `translate_legacy_v1()`; canonical writers
on macOS / Windows are unaffected (they live in their own per-adapter
`_json_emit` files and still emit `"ascendo/v1"`).

### Ubuntu adapter polish

- **`adapters/ubuntu/ascendo_ubuntu/adapter.py:520`** — Duplicate
  `_systemctl_status()` method was shadowing the more informative one
  (the first returned `ok: systemd 255 (...)`, the second returned a less
  useful `ok: running`). Removed the duplicate so the version-string
  variant runs.

- **`adapters/ubuntu/tests/test_web_manager.py:302`** — Schema assertion
  tuple was `("ascendo/v1", "ascendo/v1")` (duplicate). Updated to accept
  both legacy + canonical so the test passes regardless of which writer
  produced the sidecar.

### Validate harness — 3 OSes, 1 stale assertion

`bin/validate-{ubuntu,macos}.sh` + `validate-windows.ps1` all asserted
`len(ALLOWED_ACTIONS) == 12`. Commit `223ecfc` added `web_probe` (now 13).
All three updated, with a comment pointing at Sesja 79 C.4 so the next
mass-rename doesn't regress the count silently.

### Run-log triage — NVIDIA noise that wasn't an apply failure

The operator's most recent full run (`8d543b01`, 11 upgraded, 8 already
current, 1 deferred, 1 "failed") flagged `Nvidia Smi — failed`. Root cause
was a kernel / DKMS mismatch (`kernel=6.17.0-29-generic`, DKMS module built
for `6.17.0-23-generic`), not an apply failure. Two scripts were
overreporting:

1. **`scripts/apt/verify.sh`** ran an nvidia-smi probe and exited 1 if the
   driver wasn't responding. That's cross-domain — driver health is the
   drivers category's responsibility, not apt's. Demoted to `info` level
   so the signal still appears in a single-sidecar scan but apt verify
   doesn't fail on it.

2. **`scripts/drivers/verify.sh`** emitted `error` for "nvidia-smi not
   responsive after apply" while `scripts/drivers/{check,apply}.sh` emit
   `warn` for the same condition. Severity inconsistency between phases is
   the bug — verify shouldn't escalate a pre-existing condition to error
   just because it ran later. Demoted to `warn`, added a `details` field
   that captures `kernel=$(uname -r); dkms=$(dkms status | grep nvidia)`
   so the operator sees the actual mismatch without leaving the sidecar.

## Verification

| Surface | Before | After |
|---|---|---|
| `validate-ubuntu.sh` | 11 failed / 31 stages | **32/32 PASS** |
| `python -m pytest adapters/ubuntu/tests/` | 142 passed, 1 failed | **143/143 pass** |
| `python -m pytest tests/contract/` (regression check) | 22 failed (pre-existing) | 22 failed (zero new) |
| `python -m ascendo doctor` | crashed earlier on schema mismatch | 14 ok, 1 degraded (timeshift not installed — optional) |
| Full run `--phase apply` | 1 "failed" item (nvidia-smi noise) | clean — nvidia surfaces as warn with kernel/dkms diff |

The 22 contract failures are documented baseline drift (Windows-only
service tests + 14 web-config / web-override tests that fail on a fresh
checkout of `main` — `git stash` + re-run confirms zero introduced by this
session).

## Files changed

```
adapters/ubuntu/ascendo_ubuntu/adapter.py      (-17 lines, dedup _systemctl_status)
adapters/ubuntu/tests/test_web_manager.py      ( ±2 lines, accept both schema literals)
bin/validate-ubuntu.sh                         ( ±3 lines, 12→13 + comment)
bin/validate-macos.sh                          ( ±1 line,  12→13)
bin/validate-windows.ps1                       ( ±2 lines, 12→13 + comment)
lib/_json_emit.py                              ( ±1 line,  SCHEMA_ID legacy literal)
scripts/apt/verify.sh                          (~12 lines, demote NVIDIA to info)
scripts/drivers/verify.sh                      (~17 lines, demote NVIDIA to warn + add kernel/dkms diag)
docs/superpowers/specs/2026-05-28-ubuntu-parity-handoff.md  (new — this file)
```

## For the operator (mk-uP5520) — next steps

1. NVIDIA driver: DKMS module is built against `6.17.0-23` but you're running
   `6.17.0-29`. Either:
   - `sudo dkms autoinstall -k $(uname -r)` to rebuild for the running kernel, or
   - Reboot into a kernel the DKMS module targets, or
   - Reinstall the driver: `sudo apt install --reinstall nvidia-dkms-580` (or your installed major version).
2. After the rebuild / reboot, `python -m ascendo run --category drivers --phase verify`
   should report `ok` with the actual driver/SMI metadata.
3. The Ubuntu `.deb` rebuild path is documented in the session transcript —
   `bash packaging/build-deb.sh --edition=basic` produces
   `dist/ascendo-basic_0.6.0_all.deb`; `sudo apt install ./dist/...` to
   install. The old `ascendo 0.3.0` .deb is still installed under
   `/opt/ubuntu-aktualizacje/` — `sudo apt purge ascendo` clears it before
   the new install.

## Out of scope (intentionally untouched)

- The 22 pre-existing contract failures on `main` (Windows service tests +
  web routes). They predate this session and removing them is its own task.
- The 3 `tests/python/test_no_window_kwargs.py` failures (Windows-only code
  asserted on Linux without an OS skip mark) — same reason.
- pipx `upgrade-all` non-zero exit handling. The legacy translator maps
  `result=warn` → `status=skipped` which surfaced as "Deferred (1 app):
  Upgrade All — skipped" in the operator's report. That's accurate (pipx
  did exit non-zero on a wheel build failure for `tree-sitter-dm`), so
  leaving the mapping as-is.

## References

- `624ba3f fix(core): restore legacy schema literal — rebrand collapsed it into the canonical one`
  (companion patch — this session fixes the writer-side gap that one missed)
- `223ecfc test: ALLOWED_ACTIONS now 13 (web_probe added by C.4)` (where
  the 12→13 count drift was introduced; only validate harness scripts
  weren't bumped)
- ADR-0003 — JSON v1 sidecar contract (notes the dual-literal rule and now
  carries an explicit "do not change the legacy literal" warning since
  Sesja 82)
