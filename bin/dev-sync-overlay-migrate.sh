#!/usr/bin/env bash
# dev-sync-overlay-migrate.sh — copy private files to dev-sync-overlay/
#
# Run from the repo root. After this completes successfully:
#   1. Run dev-sync-export.sh to push the overlay to Proton.
#   2. Verify with dev-sync-verify-full.sh.
#   3. ONLY THEN run `git rm` on the originals (a separate manual step).
#
# This script is idempotent — re-running just refreshes the overlay copies.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$REPO_ROOT/dev-sync-overlay"

mkdir -p "$OVERLAY"/{ai-instructions,ai-state,handoff,graphify}

copy_if_exists() {
    local src="$1" dst="$2"
    if [ -e "$src" ]; then
        printf '  copy: %s -> %s\n' "$src" "$dst"
        cp -a "$src" "$dst"
    fi
}

printf '== migrate AI instructions ==\n'
copy_if_exists "$REPO_ROOT/CLAUDE.md"  "$OVERLAY/ai-instructions/"
copy_if_exists "$REPO_ROOT/AGENTS.md"  "$OVERLAY/ai-instructions/"
copy_if_exists "$REPO_ROOT/CODEX.md"   "$OVERLAY/ai-instructions/"

printf '== migrate AI state ==\n'
copy_if_exists "$REPO_ROOT/.claude"        "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.claudeignore"  "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codex"         "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codex.local"   "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codexignore"   "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.gemini"        "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.geminiignore"  "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.graphifyignore" "$OVERLAY/ai-state/"

printf '== migrate handoff docs ==\n'
copy_if_exists "$REPO_ROOT/HANDOFF.md"             "$OVERLAY/handoff/"
copy_if_exists "$REPO_ROOT/PLAN.md"                "$OVERLAY/handoff/"
copy_if_exists "$REPO_ROOT/DEV_SCRIPTS_README.md"  "$OVERLAY/handoff/"
copy_if_exists "$REPO_ROOT/docs/superpowers/specs" "$OVERLAY/handoff/specs"

printf '== migrate graphify ==\n'
copy_if_exists "$REPO_ROOT/graphify-out" "$OVERLAY/graphify/"

printf '\n== overlay snapshot ==\n'
du -sh "$OVERLAY"/* 2>/dev/null || true

printf '\nNext steps:\n'
printf '  1. bash dev-sync-export.sh        # push overlay to Proton\n'
printf '  2. bash dev-sync-verify-full.sh   # confirm overlay reached Proton\n'
printf '  3. (manual) git rm <files>        # only AFTER step 2 succeeds\n'
