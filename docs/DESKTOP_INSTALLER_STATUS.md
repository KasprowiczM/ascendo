# Desktop installer status (DMG / MSI / .deb)

Plain-English summary of "can I ship Ascendo as a clickable .dmg / .msi
/ .deb to users I don't know, today, without paying anything?"
Short answer per platform up top, gory details below.

| Platform | Works without signing? | Recipient pain | Recommendation today |
|----------|------------------------|----------------|----------------------|
| **macOS** (.dmg) | ❌ Hard-blocked on Sequoia 15 / Tahoe 16 | High — System Settings → Privacy override required | Ship **CLI + Web** only; build DMGs locally for dev/test only |
| **Windows** (.exe / .msi) | ⚠️ SmartScreen warns but is clickable through | Medium — "More info → Run anyway" link visible | Ship **CLI + Web** primary; native installer is fine for power users who know to click through |
| **Ubuntu / Debian** (.deb) | ✅ Works fine, no signing required | None | **.deb is safe to publish** — dpkg signature is for apt repos, not standalone .debs |

The recommended public-release path on all three OSes is the
**`curl … install.sh \| bash`** / **`iwr … install.ps1 \| iex`**
one-liner, which is unaffected by every signing concern below.

---

## macOS — .dmg + .app

`bin/build-dmg.sh` produces `Ascendo-{Basic,Dev}-<version>-<arch>.dmg`.
On the **build host** it works fine. On **another Mac that downloads
it from your GitHub Releases**, Gatekeeper escalates with each macOS
version:

| macOS | First-launch experience (unsigned) | What the recipient does |
|-------|------------------------------------|-------------------------|
| 13 Ventura | "App is from unidentified developer. [Cancel] [Open]" | Right-click → Open |
| 14 Sonoma | Same dialog, but "Open" button removed | Right-click → Open, OR System Settings → Privacy & Security → "Open Anyway" |
| 15 Sequoia + 16 Tahoe | **Hard-block** — no Open button anywhere | System Settings → Privacy & Security → scroll to bottom → "Open Anyway" → enter password |

To get the textbook **"Downloaded from Internet, are you sure? [Open]"**
experience the operator expects, you need:

1. **Apple Developer Program** membership ($99 / year).
2. **Developer ID Application** certificate from the Apple Developer
   portal.
3. **Notarization** via `xcrun notarytool submit`.
4. **Stapling** via `xcrun stapler staple`.

All four are already wired into `bin/build-dmg.sh` (lines 260–392); set
four env vars and re-build:

```bash
export APPLE_CERT_NAME="Developer ID Application: Your Name (TEAM_ID)"
export APPLE_NOTARY_USER="you@example.com"
export APPLE_NOTARY_PASSWORD="app-specific-password"  # from appleid.apple.com
export APPLE_NOTARY_TEAM="ABCDE12345"
bash bin/build-dmg.sh --edition=basic --profile=full
```

**Current public-release stance**: don't ship DMGs from a public repo.
The operator builds locally for their own MacBook testing only.
End-users get the curl-bash one-liner.

Workaround if you do hand a DMG to one specific person and don't want
to pay for an Apple Developer ID: include this in your message —

```bash
# Once after dragging Ascendo.app to /Applications:
xattr -dr com.apple.quarantine /Applications/Ascendo.app
```

Full detail in [DMG_DISTRIBUTION.md](DMG_DISTRIBUTION.md).

---

## Windows — .exe / .msi

`bin/build-installer.ps1` produces `Ascendo-<version>-x64-setup.exe`
(NSIS) + `Ascendo-<version>-x64.msi` (WiX). On the build host both
work. On another Windows machine the recipient's experience depends
on what kind of code signing certificate (if any) signed the binary:

