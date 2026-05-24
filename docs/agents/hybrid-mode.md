# Hybrid mode: konsola + dashboard

Aplikacja **z założenia działa hybrydowo**. Każdy use-case dostępny zarówno
z linii poleceń, jak i z dashboardu — ten sam kontrakt fazowy (5 faz × JSON
sidecar v1) i ten sam katalog logów `logs/runs/<run-id>/`.

## Trzy ścieżki uruchomienia tej samej aktualizacji

| Co chcę zrobić | CLI (legacy) | CLI (nowy) | Dashboard |
|----------------|--------------|------------|-----------|
| Pełna aktualizacja | `./update-all.sh` | `./update-all.sh --profile full` | przycisk **Full update** |
| Tylko podgląd | `./update-all.sh --dry-run` | `./update-all.sh --profile full --dry-run` | **Full dry-run** |
| Tylko apt | `bash scripts/update-apt.sh` (legacy) | `./update-all.sh --only apt` | **Categories → apt → apply** |
| Tylko apt:check | n/a w legacy | `./update-all.sh --only apt --phase check` | **Categories → apt → check** |
| Snap refresh | `bash scripts/update-snap.sh` (legacy) | `./update-all.sh --only snap` | **Categories → snap** |
| Inventory APPS.md | `bash scripts/update-inventory.sh` | `./update-all.sh --only inventory` | (auto po runie) |
| Pre-apply snapshot | `bash scripts/snapshot/create.sh` | `./update-all.sh --snapshot` | Settings → snapshot toggle |
| Scheduler | `bash scripts/scheduler/install.sh …` | (n/a) | Settings → Install/Update timer |
| Git pull/push | `bash lib/git-push.sh push main` | (n/a) | **Sync screen** |
| Dev-sync export | `bash dev-sync-export.sh` | (n/a) | **Sync → Export (dry-run/real)** |

## Co znaczy „legacy zostaje”

- Wszystkie skrypty `scripts/update-{apt,snap,brew,npm,pip,flatpak,drivers,inventory}.sh` **pozostają w repo i działają standalone**.
- Można je odpalać niezależnie od `update-all.sh` i niezależnie od dashboardu.
- Nowe natywne fazy w `scripts/<cat>/<phase>.sh` są **rozszerzeniem**, nie zastąpieniem. Master `update-all.sh` używa nowych; CLI legacy używa starych.
- Dashboard wywołuje master `update-all.sh`, więc też używa nowych — ale można go ominąć całkowicie.

## Kiedy która ścieżka

- **Legacy CLI (`scripts/update-*.sh`)** — gdy chcesz ad-hoc, pojedynczy menedżer, bez orchestratora i bez generowania run-id/sidecara. Używają tych samych helperów (`lib/common.sh`, `lib/detect.sh`, `config/*.list`).
- **Nowy CLI (`update-all.sh --profile/--phase`)** — gdy chcesz spójny zestaw faz z JSON sidecarem (do parsowania przez Twoje narzędzia, do CI).
- **Dashboard** — gdy chcesz UI, historię, live log, scheduler, git/sync z guzika.

## Wspólne fundamenty

| Komponent | Używają wszystkie ścieżki |
|-----------|-------------------------|
| `config/*.list` | source of truth pakietów |
| `config/host-profiles/<host>/*.list` | overlay per-host (opcjonalny) |
| `lib/common.sh`, `lib/detect.sh`, `lib/repos.sh` | helpery |
| `lib/secrets.sh` | secret lookup z fallbackiem libsecret → .env.local |
| `flock` na `${XDG_RUNTIME_DIR}/ascendo.lock` | wzajemnie wykluczają się |
| `logs/master_*.log` (legacy) i `logs/runs/<id>/` (nowy) | równoległe katalogi logów |

## Test hybrydowości

```bash
# 1. Legacy CLI
bash scripts/update-snap.sh                      # standalone, generuje master_*.log

# 2. Nowy CLI
./update-all.sh --only snap --phase check         # generuje logs/runs/<id>/snap/check.json

# 3. Dashboard (wymaga venv)
bash systemd/user/install-dashboard.sh
xdg-open http://127.0.0.1:8765
# → Categories → snap → check
```

Wszystkie trzy odczytują z `config/snap-packages.list`, używają `lib/detect.sh`,
i nie wchodzą sobie w drogę dzięki `flock`.
