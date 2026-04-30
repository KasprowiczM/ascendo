# Contributing to Ascendo

Thank you for considering a contribution. Ascendo is an open-source project,
and everyone is welcome — code, docs, translations, bug reports, plugin
authoring, OS adapter ports.

## Quick paths to contribute

| Want to | How |
|---|---|
| Report a bug | Open a [GitHub Issue](https://github.com/KasprowiczM/ascendo/issues/new) with reproduction steps |
| Suggest a feature | Open a Discussion or Issue with `feature` label |
| Add a plugin | Copy `plugins/_template/`, follow `docs/plugin-author-guide.md`, submit PR |
| Add an OS (Tier 2) | Create `contrib/adapters/<os>/`, follow `docs/adapter-author-guide.md`, submit PR |
| Add/improve translations | Edit `core/ascendo/i18n/locales/<lang>.json`, follow `docs/i18n-author-guide.md`, submit PR |
| Improve docs | Direct PRs welcome — typo fixes, clarity improvements, examples |
| Report a security issue | **Do NOT open public Issue.** See [`SECURITY.md`](SECURITY.md) |

## Development setup

### Prerequisites

- Python 3.11+
- Git
- Per-OS:
  - **Linux:** `apt`, `bash`, `shellcheck`
  - **Windows:** PowerShell 5.1+ or 7.x, optionally Pester
  - **macOS:** Bash 3.2+ (system shell), `brew`
- Optional: `pre-commit` (`pip install pre-commit && pre-commit install`)

### Clone

```bash
git clone https://github.com/KasprowiczM/ascendo.git
cd ascendo
```

### Setup local environment

```bash
# Create virtualenv:
python -m venv .venv
source .venv/bin/activate              # Linux/macOS
.venv\Scripts\Activate.ps1             # Windows

# Install core in editable mode:
pip install -e core/[dev]

# Install your OS adapter:
pip install -e adapters/ubuntu/        # or windows/, or macos/

# Run tests:
pytest core/tests/ tests/contract/
```

### Read first

- [`HANDOFF.md`](HANDOFF.md) — current implementation state, what's done, what's next
- [`docs/architecture/`](docs/architecture/) — ADR-driven architecture decisions
- This file (CONTRIBUTING.md) — workflow expectations

## Pull request workflow

1. **Find or open an Issue** before significant work — avoid surprise PRs
2. **Branch** from `main` (or current dev branch — see `HANDOFF.md`):
   ```bash
   git checkout -b feat/your-feature-name
   ```
3. **Commit** in [Conventional Commits](https://www.conventionalcommits.org/) style:
   ```
   feat(scope): one-line summary

   Optional longer description.

   Refs #issue-number
   ```
   Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`,
   `perf`, `style`, `ci`.
4. **Run tests + linters** before push:
   ```bash
   pre-commit run --all-files
   pytest
   ```
5. **Push and open PR** with:
   - Clear description of what + why
   - Linked Issue (`Closes #N` or `Refs #N`)
   - Tests for new behavior
   - Updated docs if behavior changed
6. **Wait for review** — at least one maintainer approval required.
7. **Squash merge** is the default — keep main history clean.

## Code style

| Language | Style |
|---|---|
| Python | `ruff format`, `mypy --strict` on `core/`, Pydantic v2 models |
| Bash | `shellcheck`, `set -euo pipefail`, POSIX where possible |
| PowerShell | `PSScriptAnalyzer` warnings = errors, PS 5.1 + 7.x compatibility |
| Markdown | Clear, scannable, line-wrap at 80-100 chars for prose |
| TOML | Hand-formatted, alphabetized within sections where reasonable |

`.editorconfig` and pre-commit hooks enforce most of this automatically.

## Testing expectations

- **New core code** must have unit tests (mocked adapters)
- **New adapter implementations** must pass `tests/contract/`
- **New plugins** must have a smoke test (at minimum)
- **Bug fixes** must include a regression test that fails before the fix

Coverage target (post-v1.0): >80% on `core/`, >60% on adapters.

## Architecture firewall

Ascendo follows Clean Architecture (6 layers, see
[`docs/architecture/0005-six-layer-architecture.md`](docs/architecture/0005-six-layer-architecture.md)).
Critical rules:

- **`core/` MUST NOT import from `adapters/*`** — only from `core/ascendo/interfaces/`
- **Native scripts (Bash/PowerShell) MUST emit JSON v1 sidecars** — that's how core sees them
- **Adapters communicate with core only via interfaces** — never modify core internals
- **Plugins live in their own folder** — they don't import from core/adapters either

The CI pipeline enforces these rules with import linters. Violations are blocked.

## Tier 1 vs Tier 2 contributions

- **Tier 1 (`adapters/`, `plugins/`):** higher bar — full Python implementation,
  tests passing, docs, CI matrix slot. Maintained by core team.
- **Tier 2 (`contrib/`):** lower bar — manifest + scripts + smoke test.
  Experimental, "as is", maintained by contributor.

Start in Tier 2. After 3+ months without critical bugs, propose promotion.

## Code of Conduct

We follow the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be kind,
be specific, assume good faith.

## Questions?

- Open a [Discussion](https://github.com/KasprowiczM/ascendo/discussions)
- Or ping a maintainer on a relevant Issue/PR

Thank you for making Ascendo better.