| Signing | Recipient experience |
|---------|----------------------|
| **None** (today) | SmartScreen blue screen "Windows protected your PC". User clicks **More info → Run anyway**. Works on every supported Windows version. Mildly scary first time, fine for power users. |
| **Standard OV / IV Authenticode cert** ($100–300 / year via Sectigo, DigiCert) | Same SmartScreen blue screen at first, but reputation accrues over downloads. After ~3000 successful installs across users, the warning disappears for new downloaders. |
| **Extended Validation (EV) Authenticode cert** ($300–700 / year) | **No SmartScreen warning at all** from day one. Single "Do you want to allow this app to make changes?" UAC prompt — same as winget-installed apps. |
| **Microsoft Azure Trusted Signing** ($10 / month, public preview) | Equivalent to EV in user UX. No SmartScreen prompt. Cheapest path to a clean install experience. |

Unlike macOS, **the recipient is never hard-blocked** — the "Run anyway"
escape hatch exists at every signing tier including unsigned. So
publishing an unsigned .msi to GitHub Releases is *less* user-hostile
than publishing an unsigned .dmg. But it's still ugly.

**Current public-release stance**: same as macOS — use the install.ps1
one-liner for everyone. Power users who insist on a real installer can
clone the repo and run `pwsh ./bin/build-installer.ps1` themselves.
When you're ready to sign, Azure Trusted Signing is the cheapest path
to a clean UX.

`bin/build-installer.ps1` already accepts `-CertificatePath` +
`-CertificatePassword` + `-TimestampUrl` parameters; signing is one
flag-set away once you have a cert.

---

## Ubuntu / Debian — .deb

`packaging/build-deb.sh` produces `ascendo_<version>_amd64.deb`. **This
is the easy one.** A .deb has two kinds of "signing":

1. **Repository signing** — the `Release` file in an apt repository
   gets signed with the maintainer's GPG key. Required for apt to
   trust an `apt-get update` against `deb http://...` source lines.
   **Not relevant for standalone .deb downloads.**
2. **Package signing** — debsigs / dpkg-sig. Optional, rarely used in
   practice, no enforcement by default.

A user who downloads `ascendo_0.7.0_amd64.deb` from GitHub Releases
and runs:

```bash
sudo apt install ./ascendo_0.7.0_amd64.deb
```

…gets a "Get:1 …" / "Setting up ascendo …" / "OK" sequence. **No
warning, no override, no signing prompt.** apt resolves the .deb's
declared dependencies against the user's package index and installs
cleanly. The recipient does NOT need anything special.

If you later want to ship an APT repository (so users can do
`apt-get install ascendo` without re-downloading the .deb each
release), THEN you need the maintainer's GPG key and a signed
`Release` file. The free service `cloudsmith.io` or self-hosted
options like `reprepro` handle this. Out of scope for today.

**Current public-release stance**: .deb is **safe to publish** to
GitHub Releases as-is. No signing investment needed. The
`packaging/build-deb.sh` script's output is ready for upload.

The only thing left is wiring the .deb's `postinst` script to do the
equivalent of `first-run-bootstrap-macos.sh` — install missing Python
3.11+, set up the venv, drop the `ascendo` shim. Per
[`packaging/build-deb.sh`](../packaging/build-deb.sh) that's already
done.

---

## Why "CLI + Web" is the public path on every OS

- One install mechanism per OS (curl|bash, iwr|iex, .deb) — no
  per-edition installer matrix to maintain.
- Zero per-recipient signing/notarization tax.
- The web UI is **identical to the desktop UI**. The Tauri shell is
  just a WKWebView/WebView2 pointing at `http://127.0.0.1:8765/`.
  Skipping the shell loses nothing functionally — users open the
  dashboard in their default browser, which they were probably going
  to do anyway.
- Updates flow through `ascendo_update` / `update.sh` / `update.ps1`
  cleanly. No "drag new .app to /Applications" dance.

The desktop installers stay in-repo for two reasons:
1. **Contributors** who want to test the Tauri shell locally still need
   them (`bash bin/build-dmg.sh` etc.).
2. **Future signing path** — once an Apple Developer ID + EV
   Authenticode cert are in place, ship the installers as part of
   GitHub Releases and call them "Beta" until reputation accrues.

Tracking: signing roadmap is in [PLAN.md](../PLAN.md) under "v0.7+
desktop distribution".
