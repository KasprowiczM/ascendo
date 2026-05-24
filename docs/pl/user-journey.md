# Ascendo — ścieżki użytkownika

Mapa wszystkich realnych przejść przez produkt — od momentu sklonowania
repo aż po cofnięcie nieudanej aktualizacji. Każda ścieżka ma
**komendy**, **spodziewany rezultat** i **wyjścia awaryjne**.

> Wersja angielska: [`../en/user-journey.md`](../en/user-journey.md)

## 1. Świeży clone → pierwszy run (nowy komputer)

**Cel:** zamienić sklonowane repo na nowym Ubuntu w działający zestaw
aktualizacyjny bez niespodzianek.

```bash
git clone https://github.com/KasprowiczM/Ascendo
cd Ascendo
bash scripts/fresh-machine.sh
```

Co się dzieje, krok po kroku:

1. **Wybór języka** (interaktywnie) — English / Polski. Zapisany w
   `~/.config/ascendo/lang`. Używany dla CLI i dashboardu.
2. **Krok 1/5 preflight** — `scripts/preflight.sh` sprawdza Bash, Python,
   git, sudo, snap, brew (z ostrzeżeniami przy brakach).
3. **Krok 2/5 bootstrap** — przywraca prywatny overlay z Proton/rclone
   jeśli `.dev_sync_config.json` istnieje. Wywołuje `setup.sh --check`,
   więc **nic** nie jest jeszcze instalowane.
4. **Apps detect (tylko odczyt)** — wypisuje kolorową tabelę:
   `tracked` (w configu + zainstalowane), `detected` (zainstalowane ale
   nie w configu, kandydat do dodania), `missing` (w configu ale nie
   zainstalowane, kandydat do instalacji). **Nic nie jest zmieniane.**
5. **Krok 3/5 venv dashboardu** — `app/.venv/` + FastAPI.
6. **Krok 4/5 user-service** — `systemd --user` unit autostartu.
7. **Krok 5/5 weryfikacja** — finalny check stanu.

> **Ważne:** fresh-machine **nigdy nie instaluje pakietów z
> `config/*.list` automatycznie.** Mówi co brakuje; decyzję podejmujesz
> sam przez `bash scripts/apps/install-missing.sh`.

Po zakończeniu adres dashboardu to **<http://127.0.0.1:8765>**; jeśli
`~/.config/ascendo/onboarded.json` jeszcze nie istnieje,
pojawi się First-run wizard.

**Wyjścia awaryjne:**
- `--check-only` — tylko audyt, zero mutacji.
- `--no-dashboard` — pomiń venv + user-service.
- `--no-sync` — pomiń przywracanie overlay z Proton.
- `--lang en|pl` — nieinteraktywny wybór języka.

---

## 2. Codzienna aktualizacja

```bash
./update-all.sh                 # pełen profil
./update-all.sh --profile safe  # wszystko poza driverami/firmware
./update-all.sh --profile quick # tylko check (~15 s)
./update-all.sh --dry-run       # plan, bez mutacji
```

**Sudo:** pytane RAZ, cache'owane na cały run przez askpass helper. Brak
ponownych próśb nawet podczas długich faz apt.

**Live progress:** output każdego skryptu fazowego trafia na terminal i
do `logs/runs/<id>/<cat>/<phase>.log`. Linie typu
`[3/47] firefox 131 → 132` pojawiają się na żywo podczas apt:apply.

**Koniec runu:** kolorowa tabela podsumowująca, JSON sidecar per faza
(schema v1) i — jeśli ustawione `/var/run/reboot-required` — zasobne
pudełko `WYMAGANY RESTART` z jednoliniową komendą reboota.

---

## 3. Dodawanie nowej aplikacji

Użytkownik zainstalował narzędzie ręcznie i chce żeby Ascendo
zarządzało nim od teraz. Dwie ścieżki:

### CLI

```bash
# 1. Zobacz co jest wykryte ale nie-zarejestrowane
bash scripts/apps/detect.sh

# 2. Wybierz kategorię (apt|snap|brew|brew-cask|npm|pipx|flatpak)
bash scripts/apps/add.sh slack --category snap

# 3. Weryfikacja
bash scripts/apps/list.sh
```

### Dashboard

Zakładka `Apps` → wiersz na żółto (`detected`) → kliknij **+ Add to
config**. Pakiet od teraz jest `tracked` i wchodzi w każdy kolejny
`update-all.sh` run.

---

## 4. Wykrywanie aplikacji "spoza configu"

Sytuacja: ktoś zainstalował aplikacje przez `apt`/`snap`/`brew`
bezpośrednio. Chcemy je ujawnić i rozpoznać.

```bash
bash scripts/apps/detect.sh           # wynik dla człowieka
bash scripts/apps/detect.sh --json    # do jq / pipeline
```

Legenda:

| Stan      | Znaczenie |
|-----------|-----------|
| tracked   | jest w `config/*.list` ORAZ zainstalowane |
| detected  | zainstalowane ALE poza configiem — kandydat do `apps add` |
| missing   | w `config/*.list` ALE nie zainstalowane — kandydat do instalacji |

Pakiety untracked nigdy nie psują aktualizacji — są niewidoczne dopóki
ich nie zarejestrujesz.

---

## 5. Migracja na nowy komputer

```bash
# Na STARYM komputerze
bash dev-sync-export.sh        # push prywatnego overlay do Proton/rclone

# Na NOWYM komputerze
git clone https://github.com/KasprowiczM/Ascendo
cd Ascendo
bash scripts/fresh-machine.sh
```

Overlay (8 plików: `.env.local`, klucze ssh, `.claude/*`,
`.dev_sync_config.json`) jest przywracany automatycznie gdy Proton/rclone
są dostępne. Fresh-machine prowadzi resztę identycznie jak ścieżka 1.

Po zakończeniu fresh-machine, uruchom `bash scripts/apps/install-missing.sh`
aby zainstalować to co było w configu ale jeszcze nie na dysku.

---

## 6. Recovery (rollback ze snapshotu)

Aktualizacja się posypała? Cofamy się.

```bash
# Pre-apply snapshot zrobiony automatycznie (Settings → snapshot toggle)
# lub ręcznie: bash scripts/snapshot/create.sh "before risky upgrade"

bash scripts/snapshot/list.sh                 # co dostępne
bash scripts/snapshot/restore.sh <snap-id>    # zrób (zapyta o sudo)
sudo systemctl reboot                         # wstań na cofniętym stanie
```

Odpowiednik w dashboardzie: panel **Snapshots** → wiersz → **Restore**
→ confirm. Akcja loguje się do
`~/.local/state/ascendo/audit.log`.

---

## Mapa komend w skrócie

| Potrzeba | Komenda |
|----------|---------|
| Nowy komputer            | `bash scripts/fresh-machine.sh` |
| Pełna aktualizacja       | `./update-all.sh` |
| Szybki health check      | `./update-all.sh --profile quick` |
| Lista śledzonych aplikacji | `bash scripts/apps/list.sh` |
| Co wykryte/brakujące     | `bash scripts/apps/detect.sh` |
| Dodaj śledzoną aplikację | `bash scripts/apps/add.sh <pkg> --category <cat>` |
| Doinstaluj brakujące     | `bash scripts/apps/install-missing.sh` |
| Snapshot pre-apply       | `./update-all.sh --snapshot` |
| Rollback                 | `bash scripts/snapshot/restore.sh <id>` |
| Otwórz dashboard         | `xdg-open http://127.0.0.1:8765` |
| Zbuduj .deb              | `bash packaging/build-deb.sh` |
