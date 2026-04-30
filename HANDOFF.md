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
2026-04-30 — Sesja 1 (Cowork)

### Branch & commits
- **Branch:** `restructure/monorepo`
- **Tag rollback:** `pre-monorepo-restructure` (commit 36bc6f0)
- **Last commit on branch:** identyczny z `pre-monorepo-restructure` (nic jeszcze nie zacommitowane na branch)
- **Origin:** `https://github.com/KasprowiczM/ascendo.git` (NEW, nie pushowane jeszcze)
- **Backup origin (ojciec klonu):** `D:\Dev_Env\Ubuntu_Aktualizacje` (lokalny)

### Working tree
- Clean, poza:
  - `HANDOFF.md` (untracked — ten plik) — do dodania w pierwszym commit
  - `.write-test` (untracked, leftover testowy) — do usunięcia (`Remove-Item .write-test`)

### Konfiguracja repo
- `core.autocrlf=false` (set w tym repo, naprawia CRLF/LF problem)
- Brak `.gitattributes` jeszcze — będzie utworzony w M1.6 zanim ktokolwiek inny sklonuje

### M1 Progress (Tasks #8-#14, #15)

| Task | Status | Notes |
|---|---|---|
| M1.0 — HANDOFF dokument | ✅ done | Ten plik |
| M1.1 — git tree clean + tag + branch | ✅ done | Wykonane przez user w PowerShell |
| M1.2 — Szkielet folderów monorepo | ⏳ pending | Następna sesja |
| M1.3 — Top-level docs (README/LICENSE/CHANGELOG/SECURITY/CONTRIBUTING) | ⏳ pending | |
| M1.4 — pyproject.toml workspace | ⏳ pending | |
| M1.5 — 7 ADR-ów w docs/architecture/ | ⏳ pending | |
| M1.6 — .gitattributes + .gitignore + pre-commit | ⏳ pending | **Priorytet — przed jakimkolwiek innym klonem** |
| M1.7 — Walidacja `update-all.sh` | ⏳ pending | User-side test po M1.6 |

### FAZ 1-4 (analiza)
Wszystkie ✅ ukończone, decyzje zapisane wyżej w sekcji "Reference".

---

## Next Steps (do wykonania w następnej sesji)

### Krok 1 — User: zaktualizuj remote do nowego GitHub repo

```powershell
cd D:\Dev_Env\ascendo
git remote -v   # zobacz current (wskazuje na lokalny D:\Dev_Env\Ubuntu_Aktualizacje)
git remote set-url origin https://github.com/KasprowiczM/ascendo.git
git remote -v   # verify (powinien być GitHub URL)
```

### Krok 2 — User: pierwszy commit na branchu + push

```powershell
# Posprzątaj test file:
Remove-Item .write-test -ErrorAction SilentlyContinue

# Dodaj handoff:
git add HANDOFF.md
git commit -m "docs(handoff): initial implementation plan + state snapshot

- Captures decisions from FAZ 1-4 (architecture, milestones, contracts)
- Documents current branch state (restructure/monorepo)
- Provides resume-from-crash capability
- M1.0 task complete

See HANDOFF.md for the full implementation plan."

# Push do GitHub (pierwsze:
git push -u origin restructure/monorepo
```

### Krok 3 — Następna sesja: ja kontynuuję od M1.6

Otwórz nową sesję Cowork, zamontuj `D:\Dev_Env\ascendo` (i opcjonalnie
trzy stare repo dla reference), powiedz „kontynuuj M1 od M1.6". Przeczytam
HANDOFF.md, zaktualizuję sekcję `Current State` na rozpoczęcie sesji,
wykonam M1.6 → M1.2 → M1.3 → M1.4 → M1.5.

**Kolejność M1.6 ZANIM M1.2-M1.5** jest świadoma:
- `.gitattributes` musi być pierwsze, żeby zapobiec CRLF problemowi przy
  jakimkolwiek nowym klonie (nawet moim w next session)
- `.gitignore` rozszerzenie też pierwsze, żeby `.venv/`, `target/`,
  `dist/` nie weszły do repo

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
