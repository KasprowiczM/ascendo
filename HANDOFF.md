# Ascendo — Implementation Handoff

> **Single source of truth dla resumowania pracy nad Ascendo.**
> Jeśli sesja Cowork crashuje, jeśli wracasz po przerwie, jeśli nowy Claude
> zaczyna od zera — **przeczytaj ten plik najpierw**. Wszystko czego
> potrzebujesz do kontynuacji jest tutaj.
>
> **Aktualizuj** ten plik po każdej sesji w sekcji `## Session Log`
> i `## Current State`.

---

## TL;DR — gdzie jesteśmy

**Projekt:** Ascendo — cross-platform (Linux + Windows + macOS) update orchestrator
z dashboard webowym, scheduler, snapshots, plugin system. Open-source MIT.

**Faza:** M1 (Foundation) — restrukturyzacja monorepo. Ukończono M1.0 (handoff)
i M1.1 (clean working tree, tag, branch). Pozostały: M1.2-M1.7.

**Repo:** `D:\Dev_Env\ascendo` lokalnie, origin: `https://github.com/KasprowiczM/ascendo.git`

**Branch pracy:** `restructure/monorepo` (utworzony, working tree clean,
poza nieistotnym `.write-test`)

**Tag rollback:** `pre-monorepo-restructure` (stan przed jakimikolwiek zmianami)

---

## Project Overview

### Co to jest

Ascendo to platforma orchestrująca aktualizacje na 3 OS (Linux, Windows, macOS)
przez jeden CLI + jeden web dashboard + jeden plugin system. Powstaje przez
**unifikację trzech istniejących repo**:

1. `D:\Dev_Env\Aktualizacje_MAC` — najstarsze (shell scripts macOS, ~5000 LOC)
2. `D:\Dev_Env\Aktualizacje-W11-Dell5520` — średnie (PowerShell Windows)
3. `D:\Dev_Env\Ubuntu_Aktualizacje` — najmłodsze, **najbardziej dojrzałe**
   (Bash + Python FastAPI + vanilla JS SPA + Tauri + scheduler + snapshots
   + plugins + dev-sync). To jest punkt startowy — sklonowane jako
   `D:\Dev_Env\ascendo`.

### Cele biznesowe

- Open-source projekt na GitHub
- 3 OS first-class (macOS priorytet wysoki, projektujemy z myślą o nim)
- 100% native Windows (bez WSL2)
- Distribution: winget (Win), brew tap (mac), `.deb`/AUR (Linux), GitHub Releases
- Landing page na GitHub Pages (na razie `<you>.github.io/ascendo`)
- Brak komercyjnego modelu, brak telemetrii (opt-in tylko)
- Brak centralnego backendu (100% lokalne)

### Co użytkownik dostaje (target v0.1.0)

- `winget install Ascendo.Ascendo` na Windows
- `brew install KasprowiczM/tap/ascendo` na macOS (gdy dojdziemy)
- `apt install ./ascendo_*.deb` na Linux
- Tauri desktop app (z embedded FastAPI backend)
- CLI `ascendo run --profile=safe` dla power-userów
- Dashboard na `http://127.0.0.1:8765/` (lokalnie)

---

## Reference — Decyzje z FAZ 1-4 (kompresowane)

### FAZA 1 — Mapa architektury 3 repo

**Najdojrzalsza:** Ubuntu/Ascendo (90% infrastruktury core już istnieje —
FastAPI, JSON v1 contract, plugin manifest, scheduler, snapshots, dev-sync,
branding, Tauri shell)

**Najsprytniejsze hacks (do zachowania):** Windows ma column-position parser
(`Get-ColValue`), unknown-version suppression z lokalnym evidence,
`NativeInstallPaths` whitelist, exit-code mapping
(`-1978335190`/`-1978335212`/`3010`), separator-before-header detection.

**Najwięcej lekcji:** macOS — i18n loader z 7 językami (PL/EN/ES/IT/PT/DE/FR),
DMG verification chain (`hdiutil` + `spctl` + `pkgutil`), session dir +
trap EXIT cleanup, Keystone integration.

### FAZA 2 — Wariant A (zatwierdzony)

**Architektura:**
- **Core:** Python (FastAPI + Typer CLI + Pydantic v2 + SQLite)
- **Adapters:** PowerShell na Windows, Bash na Linux/macOS — **zachowane jako natywne skrypty**, NIE przepisywane na Python
- **Desktop UI:** Tauri 2.x (już jest w `app/tauri/`, rozszerzamy na 3 OS)
- **Backend bundling:** PyInstaller na Windows + macOS (one-folder mode), system Python na Linux (.deb declares dep)
- **Dystrybucja:** multi-channel (winget primary na Win, brew tap primary na mac, .deb primary na Linux)

**Kluczowe założenie:** PS scripts mają HIDDEN GEMS (6+ iteracji bugfixów)
których nie wolno zgubić. Promotion-on-demand — przepisujemy na Pythona TYLKO
jeśli konkretna logika potrzebna jest cross-OS.

### FAZA 3 — Docelowa architektura

#### Struktura monorepo (cel — M1.2 ją zbuduje)

