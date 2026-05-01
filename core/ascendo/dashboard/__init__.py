"""FastAPI HTTP backend (Layer 3) — exposes core domain to SPA + agents.

The dashboard is the network face of Ascendo. It calls Layer 4
(:mod:`core.ascendo.orchestrator`) the same way the CLI does and serves
the result as JSON over HTTP. It does NOT import from any
``adapters/<os>/*`` package — the architectural firewall (ADR-0005) holds.

MVP endpoints (this milestone, M2.7):

- ``GET  /health``         — reports adapter `health_check()`.
- ``GET  /version``        — Ascendo version + adapter info.
- ``POST /runs``           — execute a synchronous run via :func:`run_phases`.
- ``GET  /runs``           — list run-ids that exist on disk under runs_dir.
- ``GET  /runs/{run_id}``  — return that run's sidecars (parsed).

Future endpoints (deferred to follow-up sessions):
- ``GET  /runs/{run_id}/stream`` — SSE for live phase logs (M3+ orchestrator
  rework needed to surface in-flight events).
- ``POST /schedule``, ``GET /schedule`` — Task Scheduler / systemd / launchd
  CRUD (M3.13).
- ``GET  /metrics`` — Prometheus-style exposition.
- ``POST /auth/token`` — opt-in HttpOnly cookie auth (M6).
"""
from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
