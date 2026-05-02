# Ascendo — slogans, taglines, and marketing copy

> Single source of truth for every user-facing piece of marketing text.
> The installer banner, About modal, wizard welcome screen, Tauri config,
> README hero, GitHub Releases, winget manifest, and Microsoft Store
> listing all pull from this file. Update here, then propagate.

---

## Tagline (one-line, ≤ 6 words)

**Primary:**

> **Unified updates. Every app. One click.**

**Variants by surface:**

| Surface | Tagline | Char limit |
|---|---|---|
| Installer banner | Unified updates. Every app. | 32 |
| About modal subtitle | One tool to keep your machine up to date | 60 |
| Tauri `shortDescription` | Ascendo — Unified Updates | 40 |
| Tauri `longDescription` | One tool that keeps Windows, your apps, and your dev stack up to date — through a unified dashboard, CLI, and scheduler. | 200 |
| README hero | Cross-platform update orchestrator. One CLI, one dashboard, one Tauri shell — keeping Linux, Windows, and macOS continuously current. | — |
| winget short description | One-click unified updates for winget, Microsoft Store, Add/Remove Programs, and Windows Update. | 100 |
| Wizard welcome H1 | Welcome to Ascendo | — |
| Wizard welcome subtitle | Let's get every app on this machine ready to update — together, on your schedule, with no surprises. | — |

## Pitch (one paragraph)

> Ascendo is a unified updater that wraps every package source on your
> machine — winget, Microsoft Store, Add/Remove Programs, Windows Update,
> drivers, dev tools, AI CLIs — behind one command, one dashboard, and one
> schedule. Run a read-only check in 15 seconds. Plan before applying.
> Apply with a confirmation gate and an automatic Volume Shadow Copy
> snapshot. Verify and clean up automatically. See every change in
> structured run history with sidecar JSON receipts. The same shell runs
> on Linux and macOS, so what you learn on Windows transfers.

## Pitch (three bullets — for installer page or About modal)

- **One dashboard, every package source.** winget, Microsoft Store,
  Add/Remove Programs, Windows Update, drivers — all in one place.
- **Safe by default.** Read-only check, separate plan + apply phases,
  optional VSS snapshot, dry-run preview, and a typed-`apply` confirm
  gate before any system mutation.
- **Audit receipts, not screenshots.** Every run writes a structured
  JSON sidecar to `logs/runs/<id>/`. History, diffs, and
  reproducible reports are first-class.

## Feature highlight slogans (≤ 50 chars each)

For installer feature list, About modal feature grid, GitHub release
notes section headers, and the website hero rotator:

| Slogan | Feature |
|---|---|
| **See it before you ship it.** | Plan + dry-run before apply |
| **One password, every helper.** | UAC elevation, in-memory token |
| **Roll back without regrets.** | Volume Shadow Copy snapshots |
| **Schedule it. Forget it.** | Windows Task Scheduler integration |
| **Every run is a receipt.** | JSON sidecars, schema v1 |
| **Web, CLI, and native — all yours.** | Same backend, three frontends |
| **Cross-platform on day one.** | One repo, three adapters |
| **Transparent, scriptable, MIT.** | Open source from the start |

## Long-form marketing (for landing page / Releases page)

> ### Stop juggling update tools.
>
> If you've ever opened the Microsoft Store, then `winget upgrade`, then
> the Windows Update settings panel, then `pip list --outdated`, then a
> driver vendor's bespoke updater — and still missed something — you've
> met the problem Ascendo solves.
>
> **Ascendo is one tool that knows about every package source on your
> machine.** It scans winget, Microsoft Store, Add/Remove Programs, and
> Windows Update in a single 20-second sweep, classifies what it finds,
> and shows you outdated apps with their installed and candidate versions
> side-by-side. You pick what to update; Ascendo runs the right tool
> behind the scenes, takes a Volume Shadow Copy snapshot first if you
> want, and writes a JSON receipt for every change.
>
> ### Built for people who care about their machine.
>
> Every apply phase is gated behind a confirmation modal that asks you
> to type the word `apply`. Every run is dry-runnable first. Every change
> goes into a structured history you can diff, replay, or roll back.
> Every helper script is plain, auditable PowerShell or Python — no
> binary blobs, no telemetry, no cloud round-trips. Your password lives
> in this dashboard process's memory and disappears when you close it.
>
> ### Same shell, three operating systems.
>
> The Linux adapter (apt, snap, brew, npm, pip, flatpak, NVIDIA, fwupd)
> and macOS adapter (brew, MAS, softwareupdate, time-machine) share
> the exact same core, dashboard, and CLI as the Windows version.
> What you learn on one OS is muscle memory on the next.

## Tone of voice

- **Plain language over jargon.** "Apply phases run as Administrator"
  beats "elevation tokens are scoped to the helper process."
- **Receipts over claims.** Every paragraph that says "Ascendo does X"
  should be backed by a path or command the reader can run themselves.
- **Confidence without hype.** No "blazing-fast", no "next-gen", no
  "powered by AI". Use exact numbers when possible (`~20s scan`,
  `300s VSS timeout`, `JSON sidecar schema v1`).
- **Bilingual without compromise.** Polish copy is parallel quality, not
  machine-translated English.

## Copy gotchas

- **Never** say "sudo" on Windows surfaces. Always "Administrator" or
  "UAC elevation".
- **Never** say "Ubuntu_Aktualizacje" — the project is Ascendo. Old
  Polish copy is being purged.
- **Never** auto-translate `winget` / `MSI` / `MSIX` / `KB`. They're
  product names; Polish keeps them as-is.
- **Never** promise "auto-updates without confirmation" — the apply
  confirmation gate is a load-bearing trust feature; emphasise it.
