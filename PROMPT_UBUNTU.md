# Ascendo — Final Push Session: Ubuntu (mk-uP5520)

> Paste this whole file as your first message in a Claude Code session running
> on the **Ubuntu** box (`~/Dev_Env/Ascendo`). Run the **macOS session first** —
> it lands the shared-core changes you pull here.

You are a senior engineer finishing the Linux/Ubuntu side of Ascendo for a
**v1.0-beta production push**. Read `ASCENDO_ULTRA_REVIEW_2.md` §4(Linux)/§7.
The honest-status fix, the deduplicator fail-safe (core), and Ubuntu's
`verify_signature` wiring are already on `main`.

## Scope decision (important)
**v1 Linux support = Ubuntu/Debian ONLY.** Fedora/Arch/RHEL/dnf/pacman are
**deferred to the next version** by operator decision. Do **NOT** add an
unsupported-distro guard or dnf/pacman adapters this round. Just make sure the
docs/README say "Linux: Ubuntu/Debian (others coming in a later release)" so we
don't overpromise.

## Ground rules
- Work directly on `main`. **No new worktrees.** Start with `git pull origin main`.
  Commit + `git push origin main` after each task. Don't lose work.
- **TEST-FIRST**; verify before claiming done.
- After each task: `python3 -m pytest adapters/ubuntu/tests/ -q` (keep green) and
  finally `bash bin/validate-ubuntu.sh --skip-dashboard --skip-scheduler --skip-web`
  (or the full harness). Confirm the new CI step (`adapters/ubuntu/tests` on
  `ubuntu-24.04`) would be green.

## MUST-DO (P1 — pre-push blockers)

### 1. Confirm the Ubuntu adapter test suite is green natively + gates CI
The new CI matrix step runs `python3 -m pytest adapters/ubuntu/tests/ -q` on the
ubuntu runner. Run it on this box; fix any genuine failures (a pre-existing
`systemctl warn` test fails *only when the Ubuntu adapter runs on macOS* — on a
real Ubuntu box it should pass). If anything is red, fix the code (don't just
xfail) and push.

### 2. Deduplicator behavior on Ubuntu
Ubuntu ships a real `adapters/ubuntu/config/ubuntu_app_sources.toml` (claude /
docker / vscode / spotify) but has **no uninstall executor** (only Windows
`apply.ps1` executes `DEDUPLICATION_TASKS.json`). After `git pull` you have the
core report-only fail-safe. Verify on this box:
- A read-only "Quick check" (`bash bin/validate-ubuntu.sh` or a check run) does
  **not** silently uninstall anything and — once the macOS session lands the
  "don't mutate read-only CHECK sidecars in report-only mode" change — does not
  mutate the check sidecars; it only writes `DEDUPLICATION_REPORT.md`.
- If you want Ubuntu to actually act on approved duplicates, mirror the Windows
  pattern: an apt/snap/flatpak uninstall step gated behind
  `ASCENDO_DEDUP_AUTO_UNINSTALL=1` **or** the dashboard `POST /dedup/apply`
  approval marker — never an implicit default. Otherwise leave it report-only and
  say so. Add a test for whichever you choose.

## SHOULD-DO (P2)

### 3. verify_signature — confirm wired + tested
`adapters/ubuntu/ascendo_ubuntu/managers/source.py:91` implements
`verify_signature` (sha256 vs the GPG-signed apt manifest, fail-closed) and
`managers/apt.py:105` calls it. Confirm it's actually invoked on the apply path
with a real expected hash (from `apt-get --print-uris`), not `None`. Add/confirm
a contract test `test_verify_signature_apt_gpg` (valid hash ⇒ True; missing hash
⇒ `SourceVerificationError`; mismatch ⇒ False). This closes P1 for Linux.

### 4. W10 / W2 / W11 parity in Ubuntu scripts
If the Ubuntu web/npm/pip scripts under `scripts/` (or `adapters/ubuntu/scripts/`)
have discovery / release-feed-regex / `sort -V` paths, apply the same fixes the
macOS session adds: W10 explicit discovery-failure signal, W2 probe_broken on
regex no-match, W11 replace `sort -V` with the Python comparator. If a path
doesn't exist on Ubuntu, note it explicitly rather than inventing one.

## Finish
- Update `CHANGELOG.md` + a `PLAN.md` note + append an Ubuntu section to
  `HANDOFF.md` Sesja 84. Make sure README/docs say "Linux v1 = Ubuntu/Debian".
- Final: `python3 -m pytest adapters/ubuntu/tests/ -q` green +
  `bash bin/validate-ubuntu.sh` green.
- `git push origin main`. Report which items landed + any Ubuntu-only blockers.
