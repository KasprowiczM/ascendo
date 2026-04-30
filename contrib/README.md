# contrib/

Community-contributed plugins and adapters. **Tier 2 — minimal contract,
experimental, no maintenance guarantees.** Use at own risk.

## Structure

```
contrib/
├── adapters/       # Community OS adapters (e.g. FreeBSD, Fedora, Alma)
└── plugins/        # Community plugins (e.g. vendor-specific drivers)
```

## Tier 2 vs Tier 1

| Aspect | Tier 1 (`adapters/`, `plugins/`) | Tier 2 (`contrib/`) |
|---|---|---|
| Python package | Required | Optional |
| Native scripts | Required (5 phases) | Required (5 phases) |
| Tests | Required (pytest + Pester/Bats + contract) | Smoke test only |
| Documentation | README + adapter-author guide compliance | manifest.toml description |
| CI slot | Yes (matrix runner) | Single smoke job |
| Dashboard integration | Full (live SSE, scheduler, snapshots) | Fallback paths only |
| Support | Maintained by core team | "As is" |

## Promotion path Tier 2 → Tier 1

A `contrib/adapters/<os>/` adapter can be promoted when:

1. Implements all interfaces in `core/ascendo/interfaces/` in Python (not only fallback)
2. Passes full `tests/contract/`
3. Has a maintainer agreeing to long-term support
4. Has 3+ months in `contrib/` without critical bugs
5. Documented at `adapters/<os>/README.md` quality level

## See also

- `docs/architecture/0006-two-tier-adapter-system.md` — tier rationale