```
ascendo/
├── core/ascendo/           # Python core (OS-agnostic)
│   ├── interfaces/         # IPackageManager, IScheduler, ISnapshot, ...
│   ├── models/             # Package, Run, PhaseResult, sidecar v1
│   ├── orchestrator/       # phase runner, lock, JSON emit/parse
│   ├── adapter_factory/    # OS detection + adapter selection
│   ├── dashboard/          # FastAPI app
│   ├── frontend_static/    # SPA (przeniesione z app/frontend/)
│   ├── cli/                # Typer CLI
│   ├── scheduler/          # systemd / launchd / Task Scheduler
│   ├── snapshot/           # timeshift / Time Machine / VSS / manual
│   ├── devsync/            # GitHub + cloud overlay
│   ├── i18n/               # 7 języków (port z macOS bash)
│   ├── plugins_loader/     # manifest validator + dispatcher
│   ├── elevation/          # sudo / UAC abstraction
│   └── ...
├── adapters/
│   ├── ubuntu/             # Tier 1 — full pack (current Bash code)
│   ├── windows/            # Tier 1 — full pack (port z Aktualizacje-W11-Dell5520)
│   └── macos/              # Tier 1 — full pack (port z Aktualizacje_MAC, deferred)
├── plugins/
│   ├── agent-clis/         # Claude/Codex/Gemini/Qwen/OpenCode (cross-OS)
│   ├── dell-driver-update/ # Windows only
│   ├── nvidia-driver-update/ # Linux only
│   └── _template/          # scaffold dla community
├── contrib/                # Tier 2 community — minimal contracts
│   ├── adapters/
│   └── plugins/
├── ui/
│   ├── desktop-tauri/      # Tauri shell (z app/tauri/, rozszerzamy 3 OS)
│   └── frontend/           # vanilla JS SPA (z app/frontend/)
├── packaging/
│   ├── deb/                # current
│   ├── msi/                # WiX
│   ├── pkg/                # macOS
│   ├── homebrew-tap/       # ascendo formula
│   ├── winget-manifest/    # YAML
│   └── pyinstaller/        # specs per OS
├── website/                # Astro static site → GitHub Pages
├── docs/architecture/      # ADRs
├── tests/{cross-cut,contract,fixtures,integration}/
├── branding/               # icon.svg + .ico + .icns
└── .github/workflows/      # validate / test / build / release / deploy-website
```

#### 6 warstw architektonicznych (Clean Architecture)

1. **Frontend SPA** (vanilla JS) — wie tylko o REST/SSE
2. **Tauri shell** (Rust) — spawn Pythona, otwarcie webview
3. **Backend HTTP** (FastAPI) — REST endpoints, deleguje do core
4. **Core domain** (Python) — modele, orchestracja, polega tylko na interfejsach
5. **Adapter Python** (`adapters/<os>/ascendo_<os>/`) — implementuje interfaces, woła Warstwę 6
6. **Native scripts** (PS/Bash) — atomic OS operations, emit JSON v1 sidecar

**Dependency rule:** N → N-1 lub niżej. Frontend NIGDY nie woła Warstwy 4 bezpośrednio. Core NIGDY nie importuje z `adapters/*`.

#### JSON v1 sidecar contract — `ascendo/v1`

Rebrand z `ubuntu-aktualizacje/v1`. Nowe pola (wszystkie opcjonalne, backward-compatible):

- `run` — id/trigger/profile/dry_run
- `host` — hostname/os/os_version/arch/user/is_elevated/elevation_method
- `tool` — name/version/binary_path
- `items[].source` — type (winget/apt/brew/web)/feed
- `items[].evidence` — registry_version/appx_version/dpkg_version/etc.
- `rollback` — available/snapshot_id/method/instructions_path

Reader akceptuje obie schemas; emiter pisze tylko `ascendo/v1` po migracji.

#### Plugin manifest v1

`plugins/<id>/manifest.toml` z polami: `schema`, `id`, `display_name`,
`description`, `version`, `maintainer`, `license`, `tier` (official/contrib),
`privilege` (user/sudo/admin), `risk` (low/medium/high), `manual_confirm`,
`timeout_sec`, `phases`, `supported_oses[]`, `dependencies` (binaries,
python_modules, plugins), `scripts` (per OS, per phase), `config`,
`reporting`.

#### Dwa tiers adapterów

- **Tier 1 (`adapters/<os>/`):** pełny pack — Python package + native scripts
  + lib + tests + docs + CI matrix slot. Pełna integracja z dashboardem,
  scheduler, snapshots. Kandydaci: Ubuntu, Windows, macOS.
- **Tier 2 (`contrib/adapters/<os>/`):** minimum — manifest.toml + scripts +
  smoke test. Działa przez fallback paths w core. Experimental, brak
  wsparcia. Promotion path do Tier 1 wg kryteriów.

#### Security — 7 zagrożeń, 7 mitygacji

- **T1 Złośliwy plugin** → sandbox + permissions allowlist + signing (FAZA II)
- **T2 Skompromitowany source** → `IPackageSource.verify_signature` per type
- **T3 MITM dla update** → SHA256SUMS + GPG-signed releases + HTTPS-only
- **T4 Local privesc** → no shell strings, args[] only, allowed elevated commands whitelist
- **T5 Sekrety** → .gitignore + gitleaks pre-commit + cleanup_protected_patterns
- **T6 Skradziony token dashboard** → opt-in, HttpOnly cookie, rotation
- **T7 CSRF** → FastAPI middleware, CSP header, 127.0.0.1-only

#### Rollback — 3 poziomy

1. **Per-package** (apt/winget/brew downgrade) — w JSON sidecar `rollback.method`
2. **System snapshot** — VSS (Win), Time Machine read-only (mac), timeshift/etckeeper (Linux), manual fallback
3. **Manual markdown instructions** — generowane przy każdym apply do `~/.ascendo/rollback/`

### FAZA 4 — Plan wdrożenia (6 milestone'ów)

