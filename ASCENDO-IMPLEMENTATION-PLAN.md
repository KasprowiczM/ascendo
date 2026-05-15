# Ascendo — Implementation Plan

> How to get from today's 3-layer palimpsest to the blueprint, in your *current
> stack* (vanilla JS + FastAPI, no build step required), with the lowest risk
> path and a prioritized roadmap. Pairs with `ASCENDO-UX-UI-AUDIT.md` (problems)
> and `ASCENDO-REDESIGN-BLUEPRINT.md` (target).

---

## 1. Strategic decision: consolidate, don't re-skin

The audit's root cause is **layering**. Therefore the #1 rule of this plan:

> **Net file count must go DOWN, not up. Every step deletes a layer or merges
> one. No new parallel CSS/JS layer is ever created.**

Two viable strategies:

| Strategy | What | Risk | Recommendation |
|---|---|---|---|
| **A — Consolidate-in-place** | Collapse `style.css`+`ui-redesign.css`+`layout-editor.css` → one `app.css`; collapse `shell.js`+`ui-components.js`+`ui-redesign.js`+`layout-editor.js` → one owned `shell.js`; introduce a tiny `components.js` factory; refactor `app.js` view renderers to use it. Keep vanilla JS, keep FastAPI serving, no build step. | Medium | ✅ **Recommended.** Matches the stack, no toolchain change, incremental, reversible per phase. |
| B — Framework rebuild | Rebuild frontend in a framework + bundler. | High | ❌ Not now. The backend/contract is fine; a framework adds a build step and migration risk for zero user value over Strategy A. Revisit only post-1.0. |

**Proceed with Strategy A.** Everything below assumes it.

---

## 2. Quick wins (Day 1 — high impact, low risk, mostly deletion)

These ship value before any redesign and *prove* the "delete a layer" thesis.

