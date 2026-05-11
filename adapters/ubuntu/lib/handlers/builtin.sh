# adapters/ubuntu/lib/handlers/builtin.sh
# Tier-B builtin handler — no remote probe, no apply mutation.
# Used for apps that auto-update themselves (e.g. Discord) where Ascendo's
# job is just to surface the registry entry and emit a 'triggered' status.

builtin_check() {
    # Always returns empty (no candidate) — caller emits skipped + info msg.
    echo ""
    return 0
}

builtin_apply() {
    # Trigger-only — emit success without doing anything.
    return 0
}
