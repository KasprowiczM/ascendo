# Phase contract (schema v1)

Wszystkie kategorie aktualizacji eksponują **5 idempotentnych faz**:

| Faza      | Mutuje? | Wymaga sudo | Cel |
|-----------|---------|-------------|-----|
| `check`   | nie     | nie         | szybki health snapshot, lista outdated |
| `plan`    | nie     | nie         | dokładny plan zmian (from→to) |
| `apply`   | tak     | zależy      | jedyny krok mutujący |
| `verify`  | nie     | nie         | post-apply walidacja |
| `cleanup` | tak     | zależy      | autoremove / prune (gdzie ma sens) |

## Layout

```
scripts/<category>/<phase>.sh
config/categories.toml          # taksonomia
config/profiles.toml            # quick / safe / full
schemas/phase-result.schema.json
lib/json.sh                     # emitter (bash)
lib/_json_emit.py               # backend Pythona dla emittera
lib/orchestrator.sh             # runner per kategoria, agreguje run.json
```

## Kontrakt CLI faz

Każdy `scripts/<cat>/<phase>.sh`:
- czyta `JSON_OUT` (ścieżka pliku sidecar) i `LOG_FILE` (plain log) z env,
- woła `json_init <kind> <category>`, `json_register_exit_trap "$JSON_OUT"`,
- używa `json_count_ok|warn|err`, `json_add_item`, `json_add_diag`, `json_set_needs_reboot`,
- zwraca exit code wg tabeli niżej.

## Exit codes

| Code | Znaczenie |
|------|-----------|
| 0    | success |
| 1    | warn — nie-krytyczne ostrzeżenia |
| 2    | bad usage / config |
| 10   | precondition failed (brak narzędzia / sudo) |
| 11   | lock contention |
| 12   | timeout |
| 20   | apply failed, system w stanie znanym |
| 30   | apply failed, system w stanie nieznanym → CRITICAL |
| 75   | already running (project flock) |

## JSON sidecar (schema v1)

Patrz `schemas/phase-result.schema.json`. Walidator: `tests/validate_phase_json.py`.

```json
{
  "schema": "ascendo/v1",
  "kind": "verify",
  "category": "apt",
  "host": "mk-uP5520",
  "started_at": "2026-04-29T22:02:00Z",
  "ended_at":   "2026-04-29T22:04:13Z",
  "exit_code": 0,
  "summary": {"ok": 12, "warn": 1, "err": 0},
  "items": [
    {"id":"apt:upgrade:firefox","action":"upgrade","from":"131","to":"132","result":"ok","duration_ms":8412}
  ],
  "diagnostics": [
    {"level":"warn","code":"APT-PHASED","msg":"remmina deferred"}
  ],
  "log_path": "logs/runs/<run-id>/apt/apply.log",
  "needs_reboot": false,
  "advisory": []
}
```

## Run-level summary

`logs/runs/<run-id>/run.json` (schema `ascendo/run/v1`) agreguje wszystkie fazy:

```json
{
  "schema": "ascendo/run/v1",
  "run_id": "20260429T203101Z-29243",
  "ended_at": "2026-04-29T20:31:17Z",
  "status": "ok",
  "needs_reboot": false,
  "phases": [
    {"category":"apt","kind":"check","exit_code":0,"summary":{...},"json":"apt/check.json"}
  ]
}
```

## Profile

- `quick` — tylko `check` we wszystkich kategoriach user-space (read-only sweep, ~kilkanaście sekund)
- `safe`  — pełen 5-fazowy pipeline bez `drivers`
- `full`  — wszystko (drivers wymaga manual-confirm)

## CLI

```bash
./update-all.sh                          # full profile, all phases
./update-all.sh --profile quick          # read-only check
./update-all.sh --profile safe           # bez drivers
./update-all.sh --only apt --phase check
./update-all.sh --dry-run                # tylko check+plan
./update-all.sh --no-drivers --no-notify
```

Backward-compat (`--only`, `--dry-run`, `--nvidia`, `--no-drivers`, `--no-notify`) zachowane.

## Walidacja lokalnie

```bash
bash -n update-all.sh scripts/*/*.sh lib/*.sh
./update-all.sh --profile quick --no-notify
python3 tests/validate_phase_json.py
bats tests/bash/test_json_emit.bats        # jeśli bats zainstalowany
```
