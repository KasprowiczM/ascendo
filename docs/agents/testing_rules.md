# Testing Rules

## Minimum po każdej zmianie
- Uruchom kontrolę składni: `bash -n update-all.sh && bash -n scripts/*.sh && bash -n lib/*.sh`.
- Jeśli zmiana dotyczy konkretnej grupy aktualizacji, użyj `./update-all.sh --dry-run` lub `--only <group>`.
- Jeśli zmiana dotyczy `dev-sync`, uruchom:
  - `bash -n dev-sync/*.sh dev-sync/provider_setup.sh dev-sync-*.sh`,
  - `pwsh -NoProfile -Command '[scriptblock]::Create((Get-Content -Raw dev-sync/dev-sync-export.ps1)) | Out-Null'` albo pełny parse check dla zmienionych `.ps1`,
  - `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_dev_sync_safety.py -v`,
  - `bash dev-sync-export.sh --dry-run --verbose`.

## Raportowanie
- Podawaj tylko istotny wynik (pass/fail + 1-2 linie diagnozy).
- Długie logi streszczaj; nie wklejaj pełnych outputów bez potrzeby.
