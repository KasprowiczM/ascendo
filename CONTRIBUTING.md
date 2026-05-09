# Contributing to Ascendo

Thank you for considering a contribution. Ascendo is an open-source
project, and everyone is welcome — code, docs, translations, bug
reports, plugin authoring, OS adapter ports.

## Editions: who this guide is for

Ascendo ships in two editions:

- **Basic** — simplified UI, default install, what most end-users see.
  See [USER_GUIDE.md](USER_GUIDE.md).
- **Dev** — full feature surface (Sync tab, Hosts editor, raw events,
  dev-sync overlay tooling, GitHub repo config). What contributors
  install. See [DEV_GUIDE.md](DEV_GUIDE.md).

**Contributors should install the dev edition.** The one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \
  | ASCENDO_EDITION=dev ASCENDO_PROFILE=full bash
```

Or, for direct repo development, clone + `pip install -e core/ adapters/<os>/`
and write `dev` into `~/.local/share/ascendo/.ascendo-edition`.

## Quick paths to contribute

| Want to | How |
|---------|-----|
| Report a bug | Open a [GitHub Issue](https://github.com/KasprowiczM/ascendo/issues/new) with reproduction steps + `ascendo doctor --verbose` output |
| Suggest a feature | Open a Discussion or Issue with the `feature` label |
| Add a plugin | Copy `plugins/_template/`, follow [ADR-0007](docs/architecture/0007-plugin-manifest-v1.md) + `docs/plugin-author-guide.md`, submit PR |
| Add an OS (Tier 2 → Tier 1) | Start in `contrib/adapters/<os>/`, follow [ADR-0006](docs/architecture/0006-two-tier-adapter-system.md) + `docs/adapter-author-guide.md`, submit PR |
| Add a package manager to an existing OS | See [DEV_GUIDE.md §5](DEV_GUIDE.md#5-adding-a-new-package-manager-cross-platform-pattern) — implement `IPackageManager` + native scripts + tests |
| Add / improve translations | Edit `core/ascendo/i18n/locales/<lang>.json` per `docs/i18n-author-guide.md`, submit PR |
| Improve docs | Direct PRs welcome — typo fixes, clarity improvements, examples |
| Report a security issue | **Do NOT open a public Issue.** See [SECURITY.md](SECURITY.md) |

## The cross-platform contribution promise

Ascendo's "make a change once, it ships everywhere" promise comes from
a strict separation of concerns. Internalize this before you start
typing:

| Where | What lives there | Cross-platform? |
|-------|------------------|-----------------|
| `core/ascendo/` | Pydantic models, interfaces, orchestrator, dashboard, CLI | ✅ OS-agnostic — runs on every supported OS |
| `adapters/macos/` `/windows/` `/ubuntu/` | `IPackageManager` impls + helper modules | ❌ Per-OS — only one runs at a time |
| `app/frontend/` | The vanilla JS SPA | ✅ One tree, gates per-OS via `data-adapter` attributes |
| Native scripts (`adapters/*/scripts/`) | Bash on macOS / Linux, PowerShell on Windows | ❌ Per-OS — communicate via JSON v1 sidecars |

The contract that holds it together:

- Native scripts emit JSON v1 sidecars per
  [docs/agents/contract.md](docs/agents/contract.md).
- Adapters parse those sidecars into Pydantic `Sidecar` models per
  the schema at `docs/architecture/schemas/sidecar.v1.schema.json`.
- The orchestrator works against `IPackageManager` only — never knows
  which OS it's on.
- The frontend renders against the same REST + SSE endpoints
  regardless of which adapter is loaded.

**If you need to add a feature that requires changing the contract,
open a Discussion first.** Contract changes ripple across all three
adapters and require regression tests for each.

## Development setup

### Prerequisites

- Python 3.11+
- Git
- Per-OS dev deps:
  - **macOS:** Bash 3.2+, `brew`, optionally `pwsh` for cross-OS scripts
  - **Windows:** PowerShell 5.1 or 7.x, optionally Pester for PS tests
  - **Linux:** `apt`, `bash`, `shellcheck`
- Optional: `pre-commit` (`pip install pre-commit && pre-commit install`)

### Clone and install (dev edition)

```bash
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo

# Create virtualenv:
python -m venv .venv
source .venv/bin/activate              # Linux/macOS
.venv\Scripts\Activate.ps1             # Windows

# Install core in editable mode:
pip install -e core/[dev]

# Install your OS adapter:
pip install -e adapters/macos/         # or windows/ or ubuntu/

# Mark as dev edition (so the dashboard unlocks Sync / Hosts / raw events):
mkdir -p ~/.local/share/ascendo
echo dev > ~/.local/share/ascendo/.ascendo-edition

# Run tests:
pytest
```

### Read first

- [DEV_GUIDE.md](DEV_GUIDE.md) — dev surfaces, dev-sync overlay,
  bootstrapping, debugging
- [docs/architecture/](docs/architecture/) — ADR-driven decisions
- [HANDOFF.md](HANDOFF.md) — per-session log of recent work
  *(only present on dev machines that imported the overlay)*
- [PLAN.md](PLAN.md) — forward roadmap

## Pull request workflow

1. **Find or open an Issue** before significant work — avoid surprise PRs.
2. **Branch** from `main`:

   ```bash
   git checkout -b feat/your-feature-name
   ```

3. **TDD by default.** Tests live with the code:

   - Core tests in `core/ascendo/tests/` *(unit)* and `tests/contract/`
     *(cross-cutting)*
   - Adapter tests in `adapters/<os>/tests/` *(unit + integration with
     mocked subprocess)*
   - Integration tests across the contract in `tests/integration/`

4. **Commit** in [Conventional Commits](https://www.conventionalcommits.org/) style:

   ```
   feat(scope): one-line summary

   Optional longer description.

   Refs #issue-number
   ```

   Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`,
   `perf`, `style`, `ci`, `build`.

5. **Run linters + tests** before push:

   ```bash
   pre-commit run --all-files
   pytest
   ```

6. **Push and open a PR** with:
   - Clear description of *what* and *why*
   - Linked issue (`Closes #N` or `Refs #N`)
   - Tests for new behavior
   - Updated docs if behavior changed
   - Screenshots if UI changed
7. **Wait for review** — at least one maintainer approval required.
8. **Squash merge** is the default — keeps `main` history clean.

## Code style

| Language | Style |
|----------|-------|
| Python | `ruff format`, `mypy --strict` on `core/`, Pydantic v2 models |
| Bash | `shellcheck`, `set -euo pipefail`, POSIX where possible, Bash 3.2-compatible (no `declare -A` / `mapfile`) |
| PowerShell | `PSScriptAnalyzer` warnings = errors, PS 5.1 + 7.x compatibility |
| Markdown | Clear, scannable, line-wrap at 80–100 chars for prose |
| TOML | Hand-formatted, alphabetized within sections where reasonable |

`.editorconfig` and pre-commit hooks enforce most of this
automatically.

## Testing expectations

- **New core code** must have unit tests (mocked adapters).
- **New adapter implementations** must pass `tests/contract/`.
- **New plugins** must have a smoke test (at minimum).
- **Bug fixes** must include a regression test that fails before the
  fix.
- **Any change touching the JSON v1 contract or `IPackageManager`
  interface** MUST add at least one cross-platform test in
  `tests/contract/`.

Coverage target (post-v1.0): >80% on `core/`, >60% on adapters.

## Architecture firewall (enforced by import-linter)

Ascendo follows Clean Architecture (6 layers — see
[ADR-0005](docs/architecture/0005-six-layer-architecture.md)).
Critical rules:

- **`core/` MUST NOT import from `adapters/*`** — only from
  `core/ascendo/interfaces/`.
- **Native scripts (Bash / PowerShell) MUST emit JSON v1 sidecars** —
  that's how core sees them.
- **Adapters communicate with core only via interfaces** — never modify
  core internals.
- **Plugins live in their own folder** — they don't import from core
  or adapters either.

The CI pipeline enforces these rules with `import-linter`. Violations
are blocked at PR time.

## Tier 1 vs Tier 2 contributions

- **Tier 1 (`adapters/`, `plugins/`)** — higher bar:
  full Python implementation, tests passing, docs, CI matrix slot.
  Maintained by core team.
- **Tier 2 (`contrib/`)** — lower bar:
  manifest + scripts + smoke test. Experimental, "as-is", maintained
  by contributor.

**New OSes start in Tier 2.** After 3+ months without critical bugs +
at least one external user actively running it, propose promotion. See
[ADR-0006](docs/architecture/0006-two-tier-adapter-system.md) for the
full criteria.

## Where to find help

- **Discussions** — [github.com/KasprowiczM/ascendo/discussions](https://github.com/KasprowiczM/ascendo/discussions)
- **Issues** — for bugs, feature requests, RFCs
- **DEV_GUIDE.md** — internal architecture, debugging, release flow
- **docs/architecture/** — ADRs explaining "why this way"
- **docs/agents/contract.md** — the 5-phase JSON contract every native
  script must honor

## Code of Conduct

We follow the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be kind,
be specific, assume good faith.

Thank you for making Ascendo better.
