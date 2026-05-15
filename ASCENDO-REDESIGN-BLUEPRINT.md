# Ascendo — Redesign Blueprint

> The target product. Direction, design system, information architecture, and a
> screen-by-screen blueprint. Built on the *existing* `colors_and_type.css`
> tokens and the existing 5-destination IA model — this is a **consolidation
> into one coherent surface**, not a new design language.

---

## 1. Design concept

**Calm · Decisive · Engineered · Quiet · Trustworthy.**

Ascendo is a *status instrument*, not a control panel. It exists to deliver one
feeling: **"your machine is current — and if it isn't, one move fixes it."**
Every screen answers a single question and offers a single primary move. The
aesthetic is a precise dark instrument cluster: near-black canvas, restrained
lime used only as *signal* (never as a button wall), monospace reserved for real
machine data, generous breathing room, flat surfaces, hairline separation, no
gradients, no noisy shadows, no decoration that isn't data.

Reference feel: Linear's calm, Vercel's restraint, a Stripe dashboard's
hierarchy, a high-end terminal's precision — applied to *one job done well*.

### Design principles (the contract every screen obeys)

1. **Answer first.** The top of every screen states the answer, then the detail.
2. **One primary action per screen.** Exactly one filled accent control. Everything else is quiet.
3. **Outcome over machinery.** "Update", "Preview", "Up to date" — never "apply phase", UUIDs, "adapter".
4. **Accent = signal, not chrome.** Lime means "this needs you / this is the move". It is rationed.
5. **One card. One shape.** A single card primitive with role variants. No bespoke card per screen.
6. **Type carries hierarchy, color confirms it.** Size/weight create the scan path; color only states status.
7. **Progressive depth.** Summary → drill-down (drawer) → raw (logs). Power is *available*, not *front-loaded*.
8. **Mono is for machines.** Versions, hashes, paths, log lines only. UI chrome is the sans face.
9. **Honest empty/idle states.** "All clear" is a designed, satisfying moment, not a leftover line of text.
10. **Quiet by default, loud on exception.** OK is silent; failure/outdated is the only thing that earns attention.

---

## 2. Visual design system

**Foundation: keep `colors_and_type.css` as-is. It is the bright spot.** This
section is the *usage discipline* layered on those tokens.

### 2.1 Color strategy

| Role | Token | Usage rule |
|---|---|---|
| Canvas | `--bg` (ink-900 dark) | Page. Recedes. Never a card. |
| Surface | `--bg-elev` | The one card surface. Hairline `--border`, no heavy shadow. |
| Nested | `--bg-nested` | Sub-panels inside a card (drawer body, code block frame). |
| Text | `--fg` / `--fg-muted` / `--fg-faint` | 3 levels only. Primary / secondary / metadata. |
| **Accent (lime)** | `--accent` | **Reserved.** Only: the one primary action, the active nav item, a focus ring, an "ascending/positive" sparkline. **Never a row of buttons, never a fill behind text.** |
| Status | `--ok / --warn / --err / --info` (+ `-bg` / `-text`) | Dots, pills, single-pixel bars. Status text uses `-text` (AA). |

Accent budget rule: **at most one filled `--accent` element visible per
screen.** If two things are lime, one is wrong.

### 2.2 Typography

Pairing (already in tokens): **Inter Tight** (UI) + **JetBrains Mono** (machine
data only). `Instrument Serif` retired from the app surface (keep for marketing
only — it does not belong in an instrument).

| Class | Token | Where |
|---|---|---|
| Page title | `--fs-2xl` / `--fw-semibold` | App header, once per screen |
| Section title | `--fs-lg` / `--fw-semibold` | Card titles (sentence case, **not** mono-caps) |
| Big stat | `--fs-3xl` / `--fw-bold` | KPI numbers |
| Body | `--fs-base` / `--fw-regular` | Default |
| Label | `--fs-sm` / `--fw-medium` / `--fg-muted` | Form labels, table headers (sentence case) |
| Caption/meta | `--fs-xs` / `--fg-faint` | Timestamps, counts, hints |
| Mono | `--font-mono` `--fs-sm` | Versions, paths, hashes, log lines — **only** |

