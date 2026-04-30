"""Post-run reports + Prometheus metrics + risk scoring.

- analyzer.py — analyzes JSON sidecars from a run, finds anomalies
- markdown_report.py — generates `runs/<id>/report.md` for human review
- prometheus_metrics.py — opt-in /metrics endpoint (counters per category)

Audit runs after every `ascendo run`, regardless of CLI vs dashboard trigger.
"""
