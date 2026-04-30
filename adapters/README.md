# adapters/

Tier 1 (official) per-OS implementations of `core/ascendo/interfaces/`.

## Structure

```
adapters/
├── ubuntu/         # Linux/Ubuntu adapter (Tier 1, supported)
├── windows/        # Windows adapter (Tier 1, supported)
└── macos/          # macOS adapter (Tier 1, supported)
```

## Tier 1 contract

Each adapter MUST provide:

1. **Python package** (`ascendo_<os>/`) implementing relevant interfaces from `core/ascendo/interfaces/`
2. **Native scripts** (`scripts/<category>/<phase>.{sh,ps1}`) — 5-phase contract per category
3. **Lib utilities** (`lib/`) — re-usable OS-specific helpers (PowerShell modules, Bash functions)
4. **Tests** (`tests/`) — pytest for Python, Pester for PowerShell, Bats for Bash, plus passing `tests/contract/`
5. **Documentation** (`README.md`) — supported categories, system dependencies, debugging tips
6. **CI matrix slot** in `.github/workflows/` — ubuntu-latest / windows-latest / macos-latest runner

## Tier 2 (community) lives in `contrib/adapters/<os>/`

Lower bar: manifest + scripts + smoke test. See `contrib/adapters/README.md`.

## See also

- `docs/architecture/0006-two-tier-adapter-system.md` — tier rationale + promotion path
- `docs/adapter-author-guide.md` — how to write a new adapter
