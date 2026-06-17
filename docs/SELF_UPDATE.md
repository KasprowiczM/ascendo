# Ascendo self-update (updating the app itself)

Ascendo checks for a newer **Ascendo** on every dashboard launch and lets
the user upgrade in place. This is distinct from `ascendo run` (which
updates the *third-party apps* Ascendo manages).

One engine — `core/ascendo/selfupdate/` — is shared by the CLI, the web
dashboard, and the Tauri desktop shell, so behaviour is identical on
**macOS, Windows, and Ubuntu/Linux**.

## How it works

1. On load, the SPA calls `GET /api/updates/check`.
2. The backend reads the installed version (`core/ascendo/__version__.py`),
   fetches the published **manifest**, and compares them with a
   PEP 440-aware comparator (so `1.0.0` > `1.0.0b1`).
3. If a newer version exists, a banner appears with **Install update**.
   The same check + install controls live in **Settings → About**.
4. Clicking install `POST`s `/api/updates/apply`, which runs the upgrade
   in a background job; the UI polls `/api/updates/status/{id}` for the
   live log, then offers **Reload**.

Because the desktop shell loads the same SPA, the macOS `.dmg`, the
Windows app, the Linux build, and the plain web dashboard all get the
identical experience.

## What "apply" actually does

- **git install (the common case, all OSes):** runs the repo's own
  `update.sh` (POSIX) / `update.ps1` (Windows) — `git pull --ff-only` +
  editable pip reinstall + `ascendo doctor`. Fully in-app.
- **packaged install (no git checkout):** can't self-pull. The API returns
  HTTP 409 with the platform's download artifact, and the UI shows
  **Download** instead of Install.
- **native shell drift:** the Tauri shell passes `ASCENDO_SHELL_VERSION`
  down. If the manifest's `shell.version` is newer than the running shell,
  the check reports `shell_update_available` and points the user to the new
  installer (a running `.app`/`.exe`/AppImage can't hot-swap its own
  binary; signed Tauri auto-update is the phase-2 upgrade once code-signing
  is in place).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/updates/check` | Version status report (never 500s; fails soft when offline). |
| POST | `/api/updates/apply` | Start an in-app upgrade → `202 {job_id}`, or `409` if unsupported. |
| GET | `/api/updates/status/{job_id}` | Poll live log + final state. |

## CLI

```bash
ascendo self-update           # check, then upgrade if newer (prompts)
ascendo self-update --check   # report only (exit 0 = up to date, 1 = newer available)
ascendo self-update --yes     # upgrade without confirmation
```

## The manifest (source of truth for "latest")

Published at `releases/latest.json` on `main`
(`https://raw.githubusercontent.com/KasprowiczM/ascendo/main/releases/latest.json`).
Override with `ASCENDO_UPDATE_MANIFEST_URL`; pick a channel with
`ASCENDO_CHANNEL=stable|beta`.

```json
{
  "schema": "ascendo/update-manifest/v1",
  "channel": "beta",
  "core":  { "version": "1.0.0b1", "notes_url": "…" },
  "shell": { "version": "0.0.7", "artifacts": {
      "macos_arm64": { "dmg_url": "…", "sha256": "…" },
      "windows_x64": { "msi_url": "…" },
      "linux_x64":   { "appimage_url": "…" }
  }},
  "channels": { "stable": { … }, "beta": { … } }
}
```

## Release checklist (publishing a new version)

1. Bump `core/ascendo/__version__.py` (and the Tauri `Cargo.toml` /
   `tauri.conf.json` if the shell changed).
2. Update `releases/latest.json`: set `core.version` (and `shell.version`
   + artifact URLs/sha256 if you shipped new installers).
3. Commit + push to `main`. Existing installs see the new version on their
   next launch and can upgrade with one click.

## Environment variables

| Var | Set by | Effect |
|---|---|---|
| `ASCENDO_DESKTOP=1` | Tauri shell (`main.rs`) | Marks a desktop launch. |
| `ASCENDO_SHELL_VERSION` | Tauri shell (`main.rs`) | Running shell version for drift detection. |
| `ASCENDO_UPDATE_MANIFEST_URL` | user/op | Override the manifest URL. |
| `ASCENDO_CHANNEL` | user/op | `stable` (default) or `beta`. |
| `ASCENDO_HOME` | install scripts | Install dir the updater operates on. |

## Safety notes

- The check is HTTPS-only and fails soft (offline → quiet, no errors).
- Upgrades require an explicit click; nothing auto-installs.
- For git installs the pre-update commit is recoverable via `git reset`;
  `update.sh`/`update.ps1` run `ascendo doctor` as a post-update self-test.
