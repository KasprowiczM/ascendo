# ADR 0006: Two-tier adapter system (Tier 1 / Tier 2)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Open-source projects that try to be "all-OS, all-package-managers,
forever" tend to fail in one of two predictable ways:

1. **They die under maintenance load.** Every random distro adapter must
   be tested by core maintainers. CI matrix grows linearly with each
   addition. Eventually a release ships with FreeBSD broken, the
   FreeBSD maintainer has moved on, and the bug stays open for a year.
2. **They reject community contributions to stay shippable.** "Sorry,
   we don't accept new adapters" → ecosystem stagnates.

The Linux ecosystem learned this lesson with `pkgsrc`, with Homebrew
taps, with Linux distro support tiers. The pattern that works is
**explicit two-tier adapter status with different SLAs and acceptance
criteria for each tier.**

For Ascendo specifically:

- We want **Linux + Windows + macOS to be supported and tested** — those
  are our v0.1.0 target OSes. Each is rich enough to need full Python
  bindings, full snapshot integration, full plugin support.
- We want **community OSes (FreeBSD, Fedora, Alpine, ChromeOS,
  OpenSUSE...)** to be addable without core team buy-in or maintenance
  obligation.

## Decision

**Adopt two adapter tiers with different folders, different requirements,
and different SLAs:**

### Tier 1 — `adapters/<os>/` (Official)

- **Acceptance gate:** core team approval, full Python adapter
  implementation, all interfaces satisfied, contract tests passing in
  CI matrix, documentation in `adapters/<os>/README.md`.
- **Maintenance:** core team commits to fixing breakages within reasonable
  time. Cuts a release if a Tier 1 OS is broken.
- **CI:** dedicated GitHub Actions runner per OS. Every PR runs against
  every Tier 1 OS.
- **Layout:** Python package + `scripts/` + `lib/` + `tests/` + README.
- **Initial members:** Ubuntu, Windows. **macOS** joins at M5.

### Tier 2 — `contrib/adapters/<os>/` (Community)

- **Acceptance gate:** maintainer name in `contrib/adapters/<os>/MAINTAINERS`,
  manifest declaring claimed interfaces, smoke test that runs in CI.
- **Maintenance:** owned by the contributor. Core team will not fix
  Tier 2 breakages but will not block releases on them.
- **CI:** smoke test only. No full matrix. Marked `experimental`.
- **Layout:** Manifest-driven, mostly-scripts. Python adapter optional;
  if absent, falls back to a generic dispatcher in core.
- **Initial members:** none. The `_template/` scaffold is provided for
  early contributors.

### Promotion path Tier 2 → Tier 1

After ≥ 3 months in `contrib/`:

1. The OS has at least one external user (verifiable by Issues / PRs).
2. The community maintainer signals willingness to be in the official
   matrix.
3. Core team review confirms:
   - Full interface implementation present.
   - Contract tests pass.
   - Snapshot integration exists or is gracefully unavailable.
   - Code style conforms to top-level standards.
4. PR moves the adapter from `contrib/adapters/<os>/` to `adapters/<os>/`
   and adds it to the CI matrix.

### Demotion path Tier 1 → Tier 2

If a Tier 1 maintainer disappears and bugs go unfixed for > 2 minor
releases, the adapter is demoted with a CHANGELOG entry. Demotion is a
mechanical move, not a deletion.

## Consequences

### Positive

- **Low barrier to entry for community OSes.** You can add support for
  Fedora in a weekend with a manifest + a few scripts. You don't need
  to know Python.
- **Predictable quality bar for users.** "If your OS is in `adapters/`,
  it's tested. If it's in `contrib/`, it's experimental." Users self-
  select their risk tolerance.
- **Core team workload is bounded.** Tier 1 grows slowly and deliberately;
  Tier 2 grows by community demand without core involvement.
- **No "dead adapter" graveyard.** When a Tier 2 OS goes stale, demoting
  is a paperwork move, not a political fight. Tier 1 maintainers either
  keep up or get demoted publicly. Health is visible.
- **Promotion creates a natural milestone.** A Tier 2 → Tier 1 promotion
  is a celebration-worthy CHANGELOG entry. Contributors see a path to
  earning trust.

### Negative

- **Two folders for "the same kind of thing."** Newcomers may briefly
  confuse Tier 1 and Tier 2. Mitigated by READMEs in both folders that
  explicitly link to this ADR.
- **Tier 2 fallback dispatcher in core.** Core needs a generic "run
  this script for this OS" path when the Tier 2 adapter has no Python
  module. That dispatcher is itself a piece of code we have to maintain.
  Acceptable; it's small and well-tested.
- **Two CI surfaces.** Tier 1 matrix (full) + Tier 2 smoke (each).
  Mitigated by parallel workflow runs; a Tier 2 failure doesn't block
  Tier 1 release.
- **Bikeshedding risk on promotion criteria.** Mitigated by writing them
  down (this ADR) and refusing to debate them on individual PRs.

### Neutral

- The `plugins/` folder follows the same pattern at a finer grain:
  `plugins/<id>/` for official plugins, `contrib/plugins/<id>/` for
  community. Same promotion rules apply.

## Alternatives Considered

### Alternative 1: Single tier — accept everything

Description: Any OS adapter is welcome under `adapters/`, no quality bar.

Why rejected:
- CI matrix explodes. Release cadence drops. Users get burned by
  broken adapters.
- Reputation cost: "Ascendo claims to support Foo OS but it doesn't
  actually work" damages trust regardless of who maintains Foo.

### Alternative 2: Single tier — official only, reject community

Description: Tier 1 only. Community wanting other OSes must fork.

Why rejected:
- Hostile to the open-source ethos. Discourages contribution.
- Real-world projects (k8s, Helm, GitHub Actions runners) all settled
  on tiered models for the same reason — tier 2/3 is the pressure-relief
  valve that keeps tier 1 healthy.

### Alternative 3: External plugin marketplace

Description: Tier 1 OSes only in this repo. All others are plugins
hosted on a separate registry (npm-style).

Why rejected:
- Premature for v0.x. We don't have the discovery / signing / review
  infrastructure a marketplace requires.
- Re-evaluate post-v1.0. The path from `contrib/adapters/` → external
  registry is straightforward when the registry exists.

## References

- Related ADRs: [0001](0001-monorepo-with-adapters.md) (the folders),
  [0004](0004-python-core-with-native-script-adapters.md) (the python-vs-scripts
  split that Tier 2 leans on), [0007](0007-plugin-manifest-v1.md)
- Linux distro tiering precedent: Debian official vs. contrib vs.
  non-free; Fedora official vs. RPMFusion; Arch core vs. AUR.
- HANDOFF.md — section "Dwa tiers adapterów"
