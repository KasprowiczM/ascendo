# Ascendo — UX / UI / Frontend Architecture Audit

> Principal product designer + senior frontend architect review.
> Evidence base: full read of `app/frontend/` architecture + live inspection of
> the running app at `http://127.0.0.1:8765` (Dashboard, Runs, Library, Insights,
> Settings, mobile 390px) on 2026-05-15. 0 console errors observed — **this is a
> design and architecture problem, not a bug-crash problem.** The JS "works";
> the product experience does not.

---

## 0. One-paragraph diagnosis

Ascendo's UI feels weak because **it is not a designed product surface — it is
an archaeological dig of three stacked redesign attempts that were never
collapsed into one.** `style.css` (3,080 lines) is skinned by `ui-redesign.css`
(823 lines, 39 `!important` fighting the cascade) and then by
`layout-editor.css`; `app.js`'s router (`ui.show`, app.js:314) is monkey-patched
**three times** (`shell.js` → `ui-redesign.js` → `layout-editor.js`), and
`ui-redesign.js` runs a *parallel shadow renderer* that re-fetches data and
re-injects DOM on top of what `app.js` already drew. The result on screen: every
card is a different shape, the most important answer the product exists to give
("is anything out of date?") is buried dead-last below the fold, the primary
actions are exposed as the raw 5-phase engineering contract
(`check/plan/apply/verify/cleanup` — six buttons on every Library row), a
power-user "Edit layout" tool with always-visible drag handles leaks into the
default end-user surface, and the mobile layout is structurally broken (the
bottom tab bar floats mid-page over content; cards clip their own contents).
The token system in `colors_and_type.css` is actually good — the failure is that
nothing downstream consistently respects it, and there is no component layer, no
single shell, and no information hierarchy. It reads like an engineering control
panel, not a calm, confident SaaS dashboard.

---

## 1. Discovery — what Ascendo actually is

### 1.1 Frontend stack

| Aspect | Reality | Evidence |
|---|---|---|
| Framework | None. Vanilla JS SPA, no build step, no bundler, no minify | `app/frontend/*.js` raw; served by FastAPI `core/ascendo/dashboard/app.py` |
| Markup | One 1,788-line `index.html` (111 KB) with **14 inline `<section class="view">`** | `index.html` views: overview L193, apps L287, categories L336, run L382, history L511, logs L540, sync L578, suggest L687, hosts L768, schedule L834, settings L910, help L1142, about L1591, insights L1681 |
| Routing | Hash router `ui.show()` at `app.js:314`, wrapped by `shell.js` (5-destination IA) and then by 2 more layers | `shell.js:309-336`, `ui-redesign.js:761`, `layout-editor.js:391` |
| Styling | `colors_and_type.css` tokens (387 ln) → `style.css` (3,080 ln) → `ui-redesign.css` (823 ln) → `layout-editor.css` (71 ln) | `index.html` L42, L43, L47, L50 |
| State/data | Loose `window` globals + a `frontendCache` Map; `fetch` wrapper; SSE `EventSource` | `app.js` api L23-44, frontendCache L56-91, SSE L1100/1249/3301 |
| i18n | `i18n.js` 2,758 lines / **205 KB ships EN+PL on every load**, render-blocking before `app.js` | `index.html:1773` |
| Backend | ~16 FastAPI route modules — **healthy and not the problem** | `core/ascendo/dashboard/routes/*.py` |

### 1.2 The layering problem (the root cause)

`index.html` loads, in order: `colors_and_type.css` → `style.css` →
`ui-redesign.css` → `layout-editor.css` (CSS), then `icons.js` → `i18n.js` →
`platform.js` → `app.js` → `shell.js` → `ui-components.js` → `ui-redesign.js` →
`layout-editor.js` (JS, end of body, **sequential, no `defer`**).