| ID | Tytuł | Time-budget | Outcome |
|---|---|---|---|
| **M1** | Foundation: rebrand + monorepo restructure | 4-6 dni | Repo scaffold gotowy, zero regresji |
| **M2** | Core skeleton: cross-OS rdzeń | 5-7 dni | Interfaces, factory, i18n, contract tests |
| **M3** | Windows MVP: pierwszy Ascendo Win | 5-7 dni | `ascendo run` działa na realnym Windows |
| **M4** | Distribution & UI: pierwsza public release | 8-12 dni | **v0.1.0** — Linux+Windows, MSI+deb+winget |
| **M5** | macOS adapter | 5-7 dni | **v0.2.0** — full 3 OS |
| **M6** | Hardening & v1.0 stable | otwarty | **v1.0** — security audit, code signing |

**Total M1-M5:** 27-39 dni single-dev, **~3-6 miesięcy kalendarzowych**.

---

## Current State (UPDATE this section after each session)

### Last updated
2026-05-01 — Sesja 3 (Cowork) — M2.1 + M2.2 ukończone

### Branch & commits
- **Branch:** `restructure/monorepo`
- **Tag rollback:** `pre-monorepo-restructure` (commit 36bc6f0)
- **Last commit on branch:** identyczny z `pre-monorepo-restructure` — wszystkie M1.2-M1.6 zmiany są w working tree, jeszcze NIE zacommitowane (jeden duży commit do zrobienia przez user)
- **Origin:** `https://github.com/KasprowiczM/ascendo.git`
- **Backup origin (ojciec klonu):** `D:\Dev_Env\Ubuntu_Aktualizacje` (lokalny)

### Working tree
- **Modified (tracked):** `.gitignore`, `README.md`
- **New (untracked):** wszystkie nowe pliki z M1.2-M1.6:
  - Top-level: `HANDOFF.md`, `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`,
    `SECURITY.md`, `.gitattributes`, `.markdownlint.json`,
    `.pre-commit-config.yaml`, `pyproject.toml`
  - Foldery monorepo: `core/`, `adapters/{ubuntu,windows,macos}/`,
    `contrib/{adapters,plugins}/`, `plugins/{_template,agent-clis,
    dell-driver-update,nvidia-driver-update}/`, `ui/{frontend,desktop-tauri}/`,
    `packaging/{deb,msi,pkg,homebrew-tap,winget-manifest,pyinstaller}/`,
    `website/`, `tests/{contract,cross-cut,fixtures,integration}/`
  - ADRs: `docs/architecture/{0001..0007}*.md` + `templates/adr-template.md` + `README.md`
  - pyproject.toml na 4 lokalizacjach: root, `core/`, `adapters/{ubuntu,windows,macos}/`

### Konfiguracja repo
- `core.autocrlf=false` ✅
- `.gitattributes` ✅ (M1.6)

### M1 Progress

| Task | Status | Notes |
|---|---|---|
| M1.0 — HANDOFF dokument | ✅ done | Sesja 1 |
| M1.1 — git tree clean + tag + branch | ✅ done | Sesja 1, user (PowerShell) |
| M1.2 — Szkielet folderów monorepo | ✅ done | Sesja 1 (przed crashem) |
| M1.3 — Top-level docs (LICENSE/CHANGELOG/SECURITY/CONTRIBUTING) | ✅ done | Sesja 1 (przed crashem) |
| M1.4 — pyproject.toml workspace | ✅ done | Sesja 2 (4 plików: root + core + 3 adaptery) |
| M1.5 — 7 ADR-ów w docs/architecture/ | ✅ done | Sesja 2 (0001-0007) |
| M1.6 — .gitattributes + .gitignore + pre-commit | ✅ done | Sesja 1 (`.gitattributes`, `.pre-commit-config.yaml`, `.markdownlint.json`, rozszerzony `.gitignore`) |
| M1.7 — Walidacja `update-all.sh` | ⏳ pending | **User-side** test na linuksie po pierwszym commit + push |

### M2 Progress (Core skeleton)

| Task | Status | Notes |
|---|---|---|
| M2.1 — Sidecar Pydantic v2 modele (`ascendo/v1`) | ✅ done | Sesja 3 — `core/ascendo/models/{host,run,package,result,sidecar}.py` + `__init__.py`. Pełne pokrycie ADR-0003: enums (Phase, ItemStatus, SourceType, ElevationMethod, ...), validators (reverse-time, summary/items consistency), legacy schema acceptance |
| M2.2 — 6 core interfaces + IAdapter | ✅ done | Sesja 3 — `core/ascendo/interfaces/{adapter,package_manager,inventory,snapshot,scheduler,source,elevation}.py`. abc.ABC + @abstractmethod, value types przy interfejsach (ScheduleSpec, SnapshotInfo, SourceMetadata, ElevationResult, AdapterCapability flag) |
| M2.3 — adapter_factory + JSON Schema export | ⏳ pending | Następna sesja |
| M2.4 — Sidecar reader (file I/O + locking + recovery) | ⏳ pending | |
| M2.5 — i18n loader (port z macOS bash, 7 języków) | ⏳ pending | |
| M2.6 — Contract tests w `tests/contract/` | ⏳ pending | |
| M2.7 — Migracja `app/backend/*.py` → `core/ascendo/{dashboard,orchestrator,...}` | ⏳ pending | Mechanical refactor |

### FAZ 1-4 (analiza)
Wszystkie ✅ ukończone, decyzje zapisane wyżej w sekcji "Reference".

---

## Next Steps (do wykonania w następnej sesji)

### Krok 1 — User: pierwszy commit na branchu + push (WSZYSTKO M1.2-M1.6 razem)