| # | Action | Files | Effect | Risk |
|---|---|---|---|---|
| Q1 | **Remove the layout editor from production.** Delete the `<link>`/`<script>` for `layout-editor.css`/`.js` from `index.html` (L50, L1786); remove the "Edit layout" button + always-on drag handles. | `index.html`; delete `app/frontend/layout-editor.{css,js}` | Kills the single biggest "unfinished" signal app-wide + on mobile. | Trivial — untracked, additive only. |
| Q2 | **Fix the mobile bottom nav.** It floats mid-page. Give it `position: fixed; bottom: 0; left: 0; right: 0; z-index: 50` + add `padding-bottom` to the canvas equal to its height. | `ui-components.js` (MobileBottomNav) or the consolidated CSS | Mobile stops looking broken. | Low — one positioning rule. |
| Q3 | **Stop cards clipping content.** Remove every fixed `height`/`max-height` on `.card` and Dashboard widgets; let cards size to content; the System-Health list scrolls *internally* only if it stays on the page (it won't — see M-tier). | `style.css`/`ui-redesign.css` `.card` rules | "Build inventory"/"100" no longer cut off. | Low. |
| Q4 | **One elevation string.** Pick one source of truth (the `Platform`/elevation status), render it once in the sidebar footer; delete the bottom status bar and the contradictory second string. | `app.js` status render; `index.html` status bar | Trust restored; removes the "Administrator not authorized" vs "sudo not cached" vs "admin permission" contradiction. | Low. |
| Q5 | **Kill the duplicate page titles.** Hide the per-view `<h2>` ("Overview", "Run Center", "Categories") since the shell header already states it. | `index.html` view sections; one CSS rule `.view > h2.view-title{display:none}` (scoped, not blanket — keep ones with embedded actions, then migrate those actions to the header in M-tier) | Reclaims the most valuable vertical space; removes redundancy. | Low–med (verify no action-bearing h2 hidden). |
| Q6 | **Defer scripts + split i18n.** Add `defer` to all `<script>` in `index.html`; serve only the active locale's i18n (split `i18n.js` → `i18n.en.js`/`i18n.pl.js`, load one). | `index.html`; `i18n.js`; `core/ascendo/dashboard/app.py` static route | ~100 KB less + non-blocking parse → faster first paint. | Low–med. |
| Q7 | **Answer-first on Dashboard (interim).** Without rebuilding: move the existing "Available updates" block to the **top** of `#view-overview`, above the action card; restyle it as the status statement. | `index.html` `#view-overview` order; `app.js` overview renderer | The product's core answer is above the fold *today*. | Low. |

**Day-1 outcome:** product stops looking broken/unfinished, loads faster,
states one truth, answers its core question first — with **2 files deleted and
zero files added.**

---

## 3. Medium refactors (Week 1–2 — the design system)

### M1 — Collapse CSS to one token-driven stylesheet
- **Create `app.css`** = `colors_and_type.css` (kept verbatim — it's good) +
  a *rewritten* component layer that replaces `style.css`'s 3,080 lines.
- Delete `style.css`, `ui-redesign.css`, `layout-editor.css`.
- Rules: **zero raw `px`** (use `--space-*`), **zero `!important`**, **one
  definition per selector** (no duplicate `.card`/`.app-header`), flat
  elevation, accent budget enforced in code review.
- Migration tactic: build `app.css` from the *component taxonomy* (§4 below),
  not by editing the old file. Style the new components; throw the old CSS away
  rather than untangle 39 `!important`s.

### M2 — One owned shell (`shell.js`), router un-patched
- Fold `shell.js` (keep its IA model — it's correct), `ui-components.js`
  (control upgrades), and the *useful* parts of `ui-redesign.js` (the drawer,
  doc viewer) into **one `shell.js`** that *owns* the router instead of
  monkey-patching it.
- Delete `ui-redesign.js`. Kill its shadow Insights renderer + `MutationObserver`
  + second `/runs?limit=200` fetch (data comes from `app.js`'s one path).
- `app.js`: extract the existing `ui.show` into a small router the shell owns;
  one `hashchange` listener total.

### M3 — Component factory (`components.js`, ~300 lines)
Introduce the finite taxonomy from the blueprint as plain functions returning
DOM nodes (no framework):

```
Card({title, action, density, tone, children})
StatPair({value, label, trend, status})
StatusPill({status, label, mono})
Button({variant, label, icon, onclick})   // variant: primary|secondary|ghost|danger
Field({label, control, hint, error})
Skeleton({shape})
EmptyState({glyph, line, action})
Banner({tone, text, actions})
KpiStrip([StatPair…])
DataTable({columns, rows, rowMenu})       // ONE row menu, never 6 buttons
Drawer.open({title, body, footer})
Timeline(runs)
```
Every view renderer in `app.js` is refactored to build screens *from these*.
This is the structural fix for "no component layer" (audit A4) and "6 card
shapes per screen" (audit L2).

### M4 — Refactor renderers screen-by-screen (behind the shell, no big-bang)
Order by impact (each is independently shippable):
1. **Dashboard** → Answer Zone + KpiStrip + Timeline (delete donut/100/12-rows/UUID card/5-lime-stack).
2. **Library/Apps** → DataTable + one row menu (delete 6-phase grid; map to Preview/Update).
3. **Runs** → two-choice Start + RunPanel (delete the mandatory wizard; Advanced collapsed).
4. **Insights** → standardize on KpiStrip + unified one-color charts (already closest).
5. **Settings** → single-column grouped `Field`s (delete 12-col mismatch).

---

## 4. File-by-file action table

| File | Today | Action | Outcome |
|---|---|---|---|
| `app/frontend/layout-editor.css` | drag-editor chrome (untracked) | **Delete** | −1 layer, −1 file |
| `app/frontend/layout-editor.js` | 4th `ui.show` wrap + drag (untracked) | **Delete** | −1 layer, −1 file |
| `app/frontend/ui-redesign.css` | 823 ln, 39 `!important` skin | **Delete** (merge intent into `app.css`) | −1 layer |
| `app/frontend/ui-redesign.js` | shadow renderer + drawer + doc viewer | **Delete**; port drawer/doc-viewer into `shell.js` | −1 layer, kill double-fetch |
| `app/frontend/style.css` | 3,080 ln, dup rules, 476 px | **Replace** with new component CSS in `app.css` | one stylesheet, tokenized |
| `app/frontend/colors_and_type.css` | sound token system | **Keep verbatim**; becomes top of `app.css` | de-risked foundation |
| `app/frontend/shell.js` | monkey-patch wrapper (good IA) | **Rewrite** to own router + absorb ui-components/drawer | one shell, one router |
| `app/frontend/ui-components.js` | runtime select upgrades | **Merge** into `shell.js`/`components.js` | −1 file |
| `app/frontend/components.js` | — | **Create** (~300 ln factory) | the missing component layer |
| `app/frontend/app.js` | 6,242 ln: router + every renderer | **Refactor** renderers to use `components.js`; extract router for shell; remove `innerHTML` string injection | consistent, smaller, one render path |
| `app/frontend/i18n.js` | 205 KB EN+PL | **Split** per-locale; load one | −~100 KB/load |
| `app/frontend/index.html` | 14 inline views, no `defer`, dup `<h2>` | Add `defer`; remove deleted layers; remove duplicate titles; views unchanged in count | faster, no double-title |
| `app/frontend/platform.js` | OS abstraction | **Keep** (used by shell) | unchanged |
| `app/frontend/icons.js` | line icon set | **Keep**; enforce single weight | unchanged |
| `core/ascendo/dashboard/app.py` | serves frontend, no-cache, asset whitelist | Update `_spa_assets` to drop deleted files, add `app.css`/`components.js`; add long-cache + content-hash for static | faster repeat loads |
| `core/ascendo/dashboard/routes/*` | ~16 route modules | **No change** — backend is fine | unchanged |

**Net: −5 files (layout-editor.css/js, ui-redesign.css/js, ui-components.js,
style.css) +2 files (app.css, components.js) = the surface shrinks.**

---

## 5. Components: replace / consolidate / introduce

| Disposition | Components |
|---|---|
| **Introduce** | `Card`, `StatPair`, `StatusPill`, `Button`, `Field`, `Skeleton`, `EmptyState`, `Banner`, `KpiStrip`, `DataTable`, `RunPanel`, `Timeline`, `Drawer`, `Sparkline` (all in `components.js`) |
| **Consolidate** | every ad-hoc `.card` → `Card`; all loaders → `Skeleton`; all status text/pills → `StatusPill`; all OK/OUTDATED/MISSING counts → `StatusPill`; `shell.js`+`ui-components.js`+drawer → one `shell.js` |
| **Replace** | the 6-phase row → `DataTable` row menu; the 3-step wizard → two-choice + `Advanced`; the donut/100/12-rows → `KpiStrip` + health drawer; the UUID dump → `Timeline` |
| **Delete** | `layout-editor.*`, `ui-redesign.*`, the always-on disclosure, the bottom status bar, the mono-caps eyebrow class, every duplicate selector |

---

## 6. Suggested design tokens (additions only — base stays)

`colors_and_type.css` is kept whole. Add only these usage-discipline tokens to
the top of `app.css`:

```css
:root{
  /* Layout primitives */
  --shell-sidebar: 240px;
  --shell-rail: 64px;          /* tablet collapsed sidebar */
  --canvas-max: 1200px;
  --drawer-w: 480px;
  --header-h: 64px;
  --bottomnav-h: 56px;         /* mobile */
  --gutter: var(--space-5);
  --gutter-mobile: var(--space-4);

  /* Component contracts */
  --card-pad: var(--space-5);
  --card-gap: var(--space-4);
  --section-gap: var(--space-6);
  --control-h: 36px;
  --tap-min: 44px;             /* touch floor */
  --row-h: 44px;

  /* The accent budget is a RULE, not a token: max 1 filled --accent / screen */
}
```
No new colors, no new fonts, no new shadows. The base ramp already covers them.

## 7. Reusable layout primitives

| Primitive | Definition | Replaces |
|---|---|---|
| `.shell` | grid: sidebar + canvas; drawer is `position:fixed` overlay | the patched 3-layer shell |
| `.app-header` | sticky, `--header-h`, title+desc+1 CTA+subtab rail | both duplicate `.app-header` rules + scattered Refresh/Edit buttons |
| `.answer` | full-width band at top of canvas, status-toned | new — the core pattern |
| `.canvas` | `max-width:var(--canvas-max)`, `--gutter` padding, vertical `--section-gap` flow | ad-hoc per-view wrappers |
| `.grid-kpi` | auto-fit, `minmax(180px,1fr)`, `--card-gap` | the mismatched Dashboard card row |
| `.grid-2` | 2-col → 1-col <768 | Insights/Settings ad-hoc grids |
| `.bottom-nav` | `position:fixed;bottom:0`, 5 items, ≥768 hidden | the broken floating nav |

---

## 8. Performance plan

| Item | Action | Win |
|---|---|---|
| Render-blocking JS | `defer` all scripts (Q6) | non-blocking parse |
| i18n weight | per-locale split, load one (Q6) | −~100 KB |
| Double fetch | delete `ui-redesign.js` shadow Insights (M2) | −1 redundant `/runs?limit=200` + MutationObserver churn |
| Payload | drop 4 deleted files (~180 KB raw) | smaller surface |
| Repeat loads | content-hashed static + long cache in `app.py` | near-instant reopen |
| `app.js` size | as renderers move to `components.js`, dead per-view code is deleted | smaller, one render path |
| Charts | no chart libs; tiny inline SVG `Sparkline`/`MiniBars` only where trend matters | no new dependency |

Target: first meaningful paint of the Answer Zone < 400 ms on localhost; total
transferred < 350 KB.

## 9. Accessibility fixes (fold into the rebuild, not a separate pass)

| Issue | Fix |
|---|---|
| Mono-caps + weak contrast helper text | Use `--fg-muted`/`-faint` per token notes (already AA-tuned); sentence case |
| Status by color only (pills) | `StatusPill` = dot **+ text label** always (never color alone) |
| Tap targets | `--tap-min: 44px` enforced in `Button`/`DataTable` rows |
| Focus | one visible `--border-focus` ring on all interactives (token exists) |
| Drawer/modal | focus trap + `Esc` + return focus (shell owns it once) |
| Sub-tabs | real `role=tablist/tab`, arrow-key roving (shell owns it) |
| Reduced motion | `prefers-reduced-motion` → instant (one media query in `app.css`) |
| Charts | text alternative / data table fallback for `Sparkline` |

---

## 10. Prioritized roadmap

| Phase | Scope | Effort | Ships |
|---|---|---|---|
| **P0 — Stop the bleeding** | Q1–Q7 (delete layout-editor, fix mobile nav, fix clipping, one status string, kill double titles, defer+split, answer-first interim) | ~1 day | Product no longer looks broken/unfinished; loads faster; answers its question first. **2 files deleted, 0 added.** |
| **P1 — Foundation** | M1 `app.css` (collapse 3 CSS layers, tokenize) + M3 `components.js` factory | ~3–4 days | One stylesheet, one component layer. Visual consistency by construction. |
| **P2 — One shell** | M2 rewrite `shell.js` to own the router; delete `ui-redesign.js`/`ui-components.js`; one data path | ~2–3 days | No monkey-patching, no shadow renderer, no double-fetch. Deterministic UI. |
| **P3 — Screens** | M4 refactor renderers: Dashboard → Library → Runs → Insights → Settings (each independently shippable) | ~1–1.5 days each (~6–8 days) | The blueprint, screen by screen. Dashboard first = biggest perceived win. |
| **P4 — Polish** | Empty/loading/error states, motion, a11y pass, perf headers, mobile sheet | ~3 days | Production-grade finish. |

Total ≈ 3–4 focused weeks single-dev. **P0 alone removes ~80% of the
"this feels weak" perception** because most of the pain is the broken mobile,
the layout-editor leakage, the buried answer, and the double titles — all P0.

### Definition of done (per screen)
- States its answer in one line, above the fold.
- Exactly one filled `--accent` action.
- Built only from `components.js` primitives.
- Zero raw `px`, zero `!important`, zero duplicate selectors touched.
- Works at 390 / 768 / 1280 with no clipping and a fixed bottom nav on mobile.
- No UUID/phase/ISO data on the surface (only in the drawer's collapsed raw).

---

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Regressing working SSE/runs/AI wiring | The backend + data layer is **not touched**. Only render + chrome change. Refactor renderers behind a stable `api`/SSE interface. |
| Big-bang rewrite stalls | P3 is screen-by-screen, each independently shippable behind the same shell. Never all-or-nothing. |
| Re-introducing a layer | Hard rule §1: net files must decrease; PR checklist forbids new CSS/JS layer + `!important` + raw px. |
| Losing the layout-editor feature | It is a power-dev tool with no user value in a finished product; if ever wanted, gate behind `?dev=1`, never default. |
| i18n parity (EN/PL) breaks on split | Keep the existing parity check (`scripts/check-i18n-parity.py`); split is mechanical, parity-preserving. |
| Token churn | None — `colors_and_type.css` is kept verbatim; only additive layout tokens (§6). |

---

## 12. First commit (concrete, today)

1. `git rm app/frontend/layout-editor.css app/frontend/layout-editor.js`
2. `index.html`: remove L50 + L1786 (layout-editor link/script); add `defer` to
   every `<script>`; move `#view-overview`'s available-updates block to the top.
3. `ui-components.js`: `position: fixed; bottom:0` on the mobile nav + canvas
   `padding-bottom: var(--bottomnav-h)`.
4. `style.css`/`ui-redesign.css`: delete fixed `height`/`max-height` on `.card`.
5. One commit: `refactor(spa): P0 — remove layout editor, fix mobile nav +
   clipping, answer-first dashboard, defer scripts`.
6. Re-test at 1440 + 390; confirm: no drag handles, bottom nav fixed, nothing
   clipped, "updates" answer above the fold.

Everything after P0 follows the roadmap table. The blueprint
(`ASCENDO-REDESIGN-BLUEPRINT.md`) is the spec each P3 screen is built to.