```
app.js          owns the real router + every view renderer (6,242 lines)
  └─ shell.js          wraps ui.show() → 5-destination chrome
       └─ ui-components.js   runtime-upgrades <select> → segmented controls
            └─ ui-redesign.js     re-parents DOM + SHADOW-RENDERS Insights/doc-viewer
                 └─ layout-editor.js   wraps ui.show() AGAIN + drag-reorder
```

`ui.show` is monkey-patched **3×** on top of the original. There are **two
independent `hashchange` paths** (`app.js` bootstrap + `shell.js:327`).
`ui-redesign.js` does its *own* `fetch("/runs?limit=200")` and `innerHTML`
chart injection for Insights with a `MutationObserver` re-render — a second data
pipeline duplicating `app.js`'s `loadInsights`. This is why the UI feels
non-deterministic and "fragile but not broken": multiple owners draw the same
pixels.

### 1.3 Core product jobs-to-be-done (JTBD)

Ascendo is a cross-platform "keep this machine's software current" tool spanning
brew / mas / npm / pip / web / softwareupdate (+ winget / registry / Windows
Update / Dell on other OSes). The user's real questions, in priority order:

| # | Job | "I want to know / do…" | Frequency |
|---|---|---|---|
| J1 | **Status** | "Is anything out of date? Does my machine need attention?" | Every visit |
| J2 | **Act** | "Update everything safely, now, in one move." | Most common action |
| J3 | **Preview/Review** | "What exactly will change / what changed?" | Per run |
| J4 | **Automate** | "Keep it current without me." | Once, then forget |
| J5 | **Tune** | "Manage which apps/sources are in scope; exclude things." | Occasional |
| J6 | **Diagnose** | "Why did a run fail / what should I do?" | On failure |

### 1.4 Primary workflow loop (what *should* be one motion)

`Open → see status (J1) → one click: Safe Update (J2) → watch progress (J3) →
see human report (J3) → done`. Secondary: `Schedule it (J4)` and forget.

### 1.5 The UI ↔ value mismatch (the core finding)

| Product value | What the UI actually foregrounds |
|---|---|
| "Everything is current — relax" / "N updates → fix" | Buried dead-last on Dashboard in a plain card ("AVAILABLE UPDATES → Everything is up to date") below 4 unrelated cards |
| "One click to safely update" | A 3-step wizard (≥3 clicks) on Runs, **and** a 6-button-per-row phase grid on Library |
| Calm confidence | 5 giant solid-lime stacked buttons + 12 identical green "OK" rows + raw run UUIDs + drag handles |
| Outcome | Implementation: the 5-phase contract, "sources", "adapters", "components", layout editor — all engineering internals exposed raw |

**Ascendo shows the user its machinery instead of its outcome.**

---

## 2. Critical audit

Severity scale: 🔴 **Critical** (blocks the core JTBD / looks broken / not
shippable) · 🟠 **Major** (materially hurts usability or perceived quality) ·
🟡 **Minor** (polish).

