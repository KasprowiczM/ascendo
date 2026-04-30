# docs/

Documentation for Ascendo.

## Structure

```
docs/
├── architecture/           # ADR-driven architecture decisions
│   ├── templates/          # ADR template + boilerplate
│   ├── 0001-*.md           # First decisions
│   └── ...
├── adapter-author-guide.md # how to write a new adapter
├── plugin-author-guide.md  # how to write a new plugin
├── operator-runbook.md     # day-to-day operator tasks
├── i18n-author-guide.md    # adding/updating translations
└── security.md             # threat model + responsible disclosure
```

## ADRs (Architecture Decision Records)

Every significant architectural decision gets an ADR in `architecture/`. Format:

- Numbered sequentially (`0001-`, `0002-`, ...)
- Status: Proposed / Accepted / Superseded / Deprecated
- Context, Decision, Consequences sections

Template: `architecture/templates/adr-template.md`

## See also

- `HANDOFF.md` (repo root) — current implementation state
- `README.md` (repo root) — project overview
