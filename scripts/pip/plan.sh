#!/usr/bin/env bash
# Same intel as check; pip plan == outdated list. Provided as separate phase
# for orchestrator symmetry. We run check.sh under-the-hood, then rewrite the
# emitted sidecar's `kind` from "check" → "plan" so the orchestrator's
# write_sidecar lands at <run-dir>/plan__pip.json (not check__pip.json,
# which would clobber the genuine check sidecar from earlier in the run).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

bash "${SCRIPT_DIR}/scripts/pip/check.sh" "$@"
rc=$?

if [[ -n "${JSON_OUT:-}" && -f "${JSON_OUT}" ]]; then
    python3 - "${JSON_OUT}" <<'PY_EOF'
import json, sys
p = sys.argv[1]
try:
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    if d.get("kind") == "check":
        d["kind"] = "plan"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.write("\n")
except Exception:
    pass
PY_EOF
fi

exit "$rc"