### 2.1 Architecture & maintainability

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| A1 | **Three stacked, conflicting CSS skins.** `style.css` (3,080 ln) + `ui-redesign.css` (823 ln, **39 `!important`** redeclaring `.card`, `.app-header`, overview buttons) + `layout-editor.css`. | Every visual decision is resolved by source-order + `!important` warfare, not design. Nobody can predict what a class does. Any "fix" risks regressions across 3 files. | 🔴 | `index.html` L43/47/50; `ui-redesign.css` 39× `!important` vs 7 in `style.css` |
| A2 | **Router monkey-patched 3×; parallel shadow renderer.** `ui.show` wrapped by shell.js, ui-redesign.js, layout-editor.js; `ui-redesign.js` independently fetches `/runs?limit=200` and re-injects Insights via MutationObserver. | Two code paths render the same screens → flicker, double fetches, non-deterministic DOM. The Insights select not upgrading and cards clipping are symptoms of layers racing. | 🔴 | `shell.js:309`, `ui-redesign.js:721-747/761`, `layout-editor.js:391` |
| A3 | **476 hard-coded `px` values in `style.css`** despite a complete `--space-*` ramp existing. | The 4px spacing system in `colors_and_type.css` is bypassed → the "random spacing rhythm" the user complains about is literally in the code. | 🟠 | `colors_and_type.css:345-355` (ramp) vs `style.css` 476 literal px |
| A4 | **No component layer.** 82 `innerHTML=` string injections vs 262 `createElement` in `app.js`; cards/tables/badges built ad-hoc per view; no factory. | Every screen reinvents card/table/badge → the inconsistency is structural, not cosmetic. Impossible to restyle globally. | 🔴 | `app.js` mixed render strategy |
| A5 | **Duplicate rules.** `.card` declared at `style.css:239` *and* `:3014`; `.app-header` at `:2336` *and* `:2990`; then re-overridden in `ui-redesign.css`. | One concept, ≥3 definitions. Confirms there is no single source of truth for any component. | 🟠 | `style.css:239/3014/2336/2990` |
| A6 | **Power-tool leakage.** `layout-editor.js`/`.css` (untracked) ships an "Edit layout" button + **always-visible drag handles on every card, on desktop and mobile**, in the default end-user surface. | Makes a finished product look like a half-built admin tool. This is the single biggest "this isn't production-grade" signal on screen. | 🔴 | Visible top-of-card ⠿ handles on Dashboard/Settings, all viewports |

### 2.2 Layout & information hierarchy

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| L1 | **The Dashboard is not a dashboard — it's a scroll of 4 mismatched cards then more cards.** No KPI strip, no "answer first". | The user cannot answer J1 ("anything out of date?") without scrolling past 4 unrelated cards to the very bottom. The product's whole reason for being is invisible above the fold. | 🔴 | Dashboard screenshot: "AVAILABLE UPDATES → Everything is up to date" is the *last* element on the page |
| L2 | **Card-pattern chaos.** On one Dashboard view: a 5-stacked-button card, a 12-row health-list card, a raw-text run-dump card, a big-number card, a donut card, a bar card — six different internal structures, densities, and type treatments. | No visual rhythm; eye has no anchor; everything competes. This is precisely the "inconsistent card patterns / random spacing" the brief forbids. | 🔴 | Dashboard screenshot |
| L3 | **Wrong visual weight.** Five **full-width solid-lime** buttons (Build inventory / Quick check / Safe update / Full dry run / Full update) dominate the top-left; the actual status answer has none. | Accent is spent on a button wall, not on signal. Violates "no too-many-accents / no gradient sludge". | 🔴 | Dashboard screenshot |
| L4 | **Massive empty canvas on action screens.** Runs/start = one dropdown + a wizard in the top 1/3; ~2/3 of the viewport is black void. Library has a 6-row table then emptiness. | Wasted space + no context (no "what will this do", no recent activity) = low confidence, looks unfinished. | 🟠 | Runs + Library screenshots |
| L5 | **Double titling everywhere.** Page header "Runs" immediately followed by `<h2>` "Run Center"; "Library" then "Categories"; "Dashboard" then "Overview". | Redundant; wastes the most valuable vertical space; signals the new shell was bolted over old per-view headings (confirmed: HANDOFF Sesja 73 carry-forward admits this). | 🟠 | All inner screens |
| L6 | **Insights is the only real dashboard in the app — and it's not the Dashboard.** It has the correct pattern (KPI strip + 2×2 grid) the home screen should have. | Proves the team *can* design a clean dashboard; the home screen simply doesn't. The fix is partly "promote Insights' pattern to home". | 🟠 (insight) | Insights screenshot |

