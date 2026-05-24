# ADR 0003: JSON v1 sidecar contract

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Ascendo's core (Python, Layer 4) is OS-agnostic. The actual update work
happens in native scripts (Layer 6: Bash on Linux/macOS, PowerShell on
Windows). These scripts know things core does not — `winget` exit codes,
`apt` dpkg status, `softwareupdate` deferred reboots, fwupd device IDs.

Core needs to:
1. **Drive** native scripts (`run check`, `run plan`, `run apply`,
   `run verify`, `run cleanup` — the 5-phase contract).
2. **Read back** structured results — what was attempted, what succeeded,
   what failed, how to roll back.
3. **Persist** that history into SQLite for the dashboard, scheduler, and
   audit trail.

The pre-merge `Ascendo` repo already had a JSON sidecar format
(`ascendo/v1`) emitted by Bash via `lib/_json_emit.py`. It
worked well, but its name is no longer accurate (we rebranded to Ascendo
and we now have three OSes). It also lacks several fields the cross-OS
work has surfaced as necessary — `host.os`, `host.is_elevated`,
`items[].source.type`, `rollback.method`, etc.

The disagreement was on whether to evolve `ascendo/v1` in
place (rename only) or break it cleanly (`v2` rebranded). We chose
rebrand-without-version-bump because the field set is a strict superset
of v1 — no field was removed, all new fields are optional. A reader that
understands `ascendo/v1` can also parse `ascendo/v1` payloads.

## Decision

**Adopt the JSON sidecar schema name `ascendo/v1`** as the durable
contract between Layer 6 (native scripts) and Layers 4-5 (core + Python
adapters). Native scripts emit a sidecar JSON file at a deterministic
path per phase; Python reads it via the `SidecarReader` interface.

The schema is normative — defined in `core/ascendo/models/sidecar.py` as
Pydantic v2 models, exported as JSON Schema in
`docs/architecture/schemas/sidecar.v1.schema.json`, and validated by
`tests/contract/`.

**Backward compatibility:** the reader accepts both `ascendo/v1`
(canonical) and `ubuntu-aktualizacje/v1` (historical legacy literal).
New native code emits only `ascendo/v1`. Legacy emitters in
`Ubuntu_Aktualizacje` checkouts (pre-rename clones) continue to work
without modification — `parse_sidecar()` routes legacy payloads
through `translate_legacy_v1()` before validating against the v1
Pydantic model.

> ⚠️ **Do not change the legacy literal.** It is the on-disk string
> historical emitters wrote — renaming the project does not retroactively
> rename JSON files on user disks. A mechanical search-and-replace
> already collapsed this once (commit `96d5167`) and broke every
> dashboard-dispatched run with `KeyError: 'kind'`.

## Schema (top-level fields)

| Field          | Required | Notes                                             |
|----------------|---------:|---------------------------------------------------|
| `schema`       |        ✓ | Literal `"ascendo/v1"`                            |
| `run`          |        ✓ | id (uuid), trigger, profile, dry_run, started_at  |
| `host`         |        ✓ | hostname, os, os_version, arch, user, is_elevated, elevation_method |
| `tool`         |        ✓ | name, version, binary_path                        |
| `phase`        |        ✓ | One of: `check`, `plan`, `apply`, `verify`, `cleanup` |
| `category`     |        ✓ | apt / winget / brew / snap / store / dell / etc.  |
| `started_at`   |        ✓ | ISO-8601                                          |
| `finished_at`  |        ✓ | ISO-8601                                          |
| `status`       |        ✓ | `success` / `partial` / `failed` / `skipped`      |
| `items`        |        ✓ | array of package operations (see below)           |
| `summary`      |        ✓ | counts, durations, exit_code                      |
| `messages`     |          | array of human-readable lines (warnings, info)    |
| `rollback`     |          | rollback hints (snapshot id, downgrade command)   |

### `items[]` shape