**Killed:** the mono-uppercase-eyebrow-on-every-block pattern. Eyebrows become
sentence-case sans labels. Mono caps survive *only* on genuine terminal output.

### 2.3 Spacing

Enforce the existing 4px ramp (`--space-1`…`--space-10`). **Zero raw `px` in
component CSS.** Canonical rhythm:

| Context | Token |
|---|---|
| Inside a control / chip | `--space-2` (8) |
| Card inner padding | `--space-5` (24) |
| Between stacked elements in a card | `--space-3` (12) |
| Between cards | `--space-4` (16) |
| Between major sections | `--space-6` (32) |
| Page gutter | `--space-5` desktop / `--space-4` mobile |

### 2.4 Shape & elevation

- Radius: `--radius-md` (8) cards, `--radius-sm` (6) controls, `--radius-pill` only for status pills/dots.
- Elevation: **flat.** Card = `--bg-elev` + 1px `--border`. `--shadow-md` only on the detail drawer and modals. **No `--shadow-lg/xl` anywhere in-app.** Hierarchy comes from surface tone + border + space, not blur.

### 2.5 Iconography

One set, one weight (the existing `icons.js` Lucide-style line set, 1.5px
stroke, 18px). Icons only where they add scanning speed: nav, status, primary
actions. **No icon-per-row decoration.** No emoji.

### 2.6 Motion

Use existing `--dur-*` / `--ease-out`. Three motions only:
- Route change: 120ms content cross-fade (no slide).
- Drawer: 200ms slide-in from right + scrim fade.
- Live/progress: a single quiet pulse on the active run dot.
`prefers-reduced-motion`: all of the above → instant. No decorative motion ever.

### 2.7 State strategy (designed, not ad-hoc)

| State | Treatment |
|---|---|
| **Loading** | One skeleton primitive (shimmer rows matching the card it replaces). Never a spinner-in-void. Same component everywhere. |
| **Empty / all-clear** | A first-class composition: large status glyph (✓), one calm line ("Everything is up to date"), the last-checked time, one quiet "Check now". This is the product's signature moment — design it like a feature. |
| **Error / failed run** | A status banner at the top of the relevant screen (not buried in a list): what failed, in plain words, with "View report" + "Retry" + "Ask AI". |
| **Success** | Inline, momentary: the run row flips to ✓ with a 1s lime confirm, then settles to quiet. No modal, no confetti. |

---

## 3. Layout & navigation model

### 3.1 App shell (one owned shell — replaces the 3-layer patch)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ SIDEBAR (240px, fixed)        │  CONTENT CANVAS (fluid, max 1200px)        │
│                               │ ┌────────────────────────────────────────┐ │
│  ◤ Ascendo                    │ │ PAGE HEADER (sticky)                    │ │
│    unified updates            │ │  Title · short desc        [Primary CTA]│ │
│                               │ │  ── sub-tab rail (only if >1) ──        │ │
│  ▸ Dashboard      (active)    │ ├────────────────────────────────────────┤ │
│  ▸ Library                    │ │                                        │ │
│  ▸ Runs                       │ │  ANSWER ZONE  (status, above the fold) │ │
│  ▸ Insights                   │ │                                        │ │
│  ▸ Settings                   │ │  CONTENT ZONE (cards / table / form)   │ │
│                               │ │                                        │ │
│                               │ │                                        │ │
│  ─────────────────            │ │                              ┌─────────┐│ │
│  ● macOS · admin              │ │                              │ DETAIL  ││ │
│  Ascendo 0.6 · ⚙ Prefs        │ │                              │ DRAWER  ││ │
└───────────────────────────────┴──────────────── (overlay, right) ─────────┘
```

- **Sidebar (240px):** brand, 5 destinations (icon + label, active = lime
  left-rule + `--bg-elev` + lime icon), and a single footer cluster: one
  elevation/OS status line (one string, one source of truth), version, ⚙
  Preferences (theme/lang/density popover). The bottom status bar is **deleted**
  — its content moves here, stated once.
- **Page header (sticky):** screen title + one-line purpose + **the single
  primary CTA** for that screen + the sub-tab rail (only rendered when a
  destination has ≥2 tabs). This *replaces* the per-view `<h2>` (kills
  double-titling) and the scattered Refresh/Edit-layout/Re-check buttons.
- **Answer zone:** the top band of the canvas, always visible without scroll,
  states the screen's answer (status pill / KPI strip).
- **Content zone:** cards / table / form. One card primitive.
- **Detail drawer (right overlay, 480px):** drill-downs (a run, an app's
  history, a failure report) slide in over the canvas. Never a new page, never
  a centered modal except destructive confirms.

### 3.2 Navigation model

- 5 destinations (unchanged model), hash routes `#dashboard`, `#library/apps`,
  `#runs/history`, etc. **One router, owned by the shell**, no monkey-patching,
  no second hashchange path.