### 2.3 Navigation & wayfinding

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| N1 | 5-destination IA (Dashboard/Library/Runs/Insights/Settings) is **conceptually sound** but implemented as a monkey-patch over a legacy 14-view router. | Good IA, fragile delivery. Sub-tabs are synthesized at runtime (`shell.js:203`), not in markup → they can desync from the visible view (seen: select not upgrading). | 🟠 | `shell.js:30-86` IA model |
| N2 | **Inconsistent global elevation status.** Status bar says "Administrator not authorized" on Dashboard, "sudo not cached" on Runs/Library — for the same machine state. Sidebar simultaneously says "admin permission". | Three contradictory truth claims about privilege on one app = erodes trust in everything else the UI says. | 🟠 | Dashboard vs Runs/Library screenshots |
| N3 | **Three "Refresh" buttons + "Re-check" + "Edit layout" on one Dashboard.** No single, obvious primary action. | Decision paralysis; the user can't tell what the app wants them to do. | 🟠 | Dashboard screenshot |
| N4 | "How do I…/What is…/What does each section do? Click to expand" disclosure repeated on **every** inner screen. | A tool the user runs daily should not re-teach itself every screen, every visit. Treats the UI as undocumented (because it is). | 🟡 | Dashboard, Runs, Library screenshots |

### 2.4 Data presentation

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| D1 | **12 identical "● OK" rows** for System Health. | Zero signal (all OK), maximum noise. A health widget should say "12 / 12 healthy" and only expand on problems. | 🟠 | Dashboard screenshot |
| D2 | **Raw engineering data shown to users**: full run UUID `71a20caf-55d6-4e48-9f78-987ee0a7abd7`, locale timestamps, `profile: quick, dry-run: no` as a flat blob. | Looks like a debug log, not a product. Users don't read UUIDs. | 🟠 | Dashboard card 3 |
| D3 | **Misleading bars.** "PER CATEGORY" bars are near-equal width for brew (151) vs npm (9) — not proportional to the numbers beside them. | A chart that contradicts its own labels is worse than no chart. | 🟠 | Dashboard + mobile screenshots |
| D4 | **Donut overkill.** A full radial ring to display "558 / 100% ok" — one number that is always ~100%. | Heavy chart for a trivial scalar; dominates a whole mobile screen. | 🟡 | Dashboard/mobile |
| D5 | **Inconsistent value styling**: `OK` counts are green pills, `OUTDATED`/`MISSING` are plain text — same data type, different treatment. | Visual grammar isn't a grammar; reader can't learn the rules. | 🟡 | Library screenshot |
| D6 | **Chart palette is arbitrary**: Insights uses a teal line, a lime area, and amber+lime bars with no legend or semantic meaning. | Color must mean something. Random color = decoration, not data. | 🟠 | Insights screenshot |

### 2.5 Interaction & ergonomics

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| I1 | **The 5-phase contract is the primary UI.** Every Library row has 6 buttons: `check plan apply verify cleanup ▶ run all`; 6 rows = ~36 buttons, several solid-fill. | Users think "update brew", not "verify-phase brew". The internal orchestrator contract is dumped on the user as the main control. Highest-traffic screen, worst ergonomics. | 🔴 | Library screenshot |
| I2 | **Most common action is a wizard.** "Quick check"/"Safe update" requires Profile → Next → Options → Continue → Confirm → Start (≥3 clicks). HANDOFF Sesja 74 itself flags this. | The #1 job is gated behind a multi-step form. Should be one click. | 🔴 | Runs screenshot; HANDOFF Sesja 74 carry-forward |
| I3 | **Four ways to do the same thing, none canonical.** Dashboard 5 lime buttons vs Library 6×6 grid vs Runs wizard vs header "Start Run". | No coherent workflow; the brief's "all actions connected into one coherent workflow" is violated four ways. | 🔴 | Cross-screen |
| I4 | `ui-components.js` is supposed to upgrade every `<select>` to a no-dropdown segmented control; on Runs the Profile field is still a **raw native `<select>`**. | The enhancement layer doesn't reliably apply (layer race, A2) → inconsistent controls. | 🟠 | Runs screenshot |

