# Distributing Ascendo as a macOS DMG

> **Public-release stance (v0.6+):** DMG distribution is **NOT** part
> of the public-release surface. Ascendo on macOS ships via the
> `curl … install.sh \| bash` one-liner (CLI + Web profile). This doc
> is kept for **two audiences only**: (a) contributors who want to
> build a DMG locally for their own MacBook testing, and (b) future
> operators who plan to sign + notarize and re-introduce DMG releases.
> See [DESKTOP_INSTALLER_STATUS.md](DESKTOP_INSTALLER_STATUS.md) for
> the cross-platform rationale.

Honest, plain-English guide for shipping `Ascendo-Basic-*.dmg` and
`Ascendo-Dev-*.dmg` privately or after signing. Read this before
clicking Publish — there are real gotchas around macOS Gatekeeper that
will block your friends from running the app on first try unless you
plan for them.

---

## 1 · Build the DMGs on your Mac

```bash
# Basic edition for everyday users
bash bin/build-dmg.sh --edition=basic --profile=full
# Produces:  dist/Ascendo-Basic-<version>-<arch>.dmg
#            (where <arch> matches your build host: arm64 on Apple Silicon,
#             x86_64 on Intel)

# Dev edition for maintainers
bash bin/build-dmg.sh --edition=dev --profile=full
# Produces:  dist/Ascendo-Dev-<version>-<arch>.dmg
```

Both DMGs bake a `.ascendo-edition` marker file inside `Resources/`. On
first launch, `first-run-bootstrap-macos.sh` reads it (priority: env var
> baked marker > default basic) so the user gets the right edition
automatically.

Architecture: the build is **single-arch** based on your host. If you
build on an M-series Mac you produce `arm64`; on an Intel Mac, `x86_64`.
Most Macs since late 2020 are Apple Silicon, so an arm64 DMG covers
~90% of recipients. Universal binaries are on the v0.6.1 roadmap.

---

## 2 · The Gatekeeper reality

> Operator's question:
> *"will it run like the rest of the apps downloaded from internet on
> macos, first time you run it it gives you warning that this app was
> downloaded from net, do you want to run it? if yes it won't show
> again."*

**Short answer: NO, not without code signing + notarization.**

Long answer: macOS Gatekeeper distinguishes three states for downloaded
apps. The DMG you build today is **unsigned**, and the user-visible
behavior depends on the recipient's macOS version:

| macOS version | First-launch dialog (unsigned) | Workaround |
|---------------|--------------------------------|------------|
| 13 Ventura | "App is from an unidentified developer. [Cancel] [Open]" | Right-click → Open works |
| 14 Sonoma | Same dialog, no Open button | Right-click → Open, OR System Settings → Privacy & Security → "Open Anyway" |
| 15 Sequoia + 16 Tahoe | Hard-block: "App can't be opened because Apple cannot check it" — no Open button at all | System Settings → Privacy & Security → scroll to bottom → "Open Anyway" → enter password |

To get the **single "Downloaded from Internet, open? [Yes]"** dialog
that the operator's mental model expects, you need TWO things:

1. **Code signing** with an Apple Developer ID Application certificate
   ($99/year for the Apple Developer Program).
2. **Notarization** via `xcrun notarytool submit` — Apple's automated
   malware scan that issues a ticket attached to the binary.

`bin/build-dmg.sh` already has the full pipeline wired (lines 260–392):

```bash
export APPLE_CERT_NAME="Developer ID Application: Your Name (TEAM_ID)"
export APPLE_NOTARY_USER="you@example.com"
export APPLE_NOTARY_PASSWORD="app-specific-password"  # from appleid.apple.com
export APPLE_NOTARY_TEAM="ABCDE12345"

bash bin/build-dmg.sh --edition=basic --profile=full
# → signs, submits to notarytool, waits, staples ticket, hdiutil verify
```

After notarization + stapling, the recipient sees: **"Ascendo was
downloaded from the Internet. Are you sure you want to open it?
[Open] [Cancel]"** — and "Open" works on first try, on every macOS
version. That's the experience you want.

**Recommendation if you're publishing to friends/colleagues without an
Apple Developer ID yet**: include a copy-paste line in your GitHub
Release notes so they can override Gatekeeper without right-clicking:

```bash
# Recipient runs this once after dragging Ascendo.app to /Applications:
xattr -dr com.apple.quarantine /Applications/Ascendo.app
# Then double-clicking works normally.
```

---

## 3 · Dependencies on the recipient's Mac

The DMG itself is small (~10–20 MB — it ships the Tauri Rust binary +
the SPA HTML/JS/CSS + the bootstrap scripts, but NOT a bundled Python).
On first launch the app needs to install Ascendo's Python core; for
that it needs:

| Dep | Required | What if missing? |
|-----|----------|------------------|
| Homebrew (`brew`) | **Yes** today | `first-run-bootstrap-macos.sh` errors out with the install instructions. Recipients without brew must install it from <https://brew.sh> first. |
| Python ≥ 3.11 | Yes | Bootstrap runs `brew install python@3.14` (auto). Adds ~5 min on slow networks. |
| `git` | Yes | Bootstrap runs `brew install git` if missing. |
| `curl`, `jq` | Yes | Same — brew-installed automatically. |
| Internet | Yes (first launch only) | Bootstrap clones the repo + installs the venv. ~150–300 MB of disk after install. |

**Disk impact on the recipient's Mac after first launch**:
- `~/.local/share/ascendo/` — repo clone (~50 MB)
- `~/.local/share/ascendo/venv/` — Python virtualenv (~150–300 MB)
- `~/.ascendo/` — per-user data (runs, inventory.db, sudo cache)
- `~/.config/ascendo/` — user overrides (web_apps.toml, AI creds)

**Known limitation (v0.6.0-rc)**: the Tauri shell does NOT currently
invoke `first-run-bootstrap-macos.sh` automatically — operators must
run it from Terminal on first install. v0.6.1 will wire bootstrap into
the Tauri `setup()` hook so double-clicking Ascendo.app on a fresh Mac
runs the bootstrap automatically. Track in PLAN.md "Tauri bootstrap
auto-invoke".

Workaround until v0.6.1 — include in your Release notes:

```bash
# Drag Ascendo.app to /Applications, then in Terminal:
/Applications/Ascendo.app/Contents/Resources/first-run-bootstrap-macos.sh
# Bootstrap installs deps + clones repo + runs validate. ~5–10 min.
```

---

## 4 · How to publish via GitHub Releases

```bash
# 1. Bump the version
edit core/ascendo/__version__.py    # e.g. 0.6.0 → 0.7.0
git add core/ascendo/__version__.py && git commit -m "chore: bump v0.7.0"
git tag -a v0.7.0 -m "Release v0.7.0"

# 2. Build both editions
bash bin/build-dmg.sh --edition=basic --profile=full
bash bin/build-dmg.sh --edition=dev --profile=full

# 3. (Optional but recommended) sign + notarize — see section 2.

# 4. Push + create release with both DMGs attached
git push --tags
gh release create v0.7.0 dist/Ascendo-Basic-*.dmg dist/Ascendo-Dev-*.dmg \
  --title "Ascendo v0.7.0" \
  --notes-file RELEASE_NOTES.md
```

The release page will show two downloadable files; recipients pick the
edition that matches their needs.

---

## 5 · Updates after first install

Two paths, depending on how the user installed:

| Install method | Update path |
|----------------|-------------|
| DMG drag-to-Applications | Re-download latest DMG from GitHub Releases; drag-replace; first launch picks up new venv via bootstrap idempotency check |
| `curl … install.sh \| bash` | `curl … update.sh \| bash` (or `ascendo_update` from `~/.local/bin/`) — `git pull --ff-only` + refresh editable installs |

In-app auto-update (Sparkle-style) is on the v0.7+ roadmap.

---

## 6 · Recipient's first-launch checklist

Tell your users to expect:

1. Drag `Ascendo.app` from the DMG to `/Applications`.
2. On macOS 15+, run `xattr -dr com.apple.quarantine /Applications/Ascendo.app` in Terminal once (only needed for unsigned builds).
3. Open Terminal and run `/Applications/Ascendo.app/Contents/Resources/first-run-bootstrap-macos.sh` (this dependency will be auto-invoked by the .app in v0.6.1).
4. After bootstrap finishes (~5–10 min on first run), double-click `Ascendo.app` — the dashboard opens at `http://127.0.0.1:8765/`.
5. The onboarding wizard appears once. Pick language + theme + profile. After clicking Finish, the wizard never re-appears; everything is changeable later under Settings.

---

## 7 · TL;DR matrix

| Scenario | Today (unsigned) | After Apple Developer ID + notarization |
|----------|------------------|------------------------------------------|
| First-launch dialog | Hard-block on Sequoia/Tahoe; need Privacy & Security override or `xattr` | Single "Downloaded from Internet, Open?" dialog |
| User experience | "Why won't this open? Did you screw something up?" | "Click Open, done" |
| Investment | Free | $99/year Apple Developer Program |
| Build script support | Full | Full (already wired — just set env vars) |
| When to upgrade | The day before you start sharing widely | — |
