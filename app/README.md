# Ascendo - Unified Updates Dashboard

Lokalny dashboard (FastAPI + vanilla SPA) dla pakietu skryptów aktualizacji.
Działa na `127.0.0.1:8765`, czyta JSON sidecary generowane przez fazowe skrypty.

## Architektura

```
app/
├─ backend/                # FastAPI service
│  ├─ main.py              # REST endpoints + SSE log stream
│  ├─ runner.py            # subprocess launcher dla update-all.sh
│  ├─ db.py                # SQLite history store
│  ├─ config.py            # ładuje config/categories.toml + profiles.toml
│  └─ __main__.py          # `python3 -m app.backend`
├─ frontend/               # static SPA (no build step)
│  ├─ index.html
│  ├─ style.css
│  └─ app.js
└─ pyproject.toml
```

## Uruchomienie ad-hoc

```bash
pip install --user fastapi uvicorn pydantic
python3 -m app.backend          # serwuje http://127.0.0.1:8765
xdg-open http://127.0.0.1:8765
```

## Instalacja jako user-service

```bash
bash systemd/user/install-dashboard.sh
# enable + start; po reboot chodzi automatycznie
xdg-open http://127.0.0.1:8765
```

## Zmienne środowiskowe

| Var                     | Default                             |
|-------------------------|-------------------------------------|
| `UA_DASHBOARD_HOST`     | `127.0.0.1`                         |
| `UA_DASHBOARD_PORT`     | `8765`                              |
| `UA_REPO_ROOT`          | repo z którego serwowany jest backend |
| `UA_DB_PATH`            | `$XDG_DATA_HOME/ascendo/history.db` |

## REST API

| Endpoint                                            | Co robi |
|-----------------------------------------------------|---------|
| `GET /health`                                       | health check |
| `GET /preflight`                                    | obecność narzędzi + reboot flag |
| `GET /categories`                                   | lista z `config/categories.toml` |
| `GET /profiles`                                     | lista profili |
| `GET /git/status`                                   | branch / dirty / ahead/behind |
| `GET /runs?limit=N`                                 | historia runów |
| `GET /runs/{id}`                                    | szczegóły runu |
| `POST /runs` `{profile,only,phase,dry_run}`         | start runu |
| `GET /runs/active`                                  | aktywny run |
| `POST /runs/active/stop`                            | SIGTERM aktywnego runu |
| `GET /runs/active/stream` (SSE)                     | live log eventy |
| `GET /runs/{id}/phase/{cat}/{phase}`                | sidecar JSON |
| `GET /runs/{id}/phase/{cat}/{phase}/log`            | plain log |

## Bezpieczeństwo

- Service słucha **tylko** na `127.0.0.1` z założenia. Nie wystawiaj na sieć.
- `POST /runs` uruchamia `update-all.sh` z uprawnieniami użytkownika; sudo
  pyta `pkexec`/cache na poziomie skryptów (jak w CLI).
- Brak autoryzacji na poziomie HTTP — dostęp = lokalny user. Dla wieloużytkownikowej
  maszyny skonfiguruj `Listen` na unix socket (TODO).