- Sub-tabs are real, in the page header, segmented control. Drill-downs =
  drawer, not route, so the user never loses their place.
- Mobile: sidebar collapses to a **fixed bottom tab bar (`position: fixed;
  bottom: 0`)** — 5 items, the current critical bug fixed by construction. Page
  header CTA becomes a sticky bottom action above the tab bar on action screens.

### 3.3 Responsive behavior

| Width | Layout |
|---|---|
| ≥1024 | Sidebar + canvas (max 1200) + drawer overlay |
| 768–1023 | Sidebar collapses to icon rail (64px); canvas full; drawer 60vw |
| <768 | No sidebar; fixed bottom tab bar; canvas full-bleed with `--space-4` gutter; drawer = full-screen sheet; KPI strip wraps 2×2; tables → stacked rows; **cards size to content (no fixed heights — fixes clipping)** |

---

## 4. Component taxonomy (the system — finite, named, reused)

Everything on every screen is one of these. No bespoke per-view markup.

### Primitives
| Component | Purpose | Replaces |
|---|---|---|
| `Card` | The only surface container. Props: `title`, `action?`, `density`, `tone(default/quiet)`. | every ad-hoc `.card`, the 6 different Dashboard cards |
| `StatPair` | Big number + label + optional delta/trend dot. | the donut, "100/100", scattered counts |
| `StatusPill` | dot + label, status-colored, mono only if it's machine data. | the 12× "● OK", inconsistent OK/OUTDATED styling |
| `Button` | `variant: primary(1/screen) \| secondary \| ghost \| danger`; one size scale. | the 5 lime stack, the 6-button row, mixed fills |
| `Field` | label + control + hint + error; control = `Segmented \| Toggle \| Select(rare) \| Text`. | raw `<select>`, the un-upgraded controls |
| `Skeleton` | loading placeholder, shaped per host. | per-view improvised loaders |
| `EmptyState` | glyph + line + one action. | "Everything is up to date." plain text |
| `Banner` | top-of-screen status/error/info, dismissible/action. | buried failure lists, contradictory status copy |

### Composites
| Component | Built from | Used on |
|---|---|---|
| `KpiStrip` | 3–4 `StatPair` in a row | Dashboard, Insights |
| `DataTable` | header + rows + **one row action menu** (not 6 buttons) | Library/Apps |
| `RunPanel` | live status + streamed log (mono) + progress | Runs |
| `Timeline` | run rows: time · profile · result pill · "report" | Runs/History, Insights |
| `Drawer` | header + body + footer actions | run detail, app history, report |
| `Sparkline` / `MiniBars` | one-color, semantic, axis-labeled | Insights only |
| `AppHeader` | title + desc + primary CTA + sub-tabs | every screen (the shell) |

### Killed components (do not port forward)
`layout-editor.*` (delete from product), the per-row 6-phase button group, the
donut ring, the always-on disclosure ("Click to expand"), mono-caps eyebrow,
the bottom status bar, every duplicate `.card`/`.app-header` rule, the
`ui-redesign.js` shadow renderer.

---

## 5. Information architecture — target

Model unchanged (it was correct). Delivery owned by one shell.

| Destination | Question it answers | Sub-tabs | Primary CTA |
|---|---|---|---|
| **Dashboard** | "Is my machine current, and what should I do?" | none | **Update now** (or **Check** when current) |
| **Library** | "What's managed here and what's its state?" | Apps · Sources · Tools | Update outdated |
| **Runs** | "Run it / what's scheduled / what ran?" | Start · Scheduled · History | Start run |
| **Insights** | "How is updating going over time?" | Trends · Logs(dev) | Open latest report |
| **Settings** | "Configure / integrate / get help." | General · Integrations · Sync · Support · About | (none) |