```powershell
cd D:\Dev_Env\ascendo

# Verify remote is GitHub:
git remote -v

# Stage everything new from M1.2-M1.6:
git add .gitattributes .gitignore .markdownlint.json .pre-commit-config.yaml
git add HANDOFF.md LICENSE CHANGELOG.md CONTRIBUTING.md SECURITY.md README.md
git add pyproject.toml
git add core/ adapters/ contrib/ plugins/_template/ plugins/agent-clis/ plugins/dell-driver-update/ plugins/nvidia-driver-update/ plugins/README.md
git add ui/ packaging/ website/ tests/ docs/architecture/ docs/README.md
git add scripts/.gitkeep

# (Optional) clean up if any leftovers:
git status   # review what's staged

# Commit:
git commit -m "feat(m1): foundation — monorepo restructure + scaffold + ADRs

M1.0 — HANDOFF.md (Session 1)
M1.1 — clean working tree + pre-monorepo-restructure tag + branch (Session 1)
M1.2 — monorepo skeleton: core/, adapters/{ubuntu,windows,macos}/,
       contrib/, plugins/, ui/, packaging/, website/, tests/
M1.3 — top-level docs: LICENSE (MIT), CHANGELOG, CONTRIBUTING, SECURITY
M1.4 — pyproject.toml workspace (root + core + 3 adapters with hatchling
       build backend, ruff/mypy/pytest config, import-linter contracts)
M1.5 — seven ADRs (0001-monorepo, 0002-tauri, 0003-json-v1-sidecar,
       0004-python-core+native-scripts, 0005-six-layer-architecture,
       0006-two-tier-adapter-system, 0007-plugin-manifest-v1)
M1.6 — .gitattributes (LF/CRLF policy), .gitignore (rebrand+expansion),
       .markdownlint.json, .pre-commit-config.yaml (ruff, mypy, shellcheck,
       PSScriptAnalyzer, gitleaks, markdownlint, plugin-manifest validator)

Closes M1.0-M1.6. M1.7 (validate update-all.sh on Linux) is the
user-side smoke test after this commit lands."

# Push to GitHub:
git push -u origin restructure/monorepo
```

### Krok 2 — User: M1.7 walidacja na Linuksie

Po pushu — przeklonuj na Linuksie (mk-uP5520) i odpal:

```bash
git clone -b restructure/monorepo https://github.com/KasprowiczM/ascendo.git ~/ascendo-test
cd ~/ascendo-test
./update-all.sh --profile quick     # read-only, ~15s
./update-all.sh --dry-run           # podgląd bez wykonania
```

Cel: potwierdzić że istniejący update-all.sh nadal działa po
restrukturze (skrypty Linuksa są na razie nietknięte — będą przeniesione
do `adapters/ubuntu/scripts/` w M3+).

Jeśli coś się sypie — to nie M1, to M2 jeszcze nieskończone (ale powinno
być clean: na branchu nic nie zmienialiśmy w `update-all.sh`/`scripts/`,
tylko dodaliśmy nowe foldery + dokumenty).

### Krok 3 — User: commit M2.1 + M2.2

```powershell
cd D:\Dev_Env\ascendo

git add core/ascendo/models/ core/ascendo/interfaces/
git add HANDOFF.md

git status   # weryfikacja: 14 nowych plików .py + HANDOFF.md modified

git commit -m "feat(m2): core models + interfaces (M2.1 + M2.2)

M2.1 — Pydantic v2 models for ascendo/v1 sidecar contract:
  core/ascendo/models/{host,run,package,result,sidecar}.py
  - HostInfo / RunInfo / Sidecar (frozen historical records)
  - Item with version triplet (current/target/resolved)
  - ItemEvidence for unknown-version suppression
  - ItemRollback for 3-tier rollback (method/snapshot_id/instructions)
  - SidecarSchema enum accepts both ascendo/v1 + ubuntu-aktualizacje/v1
  - Validators: reverse-time, summary/items consistency

M2.2 — Six core interfaces + IAdapter aggregate:
  core/ascendo/interfaces/{package_manager,inventory,snapshot,
                          scheduler,source,elevation,adapter}.py
  - abc.ABC + @abstractmethod (explicit, runtime-checked)
  - IPackageManager.run_phase returns parsed Sidecar
  - IElevation enforces argv-only + allow-list (T4 mitigation)
  - ISource.verify_signature centralizes T2/T3 mitigation
  - AdapterCapability flag with TIER_1_FULL preset
  - Value types (ScheduleSpec, SnapshotInfo, SourceMetadata) live
    next to their interfaces, not in models/

Smoke-tested live: imports work, sidecar round-trips, legacy schema
accepted, validators reject malformed payloads, ABCs prevent direct
instantiation.

Refs ADR-0003, ADR-0005."

git push
```

### Krok 4 — Następna sesja: M2.3 + M2.4 + M2.6

Otwórz nową sesję i powiedz „kontynuuj M2 od M2.3". W kolejnej sesji
zrobię:
- **M2.3** — `adapter_factory` (OS detection + adapter selection przez
  entry_points) + JSON Schema export do `docs/architecture/schemas/sidecar.v1.schema.json`
- **M2.4** — Sidecar reader z file I/O + flock (Linux/macOS) +
  LockFile (Windows) + recovery z partial sidecar
- **M2.6** — Contract tests w `tests/contract/` — fixtures z prawdziwych
  pre-merge sidecarów (Ubuntu_Aktualizacje `logs/runs/*`) + Pydantic
  validation pass

M2.5 (i18n loader, port macOS bash do Pythona) i M2.7 (migracja
`app/backend/*.py` → `core/ascendo/{dashboard,orchestrator,...}`) idą
w osobnych sesjach — to mechaniczny refactor, nie design.

