"""Cross-OS scheduler facade.

Per-OS implementations:
- systemd.SystemdScheduler — Linux (systemd timers)
- launchd.LaunchdScheduler — macOS (launchd plists)
- task_scheduler.TaskSchedulerScheduler — Windows (schtasks.exe)

All implement IScheduler. Cron expression parsing is in parser.py
(translates "0 3 * * 0" → per-OS schedule format).

The scheduler is OS-agnostic from core's perspective — adapter_factory
returns the correct implementation per detected OS.
"""