Notable IA corrections:
- **Apps before Sources** in Library — users think in apps, not package
  managers. "Sources" is the engineer's view, demoted to second tab.
- **"Tools" (AI)** stays in Library but the AI failure-diagnosis entry point
  also appears contextually on any failed run (Banner → "Ask AI").
- The 5-phase contract is **never** an IA element. It collapses into two user
  verbs: **Preview** (check+plan) and **Update** (apply+verify+cleanup). Power
  users get a "phases" expander inside the run detail drawer only.

---

## 6. Screen-by-screen blueprint

Format per screen: **Purpose · Above-the-fold · Primary action · Secondary ·
Layout zones.**

### 6.1 Dashboard (the home — currently the worst, must become the best)

- **Purpose:** answer J1 + offer J2 in one screen, zero scroll to decide.
- **Above the fold (the Answer Zone — this is the whole point):**
  - One large **status statement**, lime/amber/red by state:
    - `✓ Everything is up to date` · "558 packages · checked 4 min ago"
    - or `▲ 7 updates available` · "across brew, npm · last checked 4 min ago"
  - Directly beside/under it: **one primary button** — `Update all safely`
    (when updates) or `Check for updates` (when current). One. Lime. Nothing
    else lime on the screen.
- **Content zone (below the answer; these are peers, equal weight is fine):**
  - `KpiStrip`: Managed (558) · Outdated (7) · Last run (4m ago, ✓) · Health
    (12/12). Health is **one** StatPair "12 / 12 healthy" → click opens the
    health drawer (the 12 rows live there, not on the page).
  - `Timeline` (last 5 runs): time · profile · result pill · "report". The only
    recent-activity surface; replaces the raw UUID card.
- **Secondary actions:** "Check now" (ghost, when "Update" is primary), per-run
  "report" links in the timeline.
- **Deleted:** the 5 stacked lime buttons, the donut, "100/100", the 12-row
  health card, the raw run-dump card, "Edit layout", the disclosure, the bottom
  status bar, both "Refresh" buttons.
- **Zones:** Header → **Answer (status + 1 CTA)** → KpiStrip → Recent runs.

### 6.2 Runs

- **Purpose:** start a run with the least friction; the wizard is the exception.
- **Above the fold:**
  - Sub-tabs: Start · Scheduled · History.
  - **Start:** two large choices, *not* a wizard — `Safe update` (recommended,
    primary) and `Quick check` (secondary). One click each → confirm sheet → go.
    An "Advanced…" disclosure (collapsed) holds profile/scope/dry-run for the
    rare power case.
  - When a run is active, the Start panel is **replaced** by the live
    `RunPanel` (progress + streamed mono log + Stop). Stop is always visible
    (fixes the Sesja 74 "Stop unreachable" bug by design — Stop lives in the
    RunPanel header, never inside a collapsed step).
- **Primary action:** `Safe update`.
- **Secondary:** Quick check, Advanced, (during run) Stop, View report.
- **Deleted:** the mandatory 3-step Profile→Next→Options→Continue→Confirm
  wizard; the redundant "Run Center" heading; the "How do I start a run?"
  disclosure.
- **Zones:** Header(+subtabs) → choice pair OR live RunPanel → recent runs strip.

### 6.3 Library — Apps (default) / Sources / Tools

- **Purpose:** see and act on what's managed, by app or by source.
- **Above the fold:**
  - Sub-tabs: **Apps** (default) · Sources · Tools.
  - A compact summary line: "558 managed · 7 outdated" + filter chips
    (All / Outdated / by source) + search.
  - `DataTable`: App · Source · Installed → Available · Status. **One** row
    action: a `⋯` menu (Update · Preview · History · Exclude) OR, on hover, a
    single `Update` button when the row is outdated. **Never 6 buttons.** Bulk:
    select rows → one toolbar `Update selected`.
  - Sources tab = the same table grouped by source with a per-source `Update`
    (the 5 phases live behind "Advanced ▸" in the row's drawer, for the one
    engineer who needs them).