---

## Key Files & Locations

### Lokalne foldery (mounted w Cowork)

- `D:\Dev_Env\ascendo` — **TUTAJ PRACUJEMY** (klon Ubuntu_Aktualizacje, branch restructure/monorepo)
- `D:\Dev_Env\Ubuntu_Aktualizacje` — oryginał (parent klonu, **nie modyfikuj** — to backup + reference)
- `D:\Dev_Env\Aktualizacje-W11-Dell5520` — Windows repo (reference dla portu w M3)
- `D:\Dev_Env\Aktualizacje_MAC` — macOS repo (reference dla portu w M5)

### GitHub repos

- **Nowy (target):** `https://github.com/KasprowiczM/ascendo.git`
- **Stare (do archiwizacji po release):**
  - `Ubuntu_Aktualizacje` (na GitHub user `KasprowiczM`?)
  - `Aktualizacje-W11-Dell5520`
  - `Aktualizacje_MAC`

### Ważne istniejące pliki w `D:\Dev_Env\ascendo` (reference dla migracji)

- `update-all.sh` — orchestrator główny (zostanie w `adapters/ubuntu/` w FAZIE B M1)
- `app/backend/*.py` — FastAPI core (do rozszczepienia na `core/ascendo/{dashboard,orchestrator,models,inventory,audit}/` w M1.B)
- `app/frontend/*` — vanilla SPA (move 1:1 do `ui/frontend/`)
- `app/tauri/*` — Tauri shell (move + rozszerzenie 3 OS w M4)
- `lib/_json_emit.py` — Python JSON emitter (move do `core/ascendo/utils/`)
- `lib/json.sh` — bash wrapper (move do `adapters/ubuntu/lib/`)
- `lib/*.sh` — Linux-specific utilities (move do `adapters/ubuntu/lib/`)
- `scripts/<cat>/{check,plan,apply,verify,cleanup}.sh` — Linux phase scripts (move do `adapters/ubuntu/scripts/`)
- `bin/ascendo` — bash CLI router (refaktor → Typer w `core/ascendo/cli/`)
- `branding/{icon,logo}.svg` — branding (zostaje, dodać `.ico` i `.icns`)
- `dev-sync/dev_sync_core.py` — cross-OS dev-sync logic (przeniesie do `core/ascendo/devsync/`)
- `i18n/{en,pl}.txt` — częściowy i18n (do rozszerzenia o 5 języków z macOS)
- `plugins/example/` — scaffold (rename do `plugins/_template/`)
- `config/*` — user-facing config (zostaje 1:1)
- `tests/*` — split na `tests/cross-cut/`, `adapters/ubuntu/tests/`, `core/tests/`

---

## Workflow Conventions

### Git
- **Branch strategy:** `main` (stable), `restructure/monorepo` (current dev),
  feature branches z `feat/<topic>` lub `fix/<topic>` po zakończeniu M1
- **Commit messages:** Conventional Commits — `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`
- **No force push** na branchach z historią
- **Tag konwencja:** `v0.1.0` dla releases, `<phase>-<step>` dla checkpoints
  (np. `pre-monorepo-restructure`, `m1-foundation-complete`)

### Cross-OS
- `core.autocrlf=false` w repo
- `.gitattributes`: `*.sh` LF, `*.py` LF, `*.md` LF, `*.ps1` CRLF, `*.psm1` CRLF
- Wszystkie pliki UTF-8 (no BOM)
- Path handling przez `pathlib.Path` w Pythonie, **nigdy** stringi z `/`

### Cowork session protocol
- **Ja (Claude Cowork) NIE mogę uruchamiać `git`** (bash sandbox to read-only
  na mounted folder). Operacje git zawsze są dla user w PowerShell.
- **Ja mogę:** Read/Write/Edit pliki, Glob/Grep, bash w trybie read-only
  (git status, git log, git diff działa, git checkout/commit/tag NIE)
- **User wykonuje:** wszystkie commits, tags, branche, push, remote operations
- Każda sesja **zaczyna się** od read HANDOFF.md (Current State + Next Steps)
- Każda sesja **kończy się** updated `## Current State` + `## Session Log` +
  user wykonuje commit + push (lub przynajmniej commit)

### Code style
- Python: ruff format, mypy --strict (na core/), Pydantic v2
- Bash: shellcheck, set -euo pipefail, posix-compatible gdzie się da
- PowerShell: PSScriptAnalyzer warnings = errors, PS 5.1 + 7.x compat
- Markdown: prettier-compatible (linewrap 80-100 chars dla prozy)

---

## Otwarte decyzje / pending decisions

Te wymagają decyzji w future sessions, ale teraz nie blokują:

1. **Język core: Python (zatwierdzony w FAZIE 2 jako default), Go/Rust w przyszłości** — re-evaluate po M3 jeśli PyInstaller bundle za duży lub antivirus problemy
2. **Code signing certyfikaty:** ~$500/rok łącznie (Apple Developer ID $99 + Authenticode $300-500). Decyzja w M6.
3. **Domena:** brak (zostajemy na `KasprowiczM.github.io/ascendo` lub `ascendo.github.io` jeśli zarezerwujesz organizację). Decyzja po v0.2.0.
4. **PyInstaller vs Nuitka:** PyInstaller default. Re-evaluate po M3 jeśli bundle weight problem (Nuitka mniejszy ale dłużej kompiluje).
5. **Plugin signing:** sigstore (open-source friendly) — eksperyment w M6.
6. **Refactor monolithic `update_internet_apps.sh` (1460 LOC z macOS):**
   docelowa struktura `_apps.toml` + `handlers/{github_dmg,keystone,sparkle,direct_url}.sh`. M5.

