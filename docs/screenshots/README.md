# Screenshots

Marketing / README screenshots of the Ascendo dashboard live here.
They're operator-captured (not generated in CI) so they always show
real data on a real machine.

## How to refresh

1. Start the dashboard: `ascendo web start` (or `python -m ascendo dashboard`).
2. Open `http://127.0.0.1:8765/` and capture each destination **in both
   themes** (toggle via the header moon/sun, or Settings → Appearance):

   | File | View | Notes |
   |------|------|-------|
   | `dashboard-dark.png` / `dashboard-light.png` | Dashboard | the answer-first verdict + attention list |
   | `runs-monitor.png` | Runs → Active | a live run (start a Quick check first) |
   | `library-sources.png` | Library → Sources | "Updates available" vs "Up to date" |
   | `assistant.png` | Library → Assistant | the modern chat (send one prompt) |
   | `insights.png` | Insights | KPI strip + duration trend |
   | `history.png` | Runs → History | KPI strip + per-run tags |

3. Crop to the content area (hide OS chrome), keep them ≤ 1600px wide,
   and optimize (`pngquant` / `oxipng`) so the repo stays light.
4. Reference them from `README.md` once added.

> Tip: a short GIF of *verdict → Safe update → live monitor → completion*
> is worth more than any single frame for a landing page.
