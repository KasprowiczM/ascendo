"""FastAPI HTTP backend — exposes core domain to SPA frontend + CLI.

- app.py — FastAPI() instance, startup/shutdown hooks
- routes/ — /health, /runs, /packages, /schedule, /plugins, /metrics
- auth.py — opt-in token middleware (HttpOnly cookie)
- sse.py — Server-Sent Events for live log streaming
- frontend_static/ — SPA assets served at /

The dashboard is in Layer 3 (Backend HTTP). It depends on Layer 4 (core
domain) but never on Layer 5 (adapters) directly.
"""