---

## Architectural Decisions Reference (skompresowane uzasadnienia)

### Dlaczego Wariant A (Python core + native scripts adapters)?

- 90% reuse istniejącego kodu Ubuntu/Ascendo (FastAPI backend, JSON contract, plugin loader, scheduler, snapshots)
- 100% reuse PowerShell hidden gems (column parser, unknown-version suppression, exit-code mapping)
- Time-to-market 6-8 tyg. vs 4-9 mies. dla pełnej Pythonizacji
- Granica core↔adapter naturalna (JSON v1 sidecar contract)
- Łatwiej dodać macOS — 4. implementacja interfejsów, nie zmiana core
- Otwartość na future migration do Go/Rust (kontrakt zewnętrzny zostaje)

### Dlaczego Tauri (a nie Electron, .NET MAUI, WinUI3)?

- **Już jest w repo** (`app/tauri/`) — nie wymyślamy od nowa
- Cross-OS native (WebView2 Win, WKWebView mac, WebKitGTK Linux)
- Mały bundle (~15-30 MB vs ~100+ MB dla Electron)
- Rust shell przy minimalnej powierzchni (~80 LOC) — niskie maintenance
- Repo `app/tauri/README.md` explicit mówi: „If you need a fully native binary later, swap the webview URL for an embedded static SPA and port the API to a Rust HTTP framework — the JSON contract stays unchanged"

### Dlaczego monorepo (a nie multi-repo)?

- Atomic changes cross-component (np. modyfikacja JSON v1 contract w core + wszystkie adaptery jednym commitem)
- Jeden brand, jeden GitHub URL — open-source visibility
- Jeden CI/CD pipeline
- Łatwiejszy onboarding contributorów
- macOS dołącza jako kolejny folder, nie kolejne repo
- Zero synchronization overhead między adapter wersjami

### Dlaczego dwa tiers adapterów (Tier 1 / Tier 2)?

- **Niska barrier-to-entry** dla community (Tier 2 = manifest + scripts, koniec)
- **Wysoki standard** dla supported OS (Tier 1 = full pack)
- Promotion path: contrib → adapters po sprawdzeniu w boju
- Naturalne rozszerzenie: FreeBSD, Fedora, ChromeOS jako Tier 2 community

### Dlaczego plugin Anthropic-CLIs (a nie core)?

- Open-source neutralność (nie faworyzujemy Anthropic)
- Pluginy są first-class abstraction — używajmy ich
- Agent CLIs zmieniają się co miesiąc — izolacja w pluginie = niezależne wersjonowanie
- Easy extension: Cursor, Aider, Continue.dev = nowy plugin, nie zmiana core

---

## Session Log (UPDATE after each session)

### Sesja 3 — 2026-05-01

**Cel:** Po commit M1, ruszyć M2 — interfejsy + Pydantic modele.

**Zrobione:**
- **M2.1 Sidecar Pydantic v2 modele:** `core/ascendo/models/`
  - `host.py` — `HostInfo`, `OperatingSystem` enum (Tier 1: linux_ubuntu/
    windows/macos + 4 Linux distros + unknown), `ElevationMethod` enum.
    Frozen, `extra='forbid'`.
  - `run.py` — `RunInfo`, `Phase` enum (5 faz: check/plan/apply/verify/
    cleanup), `PhaseStatus` enum, `Trigger` enum, `ProfileName` constrained string.
  - `package.py` — `Package`, `ItemSource`, `ItemEvidence` (appx_version,
    registry_version, dpkg_version, binary_version + path + sha256 — pełne
    wsparcie unknown-version suppression), `ItemRollback` (3-poziomowy:
    method per-item / snapshot_id / instructions_path), `SourceType` enum
    (16 wariantów).
  - `result.py` — `Item` (z triplet wersji: current/target/resolved), `ItemStatus`
    (z `up_to_date`, `planned`, `partial` rozróżnionymi od `success`),
    `Summary` z metodą `is_clean()`, `Message` + `MessageLevel`.
  - `sidecar.py` — `Sidecar` top-level, `SidecarSchema` enum z literałami
    `ascendo/v1` + `ubuntu-aktualizacje/v1` (backward-compat per ADR-0003),
    `ToolInfo`, validatory (reverse-time, summary/items consistency,
    schema recognized), `parse_sidecar()` helper.
- **M2.2 Six core interfaces + IAdapter:** `core/ascendo/interfaces/`
  - `package_manager.py` — `IPackageManager` (run_phase z item_filter),
    `ManagerError`.
  - `inventory.py` — `IInventory` (list_installed, emit_sidecar).
  - `snapshot.py` — `ISnapshot` (backend slug, create/list/get) +
    `SnapshotInfo` model + `SnapshotError`.
  - `scheduler.py` — `IScheduler` (install/uninstall/list/get/trigger) +
    `ScheduleSpec` model + `SchedulerError`.
  - `source.py` — `ISource` (list_known_sources, verify_signature) +
    `SourceMetadata` + `TrustTier` enum + `SourceVerificationError`. T2/T3
    threat-model mitigation centralized.
  - `elevation.py` — `IElevation` (register_allowlist + run argv-only),
    `ElevationResult` + `ElevationDenied` + `ElevationTimeout`. T4 threat-
    model mitigation: shell strings rejected, allow-list enforced.
  - `adapter.py` — `IAdapter` aggregate root + `AdapterCapability` flag
    (TIER_1_FULL preset). `health_check()` returns dict for `ascendo doctor`.
