# core/

OS-agnostic Python core for Ascendo. **No imports from `adapters/*`** — this is
the architectural firewall. The core defines interfaces; adapters implement them.

## Structure

- `ascendo/` — importable package (`from ascendo import ...`)
- `tests/` — unit tests for core (mocked adapters)
- `pyproject.toml` — Python project metadata + dependencies

## Layered Architecture (Clean Architecture)

This is **Layer 4** in the 6-layer model:

```
Layer 1: Frontend SPA              (ui/frontend/)
Layer 2: Tauri shell               (ui/desktop-tauri/)
Layer 3: Backend HTTP              (core/ascendo/dashboard/)
Layer 4: Core domain               ← THIS LAYER
Layer 5: Adapter Python            (adapters/<os>/ascendo_<os>/)
Layer 6: Native scripts            (adapters/<os>/scripts/, plugins/<id>/)
```

## Dependencies (allowed)

- Python stdlib
- Pydantic v2 (models, validation)
- FastAPI + uvicorn (Layer 3)
- Typer (Layer 4 CLI)
- SQLite via stdlib `sqlite3`
- Standard cross-OS libraries only

## Dependencies (forbidden)

- `pywin32` / `wmi` / `winreg` — Windows-specific, must live in `adapters/windows/`
- `pyobjc` — macOS-specific, must live in `adapters/macos/`
- `python-apt` — Linux-specific, must live in `adapters/ubuntu/`
- Any direct subprocess calls to `winget` / `apt` / `brew` / `softwareupdate`

## See also

- `core/ascendo/interfaces/` — IPackageManager, IScheduler, ISnapshot, etc.
- `docs/architecture/0001-monorepo-with-adapters.md` — overall architecture
- `docs/architecture/0005-six-layer-architecture.md` — layering rules
- `HANDOFF.md` — current implementation state
