#!/usr/bin/env bash
# Real-LLM AI-chat smoke test.
#
# Exercises the full /ai/chat HTTP surface against a RUNNING dashboard:
#   GET  /ai/chat/backends      — which CLI/API backends resolve
#   GET  /ai/chat/library       — starter prompt library (EN+PL)
#   POST /ai/chat/conversations — create a conversation
#   POST /ai/chat               — post a turn (202 + turn_id)
#   GET  /ai/chat/stream/{id}   — read the SSE reply stream
#
# This is the surface the validate-*.sh Stage 14 covers structurally;
# this script additionally drives a real turn so, on a box with an
# installed + authenticated CLI (claude / gemini / codex / opencode)
# OR a configured API key, you see actual model tokens stream back.
# With NO backend it still PASSES the surface (backends=[], the turn
# stream emits a terminal error chunk) — that's a valid HTTP check;
# only the "model actually answered" line is skipped.
#
# Usage:
#   scripts/smoke-ai-chat.sh [BASE_URL]
#   BASE_URL default http://127.0.0.1:8765   (start: ascendo web start)
#
# Exit 0 = HTTP surface healthy. Exit 1 = a surface call failed.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8765}"
J() { python3 -c 'import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))' "$1"; }
say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || fail "curl not on PATH"
command -v python3 >/dev/null || fail "python3 not on PATH"

say "==> dashboard reachable @ $BASE"
curl -fsS -o /dev/null "$BASE/version" || fail "no dashboard at $BASE (run: ascendo web start)"

say "==> GET /ai/chat/backends"
BK=$(curl -fsS "$BASE/ai/chat/backends") || fail "backends endpoint"
N_BK=$(printf '%s' "$BK" | J 'len(d.get("backends", d) if isinstance(d,dict) else d)' 2>/dev/null || echo "?")
say "    backends payload OK (count=$N_BK)"

say "==> GET /ai/chat/library"
LIB=$(curl -fsS "$BASE/ai/chat/library") || fail "library endpoint"
N_LIB=$(printf '%s' "$LIB" | J 'len(d.get("prompts", d.get("library", d)))' 2>/dev/null || echo "?")
say "    prompt library OK (entries=$N_LIB)"

say "==> POST /ai/chat/conversations"
CID=$(curl -fsS -X POST "$BASE/ai/chat/conversations" \
        -H 'content-type: application/json' -d '{"title":"smoke"}' \
      | J 'd["id"]') || fail "create conversation"
say "    conversation id=$CID"

say "==> POST /ai/chat (turn)"
TURN=$(curl -fsS -X POST "$BASE/ai/chat" -H 'content-type: application/json' \
        -d "{\"conversation_id\":\"$CID\",\"message\":\"Reply with the single word: pong\",\"locale\":\"en\"}" \
      | J 'd["turn_id"]') || fail "post chat turn"
say "    turn id=$TURN"

say "==> GET /ai/chat/stream/$TURN (10s window)"
STREAM=$(curl -fsS --max-time 12 -N "$BASE/ai/chat/stream/$TURN" 2>/dev/null || true)
if printf '%s' "$STREAM" | grep -q 'event:'; then
  say "    SSE stream emitted events OK"
  if printf '%s' "$STREAM" | grep -qiE 'event: *(token|delta|message)'; then
    say "    >>> model produced tokens (a backend is live + authed)"
  elif printf '%s' "$STREAM" | grep -qi 'error'; then
    say "    >>> stream returned an error chunk — expected when NO"
    say "        backend/CLI/API key is configured. Surface is healthy;"
    say "        configure a backend to see real model output."
  fi
else
  fail "SSE stream produced no events"
fi

say ""
say "AI-CHAT SMOKE: PASS (HTTP surface healthy)"