- **Smoke test (live):** zaimportowane wszystkie modele + interfejsy,
  zbudowany realny apply sidecar (winget upgrade PowerShell), sprawdzone:
  legacy schema accepted, reverse-time rejected, summary/items mismatch
  rejected, IAdapter not instantiable. Wszystko OK.

**Co poszło źle:** nic — czysta sesja po Sesji 2 recovery.

**Czego się nauczyliśmy:**
- Pydantic v2 `ConfigDict(frozen=True, extra='forbid')` to dobry default
  dla immutable historycznych rekordów. Mutable (`Item` w trakcie rozwiązywania)
  tylko gdy konkretnie potrzebne.
- `Annotated[str, StringConstraints(...)]` jest czystszy niż `Field(...,
  pattern=...)` dla powtarzanych typów (ProfileName, ToolName, ScheduleExpr,
  PackageId, VersionStr).
- `enum.Flag` z bitwise OR (`AdapterCapability.TIER_1_FULL = PACKAGE_MANAGEMENT
  | INVENTORY | ...`) eleganckie do "co adapter potrafi".
- Trzymanie value types (ScheduleSpec, SnapshotInfo, SourceMetadata) razem
  z interfejsem co je używa — lepsze niż wszystko w `models/`. Modele to
  RUNTIME data; interface value types to KONFIGURACJA tych modeli.

**Decyzje podjęte:**
- abc.ABC + @abstractmethod (a NIE typing.Protocol) dla 6 interfejsów.
  Powód: explicit inheritance + runtime safety + łatwiejszy grep.
- Sidecar jest immutable (frozen=True) — historyczny zapis.
- IElevation enforce'uje argv-only + allow-list jako twardy kontrakt
  (T4 mitigation z threat modelu). Implementacje MUSZĄ odrzucić shell
  strings — to nie jest soft guidance.
- AdapterCapability.TIER_1_FULL jest preset — Tier 2 adapter może
  zadeklarować `PACKAGE_MANAGEMENT | INVENTORY` only (no snapshots, no
  scheduling), co odpowiada per-OS scaffold w `contrib/`.

**Następna sesja:** M2.3 (adapter_factory + JSON Schema export) +
M2.4 (sidecar reader z locking) + M2.6 (contract tests). M2.5 (i18n)
i M2.7 (backend migration) mogą iść równolegle lub w osobnej sesji.

---

### Sesja 2 — 2026-05-01

**Cel:** Dokończyć M1 (poprzednia sesja zawiesiła się w trakcie — wymagała
recovery + dokończenia M1.4 + M1.5).

**Zrobione:**
- **Recovery:** Naprawione `.git/HEAD` które było skorumpowane przez
  truncated write z hung session (zawierało `ref: refs/heads/restr` zamiast
  `ref: refs/heads/restructure/monorepo`). Przywrócone do poprawnego stanu.
