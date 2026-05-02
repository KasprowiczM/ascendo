# CLI UI Kit

The CLI is the canonical surface — every other UI is a wrapper. Color usage is restrained: structural keywords, status badges, and the brand lime for the primary action only. Defaults to no color when piping to a non-tty (`NO_COLOR` respected).

Output shapes:
- **Banner** on every run: brand · version · profile · host
- **Phase header** for each of the 5 phases
- **Per-line stream** with timestamp, level, message, and category prefix
- **Final summary** card with pass/fail counts and sidecar path