- **Primary action:** `Update outdated` (header CTA; → "Check" when none).
- **Secondary:** filter, search, per-row menu, bulk toolbar.
- **Deleted:** the 6-button phase grid; "Categories" heading; the disclosure;
  inconsistent OK-pill vs plain-text counts (all counts → `StatusPill`).
- **Zones:** Header(+subtabs) → summary+filter+search → DataTable → drawer on row.

### 6.4 Insights (already close — standardize it)

- **Purpose:** trust over time. Keep the structure, fix the craft.
- **Above the fold:** `KpiStrip` (Total runs · Success rate · Avg duration ·
  Last run) — keep.
- **Content:** 2-col: `Trends` (one chart, **one accent color**, real axis +
  legend), `Recent failures` (Banner-style rows → drawer report). Below:
  `Duration` (single-hue meaningful bars), `Recent changes`.
- **Fix:** unify chart palette (lime = positive trend, `--fg-faint` =
  baseline; no random teal/amber). Move "Operational notes" prose out of
  Insights into Settings → Support (it's documentation, not analytics).
- **Primary action:** `Open latest report`.
- **Zones:** Header → KpiStrip → 2×2 grid → (notes removed).

### 6.5 Settings

- **Purpose:** configure without a card-soup.
- **Above the fold:** sub-tabs (General · Integrations · Sync · Support ·
  About). General = a **single-column form list** of grouped `Field`s
  (Defaults, Appearance, Profiles, Backup, Scheduler, AI backend), each group a
  `Card` with a title and content-driven height. No 12-col mismatched grid, no
  drag handles.
- **Primary action:** sticky `Save` (only enabled when dirty; keep the existing
  "Saved ✓" flash — it's good).
- **Deleted:** the layout editor, drag handles, mismatched card heights.
- **Zones:** Header(+subtabs) → single-column grouped form → sticky Save.

### 6.6 Detail drawer (new shared surface — replaces ad-hoc dumps)

- Opens from: a run row, an app row, a failed-run banner, a health KPI.
- Header: human title ("brew run · 4 min ago", "Visual Studio Code") + close.
- Body (progressive): summary → per-item changes → **collapsed** "Raw log /
  phases" (mono — the only place UUIDs/phases/sidecar data live).
- Footer: contextual actions ("Open report", "Retry", "Ask AI", "Exclude").

---

## 7. The "answer-first" pattern (the single most important change)

Every screen's top band is an **Answer Zone**: a one-glance statement of state +
the one move. This is what turns Ascendo from a control panel into an
instrument. Worked examples:

| Screen | Answer Zone says | One move |
|---|---|---|
| Dashboard (clear) | `✓ Up to date · 558 packages · checked 4m ago` | Check now (ghost) |
| Dashboard (work) | `▲ 7 updates · brew, npm · last checked 4m ago` | **Update all safely** |
| Runs (idle) | `Ready · last run ✓ 4m ago` | **Safe update** |
| Runs (active) | `Running safe update · brew 3/7 · 0 failed` | Stop |
| Library | `558 managed · 7 outdated` | **Update outdated** |
| Insights | `203 runs · 74% success · 1m39s avg` | Open latest report |

If a screen can't state its answer in one line, the screen is wrong.

---

## 8. Before → after (the redesign in one table)

| Today | Target |
|---|---|
| Answer ("up to date") buried dead-last | Answer is the first thing, above the fold, every screen |
| 5 stacked lime buttons + 6-button rows + wizard + header CTA | Exactly one primary action per screen |
| 6 different card structures per screen | One `Card` primitive, role variants |
| 12 "● OK" rows on the home screen | "12 / 12 healthy" StatPair → drawer |
| Run UUID + ISO time + "dry-run: no" blob | "brew run · 4 min ago · ✓" in a Timeline |
| 3 CSS skins + 39 `!important` + 3× router patch | One stylesheet, one shell, one router |
| layout-editor / drag handles in production | Deleted (or dev-flag only) |
| Mobile nav floating mid-page, cards clipping | Fixed bottom tab bar; content-sized cards |
| Mono-caps eyebrow on every block | Sentence-case sans; mono only for machine data |
| ~800 KB blocking, double-fetch | Deferred/split load, single data path (see plan) |

Implementation sequencing, file-by-file, in `ASCENDO-IMPLEMENTATION-PLAN.md`.
