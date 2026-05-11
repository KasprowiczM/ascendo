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

# Copy a directory while excluding `worktrees/` subtrees. Used for the
# `.claude` copy so Claude Code agent worktrees (each potentially many
# MB of duplicate checkout) never land in the overlay. Operator-reported
# regression: the original `cp -a .claude` shipped ~29 MB of stale
# worktree copies in the overlay, slowing Ubuntu rclone imports to a
# crawl. See dev_sync_core.py HARD_EXCLUDE_PATTERNS for the runtime
# defence-in-depth — this script is the FIRST line of defence, the
# runtime filter is the second.
copy_dir_excluding_worktrees() {
    local src="$1" dst_dir="$2"
    if [ ! -d "$src" ]; then return 0; fi
    local name
    name="$(basename "$src")"
    printf '  copy: %s -> %s/  (excluding worktrees/)\n' "$src" "$dst_dir"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude 'worktrees/' "$src/" "$dst_dir/$name/"
    else
        # cp fallback when rsync isn't available — copy everything then
        # remove the bad subtree. Pre-existing dst gets refreshed by
        # removing it first so this stays idempotent like the original.
        rm -rf "$dst_dir/$name"
        cp -a "$src" "$dst_dir/"
        rm -rf "$dst_dir/$name/worktrees"
    fi
}

printf '== migrate AI instructions ==\n'
copy_if_exists "$REPO_ROOT/CLAUDE.md"  "$OVERLAY/ai-instructions/"
copy_if_exists "$REPO_ROOT/AGENTS.md"  "$OVERLAY/ai-instructions/"
copy_if_exists "$REPO_ROOT/CODEX.md"   "$OVERLAY/ai-instructions/"

printf '== migrate AI state ==\n'
copy_dir_excluding_worktrees "$REPO_ROOT/.claude" "$OVERLAY/ai-state"
copy_if_exists "$REPO_ROOT/.claudeignore"  "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codex"         "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codex.local"   "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.codexignore"   "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.gemini"        "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.geminiignore"  "$OVERLAY/ai-state/"
copy_if_exists "$REPO_ROOT/.graphifyignore" "$OVERLAY/ai-state/"

# Defence-in-depth: even if .claude got copied elsewhere by a future
# edit, scrub any worktrees/ subtrees from the overlay before export.
if find "$OVERLAY" -type d -name 'worktrees' -print -prune 2>/dev/null | grep -q .; then
    printf '== scrub stray worktrees/ from overlay ==\n'
    find "$OVERLAY" -type d -name 'worktrees' -print -exec rm -rf {} + 2>/dev/null || true
fi

printf '== migrate handoff docs ==\n'
copy_if_exists "$REPO_ROOT/HANDOFF.md"             "$OVERLAY/handoff/"
copy_if_exists "$REPO_ROOT/PLAN.md"                "$OVERLAY/handoff/"
copy_if_exists "$REPO_ROOT/DEV_SCRIPTS_README.md"  "$OVERLAY/handoff/"
# Idempotency fix (Sesja 57): `cp -a docs/superpowers/specs $OVERLAY/handoff/specs`
# WORKS on first run (creates `handoff/specs/`) but on re-runs, when
# `handoff/specs/` already exists, cp copies the SOURCE into the existing
# destination creating `handoff/specs/specs/`. Use rsync with the
# trailing-slash idiom that copies CONTENTS-of-src into dst regardless of
# whether dst exists. cp fallback removes the existing target first to
# avoid the nesting bug.
if [ -d "$REPO_ROOT/docs/superpowers/specs" ]; then
    printf '  copy: %s -> %s/  (idempotent — clears destination first)\n' \
        "$REPO_ROOT/docs/superpowers/specs" "$OVERLAY/handoff/specs"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$REPO_ROOT/docs/superpowers/specs/" "$OVERLAY/handoff/specs/"
    else
        rm -rf "$OVERLAY/handoff/specs"
        cp -a "$REPO_ROOT/docs/superpowers/specs" "$OVERLAY/handoff/specs"
    fi
fi

printf '== migrate graphify ==\n'
copy_if_exists "$REPO_ROOT/graphify-out" "$OVERLAY/graphify/"

printf '\n== overlay snapshot ==\n'
du -sh "$OVERLAY"/* 2>/dev/null || true

printf '\nNext steps:\n'
printf '  1. bash dev-sync-export.sh        # push overlay to Proton\n'
printf '  2. bash dev-sync-verify-full.sh   # confirm overlay reached Proton\n'
printf '  3. (manual) git rm <files>        # only AFTER step 2 succeeds\n'
