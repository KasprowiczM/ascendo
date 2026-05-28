# Sesja 84 — Windows parity + validation fixes

> Date: 2026-05-28
> Branch: `main`
> Reference machine: Windows 11 (PowerShell 7.6.2, Python 3.14)
> Result: `validate-windows.ps1` ALL CHECKS PASS (14 stages)

## Goal

Bring the Windows adapter back to 100% parity with the new validation harness, AI tools, and UX redesign that shipped for macOS (v0.2.0) and Ubuntu (Sesja 83). The goal was to ensure Windows handles the new `i18n` parity tests, `frontend hygiene` checks, and `AI chat` features without errors.

## What shipped

### Windows-specific Encoding Fixes

The new `check-i18n-parity.py` and `check-frontend-hygiene.py` scripts spawned `node` to evaluate the frontend JS. On Windows, Python's `subprocess.run` defaults to the system's active ANSI code page (often `cp1250` in Central Europe) when reading stdout, leading to `UnicodeDecodeError: 'charmap' codec can't decode byte 0x81` when encountering UTF-8 characters.
* **Fix**: Added `encoding="utf-8"` to all `subprocess.run` calls in these scripts.

### PowerShell 5.1 Parsing and String Literal Fixes

The Windows validation script `validate-windows.ps1` was failing to parse on Windows due to an interaction between default encoding and UTF-8 em-dashes (`—`). When parsed as `Windows-1252`, the UTF-8 bytes for the em-dash produced a "smart quote" character (`\x94`), which the PowerShell tokenizer interpreted as a string delimiter. This caused unbalanced script blocks (`Missing closing '}'`).
* **Fix**: Replaced em-dashes with standard dashes (`-`) in `validate-windows.ps1`.

Another syntax error occurred in the inline `python -c` script inside `validate-windows.ps1` for Stage 14.1 (prompt library validation). The escaped double quotes `\"` caused premature string termination.
* **Fix**: Replaced the `\"` inside the inline python call with single quotes (`'`).

## Verification

| Surface | Before | After |
|---|---|---|
| `validate-windows.ps1` | Crashed on syntax and encoding errors | **PASS** (all stages, 520 packages enumerated) |
| `check-i18n-parity.py` | Crashed (`UnicodeDecodeError`) | **PASS** (1196 EN keys == 1196 PL keys) |
| `check-frontend-hygiene.py` | Crashed (`TypeError`) | **PASS** (HYGIENE: PASS) |

## Files changed

```
bin/validate-windows.ps1                   (fixed parsing bugs, quotes, and dashes)
scripts/check-i18n-parity.py               (added utf-8 encoding for subprocess)
scripts/check-frontend-hygiene.py          (added utf-8 encoding for subprocess)
docs/superpowers/plans/2026-05-28-windows-parity-handoff.md (new — this file)
```

## Next steps

The Windows adapter is completely up to date with the latest cross-platform standards (frontend, i18n, AI chat capabilities).