### 2.6 Responsive (mobile 390 px) — structurally broken

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| R1 | **Bottom tab bar floats in the middle of the page**, overlapping the System Health card, instead of being fixed to the viewport bottom. | The primary mobile navigation is not where mobile nav goes and covers content. This alone makes mobile feel broken. | 🔴 | Mobile screenshot |
| R2 | **Cards clip their own content.** "Build inventory" button is cut off at the top of card 1; the "100" of "100/100" is cut off; System Health shows ~4 of 12 rows in a short fixed-height box. | Fixed card heights + overflowing content = visibly broken layout on the device class the brief says must be tolerated. | 🔴 | Mobile screenshot |
| R3 | **Drag handles + "Edit layout" present on mobile.** Drag-reorder on a touch surface with no affordance to finish; pure clutter. | Confirms the layout editor was never scoped to a context. | 🟠 | Mobile screenshot |
| R4 | Donut consumes a near-full mobile screen to show "558 / 100% ok". | One scalar eats one screen of scroll; the answer ("up to date") is still after it. | 🟡 | Mobile screenshot |

### 2.7 Visual system, typography, states

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| V1 | **Token system is good but unused.** `colors_and_type.css` defines a complete, well-reasoned ramp (ink/paper/lime, status, type scale, 4px spacing, radii, shadows, motion, 0 `!important`). The rest of the app ignores it (476 literal px, 39 `!important` skins). | The redesign does **not** need a new design language — it needs the existing one *enforced*. Big de-risking insight. | 🟠 (insight) | `colors_and_type.css` is sound |
| V2 | **Mono-uppercase eyebrows on everything** ("SYSTEM HEALTH", "PER CATEGORY", "AVAILABLE UPDATES", "RUN TRENDS", "OPERATIONAL NOTES", "MACOS"). | JetBrains-Mono caps labels on every block = "terminal log dump" aesthetic, the opposite of "premium SaaS / calm / confident". Hierarchy collapses because everything is the same loud micro-label. | 🟠 | All screenshots |
| V3 | **Weak hierarchy.** Body, labels, values, headings sit in a narrow size band; the only strong contrast is solid-lime fills. Eye has nowhere to land first. | "Strong hierarchy and ergonomics" / "fast scanning" — currently impossible; nothing is clearly most-important. | 🟠 | All screenshots |
| V4 | **Empty / loading / error states are ad-hoc.** "Everything is up to date." is plain text at the bottom; no designed empty/celebratory state; loading is per-view improvised; failures only visible by reading a list on Insights. | The most emotionally important moment ("you're all clear") gets zero design; failure has no first-class surface. | 🟠 | Dashboard / Insights |
| V5 | Score "100/100 Healthy" and the "558 100% ok" donut and "System Health 12 OK" all say the same thing three ways on one screen. | Triple redundancy of the single fact, while the actual updates answer is hidden. Inverted priority. | 🟠 | Dashboard screenshot |

### 2.8 Performance risks

| ID | Finding | Why it hurts | Sev | Evidence |
|---|---|---|---|---|
| P1 | ~**800 KB+ unminified, unbundled, render-blocking** front-end: `app.js` 262 KB + `i18n.js` 205 KB + `style.css` 117 KB + `index.html` 111 KB + `ui-redesign.*` 63 KB + rest, loaded sequentially with **no `defer`**. | Even on localhost the parse/exec of a 6,242-line `app.js` after a 2,758-line i18n blob delays first paint. "Fast-loading" is not currently true. | 🟠 | File sizes; `index.html` script block |
| P2 | `i18n.js` ships **both EN and PL** (205 KB) on every load regardless of locale. | ~100 KB of dead weight per session. | 🟡 | `i18n.js` |
| P3 | Double data fetches: `app.js` `loadInsights` *and* `ui-redesign.js`'s own `/runs?limit=200` poll + MutationObserver. | Redundant network + reflow churn. | 🟠 | `ui-redesign.js:721-747` |
| P4 | No caching strategy beyond a Sesja-73 `no-cache` add; full re-download of ~800 KB on every hard load. | Slow repeat loads for a tool opened many times a day. | 🟡 | HANDOFF Sesja 73 |

---

## 3. "Do NOT do this" — anti-patterns specific to Ascendo

These are the exact mistakes that produced today's UI. The redesign must
forbid them by construction.