- **Audit M1.2/M1.3/M1.6:** zweryfikowane że hung session zdążyła zapisać
  poprawnie (i kompletnie) wszystkie pliki — `.gitattributes`,
  `.pre-commit-config.yaml`, `.markdownlint.json`, rozszerzony `.gitignore`,
  `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, oraz cały
  szkielet folderów `core/`, `adapters/{ubuntu,windows,macos}/`, `contrib/`,
  `plugins/`, `ui/`, `packaging/`, `website/`, `tests/`,
  `docs/architecture/{README,templates/adr-template}`.
- **M1.4:** Napisany pyproject.toml workspace na 4 lokalizacjach:
  - `pyproject.toml` (root) — workspace coordinator + shared tool config
    (ruff, mypy, pytest, coverage)
  - `core/pyproject.toml` — pakiet `ascendo` (Layer 4) z hatchling backend,
    Pydantic v2 + FastAPI + Typer + jsonschema, importlinter contracts
    (Core MUST NOT import from adapters)
  - `adapters/ubuntu/pyproject.toml` — `ascendo-ubuntu`
  - `adapters/windows/pyproject.toml` — `ascendo-windows` (z pywin32)
  - `adapters/macos/pyproject.toml` — `ascendo-macos` (deferred do M5)
- **M1.5:** Napisane 7 ADR-ów w `docs/architecture/`:
  - `0001-monorepo-with-adapters.md` — uzasadnienie monorepo
  - `0002-tauri-as-desktop-shell.md` — Tauri 2.x jako desktop UI
  - `0003-json-v1-sidecar-contract.md` — JSON `ascendo/v1` schemat + reader
  - `0004-python-core-with-native-script-adapters.md` — Wariant A
  - `0005-six-layer-architecture.md` — 6 warstw + dependency rules
  - `0006-two-tier-adapter-system.md` — Tier 1 / Tier 2 + promotion path
  - `0007-plugin-manifest-v1.md` — manifest TOML + plugin SDK boundary

**Co poszło źle:**
- Poprzednia sesja (planowana jako Sesja 1 ciąg dalszy) zawiesiła się
  w trakcie pracy — ostatni write na `.gitignore` lub `.git/HEAD` był
  truncated. Recovery zajął ~2 minuty (zidentyfikowanie przez `cat -A
  .git/HEAD` + przywrócenie poprawnej wartości).

**Czego się nauczyliśmy (operational):**
- Bash sandbox w tej sesji **nie** jest już read-only — udało się
  wykonać `printf > .git/HEAD`. To rozszerza wachlarz operacji recovery,
  ale nadal git commits/push/tag rezerwujemy dla user'a (intencja:
  przegląd i intencjonalność zmian historii git po stronie człowieka).
- HANDOFF.md jako single source of truth zadziałał — przyjście na nowo
  do tematu i dokończenie M1 było mechaniczne, bez utraty kontekstu.

**Decyzje podjęte:**
- pyproject layout: per-package (root + 4 packages), nie single mega-toml.
  Zgodne z `CONTRIBUTING.md` instrukcją `pip install -e core/[dev]`.
- Build backend: hatchling (lekki, czysta konfiguracja, dobrze radzi
  sobie z włączaniem native scripts do wheela jako data files).
- import-linter zamiast manualnych testów: deklaratywny, w CI wystarczy
  `lint-imports` żeby sprawdzić wszystkie kontrakty z ADR-0005.
- ADR-y są **długie i opinionated** — celowo. Każdy zawiera Context +
  Decision + (Positive/Negative/Neutral consequences) + Alternatives.
  Open-source kontrybutorzy będą potrzebować zrozumieć "dlaczego",
  nie tylko "co".

**Następna sesja:** M2 Core skeleton (interfaces, models, contract tests).

---

### Sesja 1 — 2026-04-30

**Cel:** Analiza, projekt, plan wdrożenia.

**Zrobione:**
- FAZA 1: Mapowanie 3 repo (Ubuntu_Aktualizacje, Aktualizacje-W11-Dell5520, Aktualizacje_MAC)
- FAZA 2: Wybór Wariantu A (Python core + native scripts + Tauri)
- FAZA 3: Pełna architektura (4 podfazy: struktura, JSON v1, dystrybucja, security/rollback/migration)
- FAZA 4: 6 milestone'ów (M1-M6) z time-budgetami
- M1.0: Ten dokument (HANDOFF.md)
- M1.1: Clean working tree, tag `pre-monorepo-restructure`, branch `restructure/monorepo`
- Setup: nowe GitHub repo `KasprowiczM/ascendo`, klon lokalny `D:\Dev_Env\ascendo`,
  `core.autocrlf=false`, problem CRLF/LF rozwiązany

**Co poszło źle:**
- Mój sub-agent w FAZIE 1 przegapił folder `app/tauri/` — naprawione w
  iteracji, dodano Tauri jako desktop UI dla 3 OS
- Pierwsza próba `git checkout -- .` z bash sandbox failed (read-only mount)
  — workaround: PowerShell po stronie user'a

**Czego się nauczyliśmy (operational):**
- Bash sandbox w Cowork to **read-only** dla mounted folderów. Wszystkie
  modyfikacje plików przez Read/Write/Edit tools (te działają write).
  Wszystkie operacje git po stronie user'a (PowerShell na Windows).
- Cross-OS repo wymaga `core.autocrlf=false` + `.gitattributes` od dnia 0.

**Decyzje podjęte:**
- Wariant architektury: A (Python core + PS/Bash adapters + Tauri 3 OS)
- Strategia repo: monorepo, rename Ubuntu_Aktualizacje → ascendo (lokalnie
  klon, GitHub nowe repo)
- macOS priorytet: wysoki, projektujemy z myślą o nim
- 100% native Windows, no WSL2
- Open-source target, MIT license
- Plugin tier system: Tier 1 (`adapters/`, `plugins/`) + Tier 2 (`contrib/`)
- Schema: `ubuntu-aktualizacje/v1` → `ascendo/v1` (backward-compatible reader)
- Stack core: Python (FastAPI + Typer + Pydantic v2 + SQLite)
- PyInstaller na Windows/macOS, system Python na Linux (.deb dep)
- CI: GitHub Actions matrix 3 OS

**Następna sesja:** Continue M1 od M1.6 (.gitattributes + .gitignore +
pre-commit), potem M1.2 (foldery), M1.3 (top-level docs), M1.4 (pyproject),
M1.5 (ADRs).

---

## Quick Resume Checklist (dla nowej sesji)

Jeśli zaczynasz nową sesję Cowork, zrób te kroki w kolejności:

- [ ] Zamontuj `D:\Dev_Env\ascendo` w Cowork (`request_cowork_directory`)
- [ ] Przeczytaj **całą** ten plik (`HANDOFF.md`)
- [ ] Sprawdź `git status` i `git branch --show-current` w `D:\Dev_Env\ascendo` — zweryfikuj że jesteś na `restructure/monorepo`
- [ ] Sprawdź sekcję `## Current State` powyżej — co już zrobione, co dalej
- [ ] Sprawdź sekcję `## Next Steps` — konkretne akcje
- [ ] Sprawdź `## Open decisions` — czy któraś nie jest blokująca
- [ ] Zaktualizuj sekcję `## Current State` na początek sesji ze starting point
- [ ] Wykonuj zaplanowane M1.x kroki
- [ ] Na końcu sesji: zaktualizuj `## Current State` + dodaj wpis do `## Session Log`
- [ ] User: `git add HANDOFF.md && git commit -m "docs(handoff): session N update" && git push`

---

## Kontakty / referencje

- **GitHub repo target:** https://github.com/KasprowiczM/ascendo
- **User:** Gaipro (gaipro.mk@gmail.com)
- **Maszyna referencyjna Windows:** DP5520WMK (Dell Precision 5520, Win 11 Pro Build 26200)
- **Maszyna referencyjna Linux:** mk-uP5520 (Ubuntu 24.04, Dell Precision 5520)

---

**End of HANDOFF.md** — jeśli coś jest niejasne lub brakuje, ZGŁOŚ to w
sekcji Session Log następnej sesji i ten plik zaktualizujemy. Cel: każda
przyszła sesja może wrócić tutaj i kontynuować bez utraty kontekstu.