```jsonc
{
  "id":            "Microsoft.PowerShell",
  "name":          "PowerShell",
  "category":      "winget",
  "source":        { "type": "winget", "feed": "winget" },
  "current_version": "7.6.0",
  "target_version":  "7.6.1",
  "resolved_version": "7.6.1",     // post-apply, what's actually installed
  "status":        "success",       // success | failed | skipped | up-to-date
  "exit_code":     0,
  "evidence": {                     // optional — local-version proof
    "appx_version":  "7.6.1.0",
    "registry_version": "7.6.1",
    "binary_hash":  "sha256:..."
  },
  "duration_ms":   12700,
  "messages":      ["..."],
  "rollback": {                      // optional — per-item rollback method
    "available": true,
    "method":    "winget install --id Microsoft.PowerShell --version 7.6.0",
    "snapshot_id": null
  }
}
```

## Consequences

### Positive

- **Loose coupling.** Bash and PowerShell teams can iterate on their
  scripts without touching Python. As long as the JSON is valid against
  the schema, integration works.
- **Diffable history.** SQLite stores raw sidecars; the dashboard's run
  detail view can show two runs side-by-side at the field level.
- **Cross-OS consistency.** A Linux apt run and a Windows winget run land
  in the same shape. Reporting, metrics, and the dashboard need only one
  rendering codepath.
- **Schema versioning is a first-class concept.** When breaking changes
  arrive, we'll mint `ascendo/v2`, the reader will accept both for one
  release cycle, and emitters migrate at their own pace.
- **Rollback metadata travels with the run** — no separate rollback log
  to keep in sync. If the JSON is missing, rollback isn't available;
  that's a clear, debuggable failure mode.

### Negative

- **JSON Schema validation is now a hard dependency** in CI. Every PR
  that touches a native emitter runs `tests/contract/`. That's a feature
  but it raises the bar for native-script contributors.
- **Schema evolution requires care.** Adding a required field is a
  breaking change. We mitigate by making everything new optional with
  sensible defaults.
- **Bash JSON emission is fiddly.** `jq` is required on Linux/macOS
  runners. We ship `lib/json.sh` helpers to keep the friction low; future
  alternative is calling out to a tiny Python stdlib helper instead.

### Neutral

- The sidecar is **emitted to disk**, not streamed over a pipe. This
  decouples lifetimes: the script can crash mid-run and the partial
  sidecar still tells us how far it got. Streaming is a future option;
  not needed for v1.

## Alternatives Considered

### Alternative 1: Stream stdout as line-delimited JSON

Description: Native scripts print one JSON object per line; core reads
the pipe.

Why rejected:
- Mixes script logging with structured output. Easy to corrupt the stream
  with stray `printf` debug lines.
- No survival across crashes — if the script segfaults mid-line, you get
  an unparsable trailing line.
- Harder to inspect after the fact (unless we write the stream to a log
  file, in which case we've reinvented the sidecar with extra steps).

### Alternative 2: Direct Python subprocess with structured return

Description: Python adapter calls native script via `subprocess.run` and
parses an exit code + stdout convention. No JSON file.

Why rejected:
- Couples the adapter Python tightly to the native script's output
  format. Schema changes require coordinated edits in two places.
- Doesn't scale to non-Python consumers (Tauri, scheduler, future
  agent integrations).
- Scripts emit hundreds of lines of output during apt/winget runs; a
  separate sidecar lets us keep that human-readable while the structured
  view lives elsewhere.

### Alternative 3: Use `dpkg`/winget native output formats directly

Description: Don't define our own format. Have core parse `winget
upgrade --output json`, `apt-get install --print-uris`, etc.

Why rejected:
- Each OS package manager has a different output schema. Core ends up
  with N parsers for N tools. The Windows column-position parser story
  shows how brittle this gets.
- No place to attach Ascendo-specific concepts (run id, phase, rollback
  method, evidence).
- Tools change their output formats unilaterally. We'd be chasing
  upstream format changes forever.

## References

- Related ADRs: [0001](0001-monorepo-with-adapters.md), [0004](0004-python-core-with-native-script-adapters.md),
  [0005](0005-six-layer-architecture.md), [0007](0007-plugin-manifest-v1.md)
- Schema source: `core/ascendo/models/sidecar.py`
- JSON Schema export: `docs/architecture/schemas/sidecar.v1.schema.json`
  (generated in M2)
- Contract tests: `tests/contract/test_sidecar_v1.py` (M2)
- Pre-merge contract: `Ascendo/docs/agents/contract.md`