1. **Do not add a 4th skin layer.** No `ui-redesign-v2.css`. Conflicting layers
   + `!important` are *the* root cause. One stylesheet, tokens-only, replace —
   never overlay.
2. **Do not monkey-patch `ui.show` again.** One router, one owner. No
   `MutationObserver` re-render, no shadow renderer, no second `/runs` fetch.
3. **Do not expose the 5-phase contract as UI.** `check/plan/apply/verify/cleanup`
   are orchestrator internals. The user sees "Update" and "Preview", never six
   verbs per row.
4. **Do not ship the layout editor / drag handles in the default surface.**
   Delete it from production or gate it behind an explicit dev flag.
5. **Do not show UUIDs, ISO timestamps, "profile: quick, dry-run: no", or
   adapter/component names** to the end user as primary content. Relative time,
   human labels, progressive disclosure.
6. **Do not bury the answer.** "Anything out of date?" is the first thing on the
   first screen, above the fold, before any chart.
7. **Do not gate the #1 action behind a wizard.** Safe update = one click with a
   confirm; advanced options are progressive, not mandatory.
8. **Do not "3 feature cards" the app.** Equal-weight card rows where everything
   is the same size = no hierarchy. Cards must differ by role, not by accident.
9. **Do not put a mono-uppercase eyebrow on every block.** Reserve mono for
   actual machine data (versions, hashes, logs) only.
10. **Do not use a donut/area/bar just because data exists.** A 100% scalar is a
    number with a status color, not a ring.
11. **Do not redefine `.card`/`.app-header` more than once.** One definition,
    token-driven, period.
12. **Do not let status copy disagree with itself.** One elevation truth, one
    string, one place.

---

## 4. What is *good* and must be kept

Honest credit — the redesign is a consolidation, not a teardown of everything:

- **`colors_and_type.css` token system** — keep wholesale. Ink/paper/lime,
  status (bright fill + AA text split), 4px spacing ramp, type scale, radii,
  shadows, motion. Genuinely well-reasoned. The redesign builds *on this*.
- **The 5-destination IA** (Dashboard / Library / Runs / Insights / Settings) —
  conceptually correct. Keep the model; rebuild the delivery.
- **Insights screen pattern** — KPI strip + grid is the right dashboard
  language. Promote it to the home screen and standardize it.
- **Backend** — ~16 clean route modules, SSE, sidecar contract. Untouched by
  this work. The frontend is the entire problem.
- **The dark + lime identity** — distinctive, not generic AI sludge. Keep the
  palette; fix how it's *spent* (signal, not button walls).

---

## 5. Audit scorecard

| Dimension | Grade | One-line verdict |
|---|---|---|
| Information architecture (model) | B | Right 5 destinations; wrong delivery |
| Information hierarchy (screen) | F | Answer is dead-last; everything equal weight |
| Layout system | D− | 6 card shapes per screen; 476 hardcoded px |
| Navigation/wayfinding | C− | Sound nav, contradictory status, no primary action |
| Visual consistency | F | 3 skins + `!important` warfare; no component layer |
| Typography | D | Mono-caps everything; no hierarchy band |
| Data presentation | D | Misleading bars, UUID dumps, redundant donuts |
| Interaction/ergonomics | D− | #1 action is a wizard; 36-button table |
| States (empty/load/error) | D | Ad-hoc; the best moment ("all clear") undesigned |
| Responsive | F | Mobile structurally broken (floating nav, clipping) |
| Performance | C− | ~800 KB blocking, double fetches |
| Maintainability | F | Router patched 3×; shadow renderer; untracked layers |
| **Design tokens (the one bright spot)** | **A−** | **Keep entirely; just enforce it** |

**Overall: the product is functionally alive but experientially incoherent.
The recommended path is a clean frontend rebuild on the existing token system
and IA model — not another skin.** See `ASCENDO-REDESIGN-BLUEPRINT.md` and
`ASCENDO-IMPLEMENTATION-PLAN.md`.
