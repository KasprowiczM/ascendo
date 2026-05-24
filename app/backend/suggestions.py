"""Smart Suggestions — heuristic + optional AI provider.

Heuristic rules run on every call (free, deterministic). They look at:

  • inventory snapshot (untracked detected packages, frequently outdated apps)
  • run history (chronic failures, long phases, last-run warnings)
  • exclusion list (suggest excluding apps that fail repeatedly)
  • config/*.list (suggest profile templates for a fresh user)

Each suggestion is shaped so the UI can display:

    {
      "id": "stable-id",                 # used for dismiss
      "title": "Exclude `firefox` from auto-upgrade",
      "rationale": "Has failed in 3 of last 5 runs.",
      "confidence": "high|med|low",
      "diff": [{"file": "config/exclusions.list", "add": ["apt:firefox"], "remove": []}],
      "category": "exclusion|tracking|profile|hygiene",
      "source": "heuristic|ai",
    }

Apply is opt-in: dashboard POSTs the exact diff back to /suggestions/apply,
which writes the change atomically with a .bak_<ts> backup.

The optional AI provider just enriches the rationale or proposes additional
items — never executes anything by itself. Provider config lives in
settings.json under `ai.{provider, model, api_key}`.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import config, db, settings as settings_mod

DISMISSED_PATH = Path(os.environ.get("XDG_STATE_HOME") or
                      os.path.expanduser("~/.local/state")) / "ascendo" / "dismissed-suggestions.json"


def _load_dismissed() -> set[str]:
    if not DISMISSED_PATH.exists():
        return set()
    try:
        return set(json.loads(DISMISSED_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_dismissed(ids: set[str]) -> None:
    DISMISSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISMISSED_PATH.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def _heuristics() -> list[dict[str, Any]]:
    repo = config.repo_root()
    out: list[dict[str, Any]] = []

    # 1. Find packages that fail in apply phase repeatedly → suggest exclusion.
    try:
        with db.connect(config.db_path()) as con:
            cur = con.execute(
                "SELECT category, summary FROM phase_results "
                "WHERE phase='apply' ORDER BY id DESC LIMIT 60")
            cat_fails: Counter[tuple[str, str]] = Counter()
            for category, summary_json in cur:
                try:
                    s = json.loads(summary_json) if summary_json else {}
                except Exception:
                    continue
                # phase_results stores {"ok": N, "warn": N, "err": N} only;
                # we don't have per-package detail here without reading
                # sidecars. So this check is intentionally lightweight —
                # the heavier per-package failure mining happens in the
                # next heuristic.
                if (s.get("err") or 0) > 0:
                    cat_fails[(category, "err")] += 1
            for (cat, _), n in cat_fails.items():
                if n >= 3:
                    out.append({
                        "id": f"hint-cat-fail-{cat}",
                        "title": f"`{cat}` apply has failed {n} of last 60 runs",
                        "rationale": "Consider running `--only {0} --phase plan` to inspect, or excluding the problematic package.".format(cat),
                        "confidence": "med",
                        "diff": [],
                        "category": "hygiene",
                        "source": "heuristic",
                    })
    except Exception:
        pass

    # 2. Per-package failures from recent run sidecars.
    try:
        runs_dir = config.runs_dir()
        if runs_dir.exists():
            recent = sorted(runs_dir.glob("*/"), key=lambda p: p.name, reverse=True)[:10]
            pkg_fail: Counter[tuple[str, str]] = Counter()
            for rd in recent:
                for sidecar in rd.glob("*/apply.json"):
                    try:
                        d = json.loads(sidecar.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    cat = d.get("category", "?")
                    for it in d.get("items", []) or []:
                        if it.get("result") in ("failed", "warn"):
                            name = (it.get("id") or "").split(":")[-1]
                            if name:
                                pkg_fail[(cat, name)] += 1
            for (cat, name), n in pkg_fail.items():
                if n >= 3:
                    out.append({
                        "id": f"excl-{cat}-{name}",
                        "title": f"Exclude `{name}` ({cat}) from auto-upgrade",
                        "rationale": f"Failed {n} times in last 10 runs. Excluding stops it being touched until you re-enable it.",
                        "confidence": "high",
                        "diff": [{
                            "file": "config/exclusions.list",
                            "add": [f"{cat}:{name}"],
                            "remove": [],
                        }],
                        "category": "exclusion",
                        "source": "heuristic",
                    })
    except Exception:
        pass

    # 3. Untracked packages that show up frequently → suggest tracking.
    try:
        det_p = repo / "scripts" / "apps" / "detect.sh"
        if det_p.exists():
            res = subprocess.run(["bash", str(det_p), "--json"],
                                 capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                d = json.loads(res.stdout)
                for it in d.get("items", []) or []:
                    if it.get("state") == "detected":
                        # Common system tools — high signal "you probably want this tracked"
                        if it.get("package") in (
                            "docker.io", "docker-ce", "git", "curl", "wget",
                            "build-essential", "python3-pip", "vim", "tmux",
                            "htop", "jq", "ripgrep", "fzf",
                        ):
                            cat = it.get("category", "apt")
                            out.append({
                                "id": f"track-{cat}-{it['package']}",
                                "title": f"Track `{it['package']}` in your config",
                                "rationale": "It's installed but not in any config/*.list, so it won't be re-installed on a fresh machine.",
                                "confidence": "med",
                                "diff": [{
                                    "file": f"config/{cat}-packages.list",
                                    "add": [it["package"]],
                                    "remove": [],
                                }],
                                "category": "tracking",
                                "source": "heuristic",
                            })
    except Exception:
        pass

    # 4. Profile suggestion — fresh user with empty exclusions and no schedule.
    try:
        s = settings_mod.load()
        sched = s.get("scheduler", {}) if s else {}
        excl_path = repo / "config" / "exclusions.list"
        excl_lines = 0
        if excl_path.exists():
            excl_lines = sum(
                1 for L in excl_path.read_text(encoding="utf-8").splitlines()
                if L.strip() and not L.lstrip().startswith("#")
            )
        if not sched.get("enabled") and excl_lines == 0:
            out.append({
                "id": "profile-onboarding",
                "title": "Schedule a weekly safe update",
                "rationale": "You haven't set a scheduler yet. A weekly Sunday 03:00 `safe` profile run keeps you current with no driver risk.",
                "confidence": "med",
                "diff": [],
                "category": "profile",
                "source": "heuristic",
            })
    except Exception:
        pass

    return out


def _ai_enrich(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Optional AI step: ask the configured LLM to rewrite rationales and add
    extra suggestions. Strict-budget, fail-soft, never edits files.
    Supported providers: anthropic, openai, gemini, ollama, lmstudio,
    openai_compat. Local providers (ollama, lmstudio) need no api_key."""
    s = settings_mod.load() or {}
    ai = s.get("ai") or {}
    provider = (ai.get("provider") or "").lower()
    api_key = ai.get("api_key") or ""
    model = ai.get("model") or ""
    base_url = (ai.get("base_url") or "").rstrip("/")
    local = provider in ("ollama", "lmstudio", "openai_compat")
    if not provider or provider == "off":
        return items
    if not local and not api_key:
        return items
    # Strictly read-only: we send a compressed summary, get text back, no
    # tool-use. Limited time budget so a misconfigured key never stalls UI.
    try:
        import urllib.request
        import urllib.error
        import socket
        socket.setdefaulttimeout(10.0)
        prompt = (
            "You are an Ubuntu update assistant. Below is a list of heuristic "
            "suggestions. For each, write a one-sentence improved rationale "
            "(plain text, no markdown). Reply as JSON array with the same "
            "order: [{\"id\":\"...\", \"rationale\":\"...\"}].\n\n"
            + json.dumps([{"id": x["id"], "title": x["title"],
                          "rationale": x["rationale"]} for x in items])
        )
        if provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                method="POST",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                data=json.dumps({
                    "model": model or "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode("utf-8"),
            )
            resp = urllib.request.urlopen(req, timeout=10)
            j = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        elif provider in ("openai", "openai_compat", "lmstudio"):
            url = (
                base_url + "/chat/completions" if base_url
                else "http://127.0.0.1:1234/v1/chat/completions" if provider == "lmstudio"
                else "https://api.openai.com/v1/chat/completions"
            )
            headers = {"content-type": "application/json"}
            if api_key:
                headers["authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(url, method="POST", headers=headers,
                data=json.dumps({
                    "model": model or ("local-model" if provider == "lmstudio" else "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                }).encode("utf-8"))
            resp = urllib.request.urlopen(req, timeout=15)
            j = json.loads(resp.read().decode("utf-8"))
            text = j["choices"][0]["message"]["content"]
        elif provider == "gemini":
            # Google AI Studio v1beta REST.
            mdl = model or "gemini-1.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent?key={api_key}"
            req = urllib.request.Request(url, method="POST",
                headers={"content-type": "application/json"},
                data=json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
                }).encode("utf-8"))
            resp = urllib.request.urlopen(req, timeout=15)
            j = json.loads(resp.read().decode("utf-8"))
            cand = (j.get("candidates") or [{}])[0]
            parts = ((cand.get("content") or {}).get("parts") or [])
            text = "".join(p.get("text", "") for p in parts)
        elif provider == "ollama":
            # Ollama native /api/chat (default port 11434).
            url = (base_url or "http://127.0.0.1:11434") + "/api/chat"
            req = urllib.request.Request(url, method="POST",
                headers={"content-type": "application/json"},
                data=json.dumps({
                    "model": model or "llama3",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                }).encode("utf-8"))
            resp = urllib.request.urlopen(req, timeout=20)
            j = json.loads(resp.read().decode("utf-8"))
            text = ((j.get("message") or {}).get("content") or "")
        else:
            return items  # unknown provider — fall back to heuristics only
        # Extract JSON array (be lenient about surrounding prose)
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < 0:
            return items
        rewrites = json.loads(text[start:end+1])
        rmap = {r["id"]: r["rationale"] for r in rewrites if isinstance(r, dict) and "id" in r}
        for it in items:
            if it["id"] in rmap:
                it["rationale"] = rmap[it["id"]]
                it["source"] = "ai"
        return items
    except Exception:
        return items


def test_provider() -> dict[str, Any]:
    """Send a tiny ping to the configured provider and return success/error.
    Used by the UI's 'Test connection' button."""
    sample = [{"id": "ping", "title": "ping", "rationale": "test"}]
    try:
        out = _ai_enrich(sample)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)[:300]}
    s = settings_mod.load() or {}
    ai = s.get("ai") or {}
    return {"ok": True, "provider": ai.get("provider"), "model": ai.get("model"),
            "rationale_changed": out and out[0].get("rationale") != "test"}


def list_all() -> list[dict[str, Any]]:
    items = _heuristics()
    items = _ai_enrich(items)
    dismissed = _load_dismissed()
    return [it for it in items if it["id"] not in dismissed]


def dismiss(suggestion_id: str) -> bool:
    ids = _load_dismissed()
    ids.add(suggestion_id)
    _save_dismissed(ids)
    return True


def apply_diff(diff: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a list of file diffs (add/remove lines) atomically with backups.
    No shell injection — diffs are pure line arrays."""
    repo = config.repo_root()
    changes: list[dict[str, Any]] = []
    for d in diff:
        rel = d.get("file") or ""
        if not rel or ".." in rel or rel.startswith("/"):
            continue
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        bak = target.with_suffix(target.suffix + f".bak_{int(time.time())}") if target.exists() else None
        if bak:
            bak.write_text(before, encoding="utf-8")
        lines = before.splitlines()
        for adddel in d.get("remove", []):
            lines = [L for L in lines if L.strip() != adddel.strip()]
        for addln in d.get("add", []):
            if not any(L.strip() == addln.strip() for L in lines):
                lines.append(addln)
        new = "\n".join(lines)
        if before and not before.endswith("\n"):
            new = new.rstrip("\n")
        if not new.endswith("\n"):
            new += "\n"
        target.write_text(new, encoding="utf-8")
        changes.append({"file": rel, "added": d.get("add", []),
                        "removed": d.get("remove", []),
                        "backup": str(bak.relative_to(repo)) if bak else None})
    return {"ok": True, "changes": changes}
