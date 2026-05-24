# Ascendo — user journey

A single map of every realistic path through the product, from the first
moment the user has the repo cloned to the day they roll back a bad
update. Each journey lists the **commands**, the **expected output**,
and the **escape hatches** if something looks off.

> Polish version: [`../pl/user-journey.md`](../pl/user-journey.md)

## 1. Fresh clone → first run (new machine)

**Goal:** turn a freshly-cloned repo on a new Ubuntu box into a working
update suite without surprises.

```bash
git clone https://github.com/KasprowiczM/Ascendo
cd Ascendo
bash scripts/fresh-machine.sh
```

What happens, in order:

1. **Language pick** (interactive) — English or Polski. Stored in
   `~/.config/ascendo/lang`. Used for every CLI message and the dashboard.
2. **Step 1/5 preflight** — `scripts/preflight.sh` checks Bash, Python,
   git, sudo, snap, brew (and warnings if anything's off).
3. **Step 2/5 bootstrap** — restores private overlay from Proton/rclone
   if `.dev_sync_config.json` is present. Runs `setup.sh --check` so
   nothing is auto-installed yet.
4. **Apps detect (read-only)** — prints a colour-coded table:
   `tracked` (in config + installed), `detected` (installed but not in
   config, candidate to add), `missing` (in config but not installed,
   candidate to install). **Nothing is changed.**
5. **Step 3/5 dashboard venv** — creates `app/.venv/`, installs FastAPI.
6. **Step 4/5 user-service** — installs `systemd --user` unit so the
   dashboard auto-starts on login.
7. **Step 5/5 verify** — final state check.

> **Important:** fresh-machine **never installs packages from
> `config/*.list` automatically.** It tells you what's missing; you
> decide whether to install with `bash scripts/apps/install-missing.sh`.

After this, the dashboard URL is **<http://127.0.0.1:8765>** and shows the
First-run wizard if `~/.config/ascendo/onboarded.json`
doesn't exist yet.

**Escape hatches:**
- `--check-only` — read-only audit, never mutates anything.
- `--no-dashboard` — skip Python venv and service install.
- `--no-sync` — skip the Proton overlay restore.
- `--lang en|pl` — non-interactive language pick.

---

## 2. Daily update

```bash
./update-all.sh                 # full profile
./update-all.sh --profile safe  # everything except drivers/firmware
./update-all.sh --profile quick # read-only check (~15 s)
./update-all.sh --dry-run       # plan, no mutations
```

**Sudo:** asked once, cached for the whole run via an in-memory askpass
helper. No re-prompts even on long apt phases.

**Live progress:** every phase script's output is teed to the terminal
plus `logs/runs/<id>/<cat>/<phase>.log`. Per-package lines like
`[3/47] firefox 131 → 132` appear in real time during apt:apply.

**End of run:** colour-coded summary table, JSON sidecar per phase
(schema v1), and — if `/var/run/reboot-required` is set — a rich
`RESTART REQUIRED` box with a one-line reboot command.

---

## 3. Adding a new application

The user installs a new tool by hand and wants Ascendo to manage it from
now on. Two paths:

### CLI

```bash
# 1. See what's detected but untracked
bash scripts/apps/detect.sh

# 2. Pick a category (apt|snap|brew|brew-cask|npm|pipx|flatpak)
bash scripts/apps/add.sh slack --category snap

# 3. Verify
bash scripts/apps/list.sh
```

### Dashboard

`Apps` tab → row turns yellow (`detected`) → click **+ Add to config**.
The package now appears as `tracked` and is part of every future
`update-all.sh` run.

---

## 4. Detecting "untracked" installed apps

Use case: someone installed apps with `apt`/`snap`/`brew` directly. We
want to surface them and let the operator triage.

```bash
bash scripts/apps/detect.sh           # human, colour
bash scripts/apps/detect.sh --json    # machine-readable, pipeable to jq
```

State legend:

| State     | Meaning |
|-----------|---------|
| tracked   | listed in `config/*.list` AND installed |
| detected  | installed BUT NOT in any list — candidate for `apps add` |
| missing   | in `config/*.list` BUT NOT installed — candidate for install |

Untracked packages never break updates — they just stay invisible to the
suite until you opt them in.

---

## 5. Migrating to a new machine

```bash
# On the OLD machine
bash dev-sync-export.sh        # pushes private overlay to Proton/rclone

# On the NEW machine
git clone https://github.com/KasprowiczM/Ascendo
cd Ascendo
bash scripts/fresh-machine.sh
```

The overlay (8 files: `.env.local`, ssh keys, `.claude/*` agent settings,
`.dev_sync_config.json`) is restored automatically when Proton/rclone is
reachable. The fresh-machine flow then walks the rest exactly as in
journey 1.

After fresh-machine completes, run `bash scripts/apps/install-missing.sh`
to populate apps that were tracked in config but not yet installed.

---

## 6. Recovery (snapshot rollback)

Updates went sideways? You want to roll back.

```bash
# Pre-apply snapshot was created automatically (Settings → snapshot toggle)
# or manually: bash scripts/snapshot/create.sh "before risky upgrade"

bash scripts/snapshot/list.sh                 # what's available
bash scripts/snapshot/restore.sh <snap-id>    # do it (asks for sudo)
sudo systemctl reboot                         # come back on the rolled-back state
```

Dashboard equivalent: **Snapshots** panel → row → **Restore** → confirm.
Audit log records the action under `~/.local/state/ascendo/audit.log`.

---

## At-a-glance command map

| Need | Command |
|------|---------|
| New machine             | `bash scripts/fresh-machine.sh` |
| Update everything       | `./update-all.sh` |
| Quick health check      | `./update-all.sh --profile quick` |
| What apps are tracked   | `bash scripts/apps/list.sh` |
| What's detected/missing | `bash scripts/apps/detect.sh` |
| Add a tracked app       | `bash scripts/apps/add.sh <pkg> --category <cat>` |
| Install missing apps    | `bash scripts/apps/install-missing.sh` |
| Snapshot pre-apply      | `./update-all.sh --snapshot` |
| Roll back               | `bash scripts/snapshot/restore.sh <id>` |
| Open dashboard          | `xdg-open http://127.0.0.1:8765` |
| Build .deb              | `bash packaging/build-deb.sh` |
