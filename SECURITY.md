# Security Policy

## Reporting a vulnerability

**Do not file public GitHub Issues for security vulnerabilities.**

Instead, use one of:

1. **GitHub Security Advisories** (preferred) — go to the [Security tab](https://github.com/KasprowiczM/ascendo/security/advisories/new)
   and click "Report a vulnerability"
2. **Email** — `gaipro.mk@gmail.com` with subject prefix `[ascendo-security]`

We will acknowledge your report within **72 hours** and aim to provide an
initial assessment (severity + tentative fix timeline) within **7 days**.

## Supported versions

While Ascendo is pre-release (< v1.0.0), only the latest release receives
security fixes. After v1.0.0, the policy will be:

- Latest minor version: full support
- Previous minor version: security fixes only, for 6 months
- Older versions: not supported

## What we consider a vulnerability

Examples (not exhaustive):

- **Local privilege escalation** — Ascendo running with sudo/UAC executes
  arbitrary code, allows non-admin user to gain admin
- **Code injection** — Ascendo evaluates untrusted input as shell, Python,
  PowerShell, or SQL
- **Plugin sandbox escape** — a plugin reads/writes outside its declared
  permissions allowlist
- **Token exposure** — dashboard auth token logged, shown in URL params,
  or transmitted insecurely
- **Supply chain** — released artifact differs from tagged source code,
  unsigned binaries served from official channels
- **Snapshot/rollback bypass** — apply phase mutates state without a
  preceding snapshot, even when profile requires one

## What is NOT a vulnerability (in our model)

- Ascendo running with sudo/UAC and modifying system files — that's its job
- Plugin executing operations declared in its manifest — also its job
- Adapter calling its target OS package manager — by design
- Dashboard listening on `127.0.0.1:8765` without auth — by default
  (token auth is opt-in; binding to non-localhost is explicitly NOT supported)

## Threat model

See `docs/security.md` for the full threat model (T1-T7) and mitigations.

## Coordinated disclosure

We follow standard responsible disclosure practice:

1. You report privately
2. We confirm + work on a fix
3. We coordinate with you on disclosure timeline (typically 30-90 days)
4. Patch released, advisory published
5. CVE assigned (via GitHub Security Advisories' MITRE integration)
6. Public credit to you in release notes (if you want)

We will not pursue legal action against good-faith security researchers
who follow this process.
