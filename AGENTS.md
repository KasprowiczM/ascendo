# AGENTS.md

## GitHub Actions budget guardrail

This repository is owned by the `KasprowiczM` GitHub Free account. Private repositories share a hard allowance of 2,000 GitHub Actions minutes per month.

- Do not add, enable, or broaden scheduled workflows without the user's explicit approval.
- Default to `ubuntu-latest`; use macOS, Windows, or larger runners only when technically required and explicitly approved.
- Expensive platform-specific jobs should be manual or release-only, not triggered by every branch push.
- Restrict push/PR triggers by branch and path, add `concurrency` with cancellation, and set `timeout-minutes` on every job.
- Before changing Actions, estimate the monthly run count and minute impact. Do not change billing or spending limits without explicit approval.
- Public-repository standard runners may be free, but the same anti-waste rules still apply.


Cel projektu: utrzymanie jednego, przewidywalnego workflow aktualizacji Ubuntu 24.04 dla tej maszyny, z bezpiecznym audytem zmian.

## Stack
- Bash (`update-all.sh`, `scripts/update-*.sh`)
- Konfiguracja pakietów przez listy w `config/*.list`
- CI GitHub Actions dla walidacji składni i bezpieczeństwa repo

## Minimalne komendy robocze
```bash
./update-all.sh
./update-all.sh --dry-run
bash -n update-all.sh && bash -n scripts/*.sh && bash -n lib/*.sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_dev_sync_safety.py -v
bash scripts/preflight.sh
bash scripts/verify-state.sh
```

## Dev Sync
- GitHub jest źródłem prawdy dla plików śledzonych.
- `dev-sync/` obsługuje tylko prywatny overlay ignorowany przez Git, np. `.env.local`, `.dev_sync_config.json`, lokalne klucze i lokalne ustawienia agentów.
- Nie synchronizuj do Proton Drive plików odtwarzalnych: `APPS.md`, `logs/`, `config/*.bak_*`, `.codex.local/tmp/`, dependency/build/cache outputs.
- Domyślne sprawdzenie przed użyciem: `bash dev-sync-export.sh --dry-run --verbose`, potem `bash dev-sync-verify-full.sh`.
- `dev-sync` nie jest częścią domyślnego `update-all.sh`.
- Fresh clone: `bash scripts/preflight.sh`, `bash dev-sync/provider_setup.sh`, `bash scripts/restore-from-proton.sh --dry-run --verbose`, `bash scripts/bootstrap.sh --skip-sync`, `bash scripts/verify-state.sh`.
- Zakres odzyskiwania opisuje `config/restore-manifest.json`.

## Profile Agentów (aktualne)
- `default` — `gpt-5.3-codex`, reasoning `medium` (codzienna implementacja)
- `orchestrator` — `gpt-5.3-codex`, reasoning `high` (planowanie/delegowanie)
- `advisor` — `gpt-5.4`, reasoning `high`, `read-only` (analiza i review bez edycji)
- `worker-fast` — `gpt-5.4-mini`, reasoning `medium` (szybkie, ograniczone zmiany)
- `worker-tests` — `gpt-5.4-mini`, reasoning `medium` (testy i docs)

Źródło konfiguracji: `.codex.local/config.toml` i `.codex.local/agents/*.toml`.

## PLANNING RULE (NIEZMIENIALNA)
Nie wprowadzaj żadnych zmian w kodzie, dopóki nie poznasz kodu i wymagań na tyle, aby mieć co najmniej 95% pewności, co trzeba zbudować. Zawsze zadawaj pytania doprecyzowujące i kilkukrotnie weryfikuj swoje założenia, zanim przejdziesz z trybu planowania do implementacji. Ta zasada dotyczy wszystkich profili.

## Referencje
- @CLAUDE.md — główny kontekst projektu i komendy
- @docs/agents/architecture.md — architektura skryptów, działanie menedżerów pakietów
- @docs/agents/workflow.md — workflow agentów i zasady profili
- @docs/agents/style_guide.md — zasady stylu kodu Basha
- @docs/agents/testing_rules.md — zasady walidacji i testowania zmian
- @docs/agents/security.md — polityki bezpieczeństwa, sekrety i autoryzacja
- @docs/agents/handoff.md — kompresja kontekstu i przekazanie pracy między sesjami
- @docs/last-run-review.md — ostatni przeanalizowany pełny run i znane ostrzeżenia operacyjne
- @docs/multi-project-coexistence.md — dokumentacja współistnienia projektów ascendo i ascendo-ubuntu
