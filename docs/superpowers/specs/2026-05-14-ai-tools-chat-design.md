# AI Tools — Multi-turn chat with CLI-first backends — Design

> Date: 2026-05-14
> Milestone: post-Sesja 69; targets a follow-up milestone after v0.4.5
> Target tag: `v0.5.0` after the new `aitools` validate stages pass on Mac.r12.home / DP5520WMK / mk-uP5520
> Spec lives forward; HANDOFF.md will close out the milestone after implementation.
> Replaces and extends the one-shot AI suggestions surface shipped in Sesja 67.

## 1. Goals and non-goals

### Goals

- Replace the one-shot `Suggestions` tab with a richer `AI Tools` tab that
  combines the existing rule-based + AI-augmented suggestion cards
  (kept verbatim from Sesja 67) with a new multi-turn chat surface.
- Use the user's existing subscription credentials by shelling out to
  already-logged-in CLI tools: `claude` (Claude Code OAuth → Pro/Max),
  `gemini` (gemini-cli Google OAuth), `codex` (OpenAI Codex CLI), and
  `opencode` (open-source, multi-provider, supports local models). Zero
  credential handling on Ascendo's side — each CLI inherits its own auth.
- Keep the existing 6-provider API-key path from Sesja 67 as a fallback
  for users without a CLI installed.
- Ground every chat turn in real Ascendo state: doctor rollup, recent
  run sidecars, inventory contents, update history. The LLM should
  answer "why did this fail?" with the actual sidecar in context, not
  generic LLM hallucinations.
- Let the LLM propose actions ("Run a winget check", "Add this app to
  the skip list") that render as one-click chip buttons in the chat
  reply. User stays in control: every action is an explicit click,
  every mutating action goes through the existing confirm gate.
- Persist conversations locally (per-host SQLite at `~/.ascendo/chats.db`)
  so users can resume diagnoses across dashboard restarts.
- Maintain Ascendo's EN+PL i18n parity from day one. Every new UI
  string ships in both locales; the LLM responds in the user's UI
  locale via system-prompt injection.

### Non-goals

- **Reverse-engineering vendor OAuth** to use claude.ai / ChatGPT Plus /
  Gemini Advanced subscriptions directly from Ascendo. Not viable
  technically, violates ToS, fragile. Already ruled out in
  brainstorming.
- **Full tool-use / function-calling protocol** for v1. LLM proposes
  actions via a structured code-fence convention parsed by the SPA;
  no Anthropic-tools-API / OpenAI-function-calling integration in
  the first cut. Re-evaluate in v2 once usage patterns are observed.
- **Cross-host conversation sync.** Each machine has its own
  `chats.db`. If a user wants to see the same conversation on two
  machines, they manually copy the file. Sync is out of scope for
  v1.
- **Auto-redaction of injected context.** Hostnames, paths, app
  versions are sent to the LLM as-is. The data is no more sensitive
  than what's already in `~/.ascendo/` files the user can read freely.
  Auto-redaction is a future privacy ADR if a user pushes back.
- **Encrypted-at-rest `chats.db`** for v1. Plain SQLite, `0600` file
  perms, dev-sync HARD_EXCLUDE. Re-evaluate if a user requests
  it.
- **Mobile-native UX.** The dashboard's existing responsive
  narrow-viewport behavior covers tablets and phones at the level
  users currently access Ascendo from. No PWA, no native wrapper.
- **Remote telemetry.** Nothing about chat usage leaves the machine
  beyond what the user's chosen CLI / API normally transmits.
- **Inline tool execution by the LLM itself.** The LLM never executes
  anything directly — it only proposes actions the user clicks. This
  is the security boundary; not negotiable for v1.

## 2. Architecture

```
adapters/{macos,ubuntu,windows}/         (unchanged — chat is OS-agnostic)

core/ascendo/
├── ai/                                  NEW package
│   ├── __init__.py
│   ├── backend.py                       Backend ABC + 5 drivers + resolver   ~450 LOC
│   ├── drivers/
│   │   ├── claude_code.py               ClaudeCodeBackend                    ~90 LOC
│   │   ├── gemini_cli.py                GeminiCliBackend                     ~80 LOC
│   │   ├── codex_cli.py                 CodexCliBackend                      ~80 LOC
│   │   ├── opencode.py                  OpencodeBackend                      ~80 LOC
│   │   └── api_key.py                   ApiKeyBackend (wraps Sesja 67)       ~60 LOC
│   ├── context.py                       build_context() + resolver registry  ~350 LOC
│   ├── resolvers/                       one file per context tag             10x ~30
│   │   ├── doctor.py
│   │   ├── latest_failed_sidecar.py
│   │   ├── outdated_apps.py
│   │   └── ...
│   ├── actions.py                       parse_fences() + ALLOWED_ACTIONS     ~250 LOC
│   ├── persistence.py                   ChatsDB (SQLite v1)                  ~280 LOC
│   ├── prompts.py                       system prompt + library loader       ~120 LOC
│   ├── prompts/
│   │   └── library.toml                 prompt-library entries (EN+PL)       ~300 LOC
│   └── streaming.py                     turn registry + SSE producer         ~180 LOC
└── dashboard/routes/
    └── chat.py                          NEW — /ai/chat/* endpoints           ~250 LOC

app/frontend/
├── app.js                               +aitools namespace                   ~600 LOC delta
├── index.html                           #view-aitools section                 ~200 LOC delta
├── i18n.js                              aitools.* keys × EN + PL              ~80 keys × 2
└── style.css                            chat thread + chip styling            ~120 LOC delta

tests/contract/
├── test_ai_backend_resolver.py
├── test_ai_cli_drivers.py
├── test_ai_context_injector.py
├── test_ai_actions_parser.py
├── test_ai_actions_dispatcher.py
├── test_ai_persistence.py
├── test_ai_chat_endpoints.py
├── test_ai_chat_sse_disconnect.py
└── test_aitools_view.py

tests/fixtures/ai_cli/                   fake-claude / fake-gemini / fake-codex / fake-opencode
└── ...                                  shell scripts that mimic each CLI's output format

bin/
└── validate-{macos,windows,ubuntu}      +Stage 14 (aitools, 8 sub-steps each)
```

**One chat turn, traced through the 6-layer architecture:**

```
Layer 1: SPA chat view
  user types question → POST /ai/chat
       └─ SSE stream → token-by-token render → action chips inline
Layer 3: dashboard / FastAPI (routes/chat.py)
  /ai/chat                         start turn, returns turn_id + SSE url
  /ai/chat/stream/{turn_id}         SSE: tokens, action_proposal, done
  /ai/chat/cancel/{turn_id}         abort
  /ai/chat/action                   execute one action proposal (whitelist gate)
  /ai/chat/conversations            CRUD on saved conversations
  /ai/chat/library                  list prompt-library entries (locale-resolved)
  /ai/chat/backends                 backend availability + auth status
Layer 4: core (core/ascendo/ai/)
  backend.py                        resolve CLI-first → API-key fallback
  context.py                        smart auto-inject (base + per-template extras)
  actions.py                        fence parser + ALLOWED_ACTIONS dispatcher
  persistence.py                    SQLite at ~/.ascendo/chats.db
  prompts.py                        system prompt + prompts/library.toml
  streaming.py                      TurnRegistry (per-process), SSE producer
Layer 5: adapters                   unchanged
Layer 6: subprocess (when CLI path wins)
  claude   -p "<prompt>" --output-format stream-json
  gemini   -p "<prompt>"
  codex    exec --stream "<prompt>"
  opencode run "<prompt>"           (exact flags TBD per impl spike, Task 1)
```

**Reuse from existing code:**

- `routes/ai.py::call_provider_inference()` — unchanged; becomes the
  `ApiKeyBackend` path. The 6-provider switch (anthropic / openai /
  openrouter / ollama / google / lm_studio) stays exactly as Sesja 67
  shipped it.
- `routes/suggestions.py` — unchanged; its `/suggestions/library`
  endpoint continues to feed the "Quick suggestions" rail at the top
  of the new AI Tools tab.
- `InventoryDB`, `runs.py`, `health.py`, `IScheduler` — read by
  context resolvers. No schema changes to existing tables.
- M2.10 `RunRegistry` pattern — mirrored as `TurnRegistry` for chat
  turns (per-process, bounded, evicts completed first).
- M2.10 SSE infrastructure — same pattern; the only new event types
  are `token`, `action_proposal`, `context_trimmed`.

## 3. Backend module

`core/ascendo/ai/backend.py` defines the `Backend` ABC and a resolver.
Each backend is one driver class under `core/ascendo/ai/drivers/`.

### 3.1 Backend ABC

```python
class Chunk(BaseModel):
    type: Literal["token", "action_proposal", "context_trimmed", "done", "error"]
    content: str | None = None              # token text
    action: dict | None = None              # parsed action JSON
    status: Literal["success", "cancelled", "error"] | None = None
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None

class Backend(ABC):
    name: str                               # "claude" | "gemini" | "codex" | "opencode" | "api:<provider>"
    bin_name: str | None                    # CLI binary name; None for API backends
    min_version: str | None                 # pinned minimum CLI version

    @abstractmethod
    def is_available(self) -> bool: ...     # shutil.which + version probe
    @abstractmethod
    def is_authenticated(self) -> bool: ... # 3s probe call
    @abstractmethod
    async def stream(
        self, *, system: str, messages: list[Message], cancel_event: asyncio.Event
    ) -> AsyncIterator[Chunk]: ...
    @abstractmethod
    def model_info(self) -> dict: ...       # backend-specific model name + provider
```

### 3.2 Resolver order

Returns the first match:

1. User's saved preference in `~/.config/ascendo/ai.json` →
   `chat_backend` field. Explicit override; never auto-changed.
2. Most recently authenticated CLI (cached probe results in
   `~/.ascendo/ai_probes.json`; refresh every 24h or on
   `POST /ai/chat/backends/refresh`).
3. First installed CLI in fixed order: `claude` → `gemini` → `codex`
   → `opencode`. Reasoning: vendor CLIs first (user wants their
   subscription used); opencode last because it's
   provider-agnostic and the model is whatever the user configured.
4. `ApiKeyBackend` — the Sesja 67 6-provider path, if any provider has
   a key configured.
5. None available → `/ai/chat/backends` returns `{"available": []}`.
   SPA renders an onboarding card with install links.

### 3.3 Per-driver invocation matrix

| CLI | Stream invocation | Output shape | Notes |
|---|---|---|---|
| `claude` | `claude -p "$prompt" --output-format stream-json` | JSONL events: `message_start`, `content_block_delta`, `message_stop` | Auth via `~/.claude/credentials.json` (OAuth). Min version TBD per Task 1 spike. |
| `gemini` | `gemini -p "$prompt"` (streaming via flag) | text chunks on stdout | Auth via Google OAuth. Exact streaming flag + min version TBD per Task 1 spike. Falls back to one-shot if streaming misbehaves. |
| `codex` | `codex exec "$prompt"` (streaming variant TBD) | JSONL events expected | Auth via ChatGPT login OR API key. Exact flags + min version TBD per Task 1 spike. |
| `opencode` | `opencode run "$prompt"` (subject to verification) | text/JSONL (per-config) | Auth inherited from `~/.config/opencode/`. Exact non-interactive invocation + streaming flags + min version TBD per Task 1 spike. |
| `api:*` | direct HTTPS via existing `call_provider_inference()` | text chunks via vendor SDK streaming or simulated chunking from one-shot | No subprocess. |

**Critical impl note**: each driver's exact CLI flags MUST be validated
against the installed binary version at startup. Mismatch → driver
reports `is_available=False` and the resolver moves on. A
`MIN_VERSIONS` constant in `backend.py` pins the floor for each CLI;
versions below it surface as `available=false, reason=outdated`.

### 3.4 Multi-turn state

CLIs are stateless per invocation. We maintain conversation state
Python-side and re-pass the full transcript on each turn as a single
joined prompt:

```
<system>
{system_prompt}

<context>
{context_blob from build_context()}
</context>

<conversation_history>
USER: {msg1}
ASSISTANT: {reply1}
USER: {msg2}
ASSISTANT: {reply2}
</conversation_history>

USER: {current_message}
```

**Token budget guard** (per-component caps, cheap `len(text) / 4`
heuristic):

- System prompt: ~1k, never trimmed.
- Context blob (Section 4): ≤4k, enforced inside `build_context()`.
- Conversation transcript (older user+assistant pairs): ≤8k,
  enforced before invoking the backend; oldest pairs dropped first.
- Current user message: unbounded but practically <2k.
- Total input ceiling: ~15k tokens, leaving headroom for ~4k+ of
  response on backends with 32k+ windows. Smaller backends (e.g.
  local Ollama at 8k) need their own cap — driver declares
  `max_input_tokens` and the budget shrinks proportionally.

### 3.5 Cancellation

Each in-flight stream has a `turn_id` mapped to its `asyncio.Task` +
subprocess in a process-wide `TurnRegistry` (mirror of M2.10's
`RunRegistry`). On cancel:

1. `task.cancel()` raises `CancelledError` inside the streaming
   coroutine.
2. Stream coroutine's `finally:` block calls `process.terminate()`
   (SIGTERM).
3. After 2 seconds grace, if process is still alive, `process.kill()`
   (SIGKILL on POSIX, TerminateProcess on Windows).
4. Final SSE chunk emitted: `{type: "done", status: "cancelled"}`.
5. Partial content persisted to `chats.db` with a trailing
   `(cancelled)` marker.

Closing the SSE EventSource client-side also cancels (server detects
via `await request.is_disconnected()` polled between chunks).

## 4. Context injector

`core/ascendo/ai/context.py` — single entry point:

```python
def build_context(
    *,
    message: str,
    template_id: str | None,
    locale: Literal["en", "pl"],
    adapter: IAdapter,
    runs_dir: Path,
    inventory_db: InventoryDB,
    chats_db: ChatsDB,
    conversation_id: str | None,
) -> str:
    """Returns a markdown context blob ready to prepend to the system prompt."""
```

### 4.1 Always-on base context

~500 tokens, prepended to every turn:

- Locale + UI language hint ("respond in Polish" / "respond in
  English").
- OS + adapter name + tier.
- `doctor` rollup: e.g. `12/12 ok` or a list of degraded components.
- Last run summary: id, ended_at relative ("12 min ago"), overall
  status, item counts per category.
- Inventory totals per category: `brew:152 mas:13 npm:9 ...`.

### 4.2 Per-template extras

Each prompt-library entry declares what extra context it needs:

```toml
[[entries]]
id = "diagnose_last_run"
title.en = "Why did my last run fail?"
title.pl = "Dlaczego ostatni uruchom się nie powiódł?"
group = "diagnostics"
platforms = ["macos", "windows", "ubuntu"]  # default: all; omit field for universal
starter_prompt.en = "Analyze my most recent failed run and explain what went wrong."
starter_prompt.pl = "Przeanalizuj mój ostatni nieudany uruchom i wyjaśnij, co poszło nie tak."
context_tags = ["latest_failed_sidecar", "latest_report_md"]
suggested_followups = ["explain_exit_code", "retry_with_flags"]

[[entries]]
id = "enable_touch_id_sudo"
title.en = "How do I enable Touch ID for sudo?"
title.pl = "Jak włączyć Touch ID dla sudo?"
group = "setup"
platforms = ["macos"]                        # macOS-only — hidden on Windows + Linux
starter_prompt.en = "Walk me through enabling Touch ID for sudo on this Mac."
context_tags = ["adapter_capabilities"]
```

The `platforms` field is an allow-list. Omitted = universal (shown
everywhere). `/ai/chat/library` filters entries against the current
adapter's OS before returning. SPA never sees inapplicable cards —
no client-side gating needed (mirrors the `data-platforms` pattern
used in the Help tab since Sesja 69).

### 4.3 Context tag registry

Each tag has a resolver function in `core/ascendo/ai/resolvers/<tag>.py`.

| Tag | Resolver returns | Source | Approx tokens |
|---|---|---|---|
| `doctor_full` | All component statuses with messages | `adapter.health_check()` | 200 |
| `latest_failed_sidecar` | Most recent sidecar with `status=failed` | walk `runs_dir/` | 800 |
| `latest_report_md` | Last `REPORT.md`, truncated to 2k chars | `runs_dir/<id>/REPORT.md` | 500 |
| `outdated_apps` | Up to 50 outdated rows | `InventoryDB.query(status="outdated")` | 1500 |
| `churn_history_30d` | Apps with ≥3 updates in 30d | `update_history` SQL | 400 |
| `skip_list_current` | Current adapter's skip config | Per-adapter | 200 |
| `schedules_current` | Installed schedules | `IScheduler.list()` | 200 |
| `web_registry_schema` | TOML schema docstring (no values) | hardcoded | 300 |
| `adapter_capabilities` | Capability flag + manager list | `adapter.capabilities` | 100 |
| `recent_apply_history` | Last 5 apply runs summary | `update_history` SQL | 600 |

### 4.4 Token budget enforcement

Hard cap: **4k tokens** of context. Cheap heuristic: `len(text) / 4`.
Sits alongside the transcript cap (8k) and system prompt (~1k) inside
the overall input budget defined in §3.4.

Resolvers return a `(text, priority)` tuple where priority is 1-10
(higher = more important to keep). Injector greedy-fills highest
priority first; truncates lowest-priority sections at sentence/row
boundaries when budget exceeded.

If trimming occurred, a `context_trimmed` chunk is emitted in the
SSE stream before the first token. SPA renders an unobtrusive ℹ pill
below the assistant reply: "trimmed inventory context to fit". Click
to see which tags were truncated and by how much.

### 4.5 Hard-exclusion filter

Resolvers MUST NOT return:
- File contents from `~/.config/ascendo/ai.json` (API keys).
- Environment variables matching `*KEY|*TOKEN|*SECRET|*PASSWORD`
  (defensive; nothing currently feeds env vars into context).
- Paths outside `~/.ascendo/`, `~/.config/ascendo/`, the Ascendo
  repo root, and the adapter's read-paths.

The filter lives in `context.py` as a validator that wraps each
resolver call. Violations raise; loud failure rather than silent
leakage.

### 4.6 Output format

Markdown, prepended to system prompt with explicit delimiters:

```
<ascendo_context>
## Machine
- OS: macOS / adapter=macos / tier=1
- Doctor: 12/12 ok
- Last run: 12 min ago, success (brew:151, mas:13, ...)

## Inventory totals
brew=152, mas=13, npm=9, pip=11, web=318, system=64

## Outdated apps (top 10 by category)
- docker (web): 4.71 → 4.72
- megasync (web): 6.2.2 → 6.3.0.1
...
</ascendo_context>
```

## 5. Action proposals

The LLM signals "render a button" via a fenced code block with the
language tag `ascendo-action`:

````markdown
Your last run failed because winget couldn't resolve the AutoHotkey ID.
Try running a fresh check first:

```ascendo-action
{
  "id": "run_winget_check",
  "label_en": "Run winget check",
  "label_pl": "Uruchom winget check",
  "verb": "POST",
  "path": "/runs/async",
  "body": {"categories": ["winget"], "phases": ["check"]},
  "confirm": false,
  "risk": "low"
}
```

If that's clean, then:

```ascendo-action
{
  "id": "run_winget_apply",
  "label_en": "Apply winget upgrades",
  "verb": "POST", "path": "/runs/async",
  "body": {"categories": ["winget"], "phases": ["apply"]},
  "confirm": true, "risk": "medium"
}
```
````

### 5.1 SPA parser

`aitools.parseStream` in `app.js` holds partial fences until closing
\`\`\` arrives, then validates the JSON. Outcomes:

- **Valid action**: render as a chip button inline. CSS classes by
  risk level: `.btn-primary` (low), `.btn-warn` (medium),
  `.btn-danger` (high). Fence text itself is hidden from the
  rendered reply — user sees clean prose + chip.
- **Invalid JSON inside `ascendo-action` fence**: render as a plain
  code block; warn in console; chip not rendered.
- **Unknown action ID** (server rejects on click): chip greys out
  with tooltip "Unsupported action".

### 5.2 Server-side whitelist

`ALLOWED_ACTIONS` in `core/ascendo/ai/actions.py`:

```python
ALLOWED_ACTIONS: dict[str, tuple[str, str, type[BaseModel]]] = {
    # action_id          (verb,    path,                          body_schema)
    "run_check":         ("POST",  "/runs/async",                 RunPhaseBody),
    "run_plan":          ("POST",  "/runs/async",                 RunPhaseBody),
    "run_apply":         ("POST",  "/runs/async",                 RunPhaseBody),
    "run_verify":        ("POST",  "/runs/async",                 RunPhaseBody),
    "run_cleanup":       ("POST",  "/runs/async",                 RunPhaseBody),
    "install_schedule":  ("POST",  "/scheduler/install",          ScheduleInstallBody),
    "remove_schedule":   ("POST",  "/scheduler/remove",           ScheduleNameBody),
    "trigger_schedule":  ("POST",  "/scheduler/trigger",          ScheduleNameBody),
    "refresh_inventory": ("POST",  "/inventory/db/refresh",       EmptyBody),
    "add_web_override":  ("POST",  "/ai/chat/action/web_override", WebOverrideBody),
    "edit_skip_list":    ("POST",  "/ai/chat/action/skip_list",   SkipListBody),
    "open_view":         ("local", "navigate",                    OpenViewBody),
    # 12 entries total in v1
}
```

The dispatcher (`POST /ai/chat/action`):

1. Looks up `action_id` in `ALLOWED_ACTIONS`. Miss → 400
   `unknown_action`.
2. Validates `body` against the schema. Failure → 422.
3. For mutating actions, applies the confirm-gate policy (see 5.3).
4. Proxies to the underlying endpoint. Returns the underlying
   response wrapped with the action context.
5. Injects a synthetic `system` message into the conversation:
   `Action 'run_check' started. Run id: 4acfaead. [Open Run Center]`.
   The LLM sees this on next turn.

### 5.3 Risk tiers and confirm gates

| Risk | Action examples | Gate |
|---|---|---|
| `low` | `run_check`, `run_plan`, `refresh_inventory`, `open_view` | Click → fire. No modal. |
| `medium` | `run_apply`, `install_schedule`, `remove_schedule`, `trigger_schedule` | Click → existing "type apply to confirm" modal (Wave 1 from M3.6). |
| `high` | `add_web_override`, `edit_skip_list` | Click → diff preview modal + confirm. The diff shows the exact TOML / config change before apply. |

`medium` reuses the modal that already gates Categories-tab apply
clicks. No new threat surface for apply actions — same confirmation
rule whether the user clicked the tab button or an LLM-proposed chip.

`high` is new: writing to `~/.config/ascendo/web_apps.toml` or the
skip list is non-trivial to revert. The diff modal shows the proposed
TOML before-and-after. Cancel = no write.

### 5.4 Whitelist evolution

The whitelist starts at 12 entries (above). Adding a new action is a
PR:
- Code: new entry in `ALLOWED_ACTIONS` + body schema.
- Spec: append to this document.
- Test: contract test asserting the dispatcher accepts valid bodies
  and rejects invalid ones.

No way to add an action via runtime config alone. Keeps the auth
surface auditable and reduces the blast radius of a
prompt-injection-style attack against the LLM.

## 6. Persistence

SQLite at `~/.ascendo/chats.db`, separate file from `inventory.db`.

### 6.1 Schema v1

```sql
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    backend       TEXT NOT NULL,
    model         TEXT,
    locale        TEXT NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0,
    pinned        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    template_id     TEXT,
    context_tags    TEXT,
    actions         TEXT,
    action_clicked  TEXT,
    action_result   TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_messages_created_at      ON messages(created_at);
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at);
CREATE INDEX idx_conversations_archived   ON conversations(archived);

PRAGMA user_version = 1;
```

### 6.2 Migration story

`ChatsDB._migrate()` mirrors `InventoryDB._migrate()` from Sesja 67:

- Read `pragma_user_version`. If 0, run all migrators in order.
- If 1, no-op.
- On failure mid-migration, raise loud — don't continue with partial
  schema.

v0 → v1 is greenfield (file didn't exist). Future v1 → v2
migrations will preserve data where possible.

### 6.3 File posture

- Path: `~/.ascendo/chats.db` (Linux + macOS) / `%USERPROFILE%\.ascendo\chats.db`
  (Windows). Honors `ASCENDO_HOME` env override.
- File perms `0600` (user read/write only). Enforced via `os.chmod`
  after first connection. Windows: equivalent ACL via `icacls`
  (or accept default user-only since `%USERPROFILE%` is per-user).
- Auto-vacuum: incremental.
- WAL mode + `synchronous=NORMAL` (same as inventory.db).
- Per-call connections with `check_same_thread=False`.

### 6.4 dev-sync exclusion

Add `~/.ascendo/chats.db` to `HARD_EXCLUDE_PATTERNS` in
`dev-sync/dev_sync_core.py` (alongside the existing
`.claude/worktrees/` entry from Sesja 19).

Belt-and-suspenders: `~/.ascendo/` is gitignored at the root.
Anyone running `dev-sync-export.sh` will never accidentally upload
their chat history to a cloud overlay.

### 6.5 Endpoint surface

| Verb | Path | Purpose |
|---|---|---|
| GET | `/ai/chat/conversations` | Paginated; `?archived=true`, `?q=text` |
| POST | `/ai/chat/conversations` | Create empty, returns `{id, title="Untitled"}` |
| GET | `/ai/chat/conversations/{id}` | Full transcript |
| PATCH | `/ai/chat/conversations/{id}` | Rename, archive, pin |
| DELETE | `/ai/chat/conversations/{id}` | Hard delete |
| POST | `/ai/chat/conversations/{id}/messages` | Append turn (internal, called after `done`) |
| POST | `/ai/chat/conversations/{id}/export` | Returns markdown transcript |

### 6.6 Auto-title

When the first user message arrives, `title` is set to the first
60 chars of that message (or to the prompt-library template's title
if the user clicked a card). User can rename via PATCH. Truncation
to 60 chars happens at word boundary when possible.

### 6.7 Search

v1: simple SQL `LIKE '%query%'` over `messages.content` and
`conversations.title`. No FTS5 — overkill for the volume (typical
user will have <500 messages; even 10k stays fast with the existing
indexes).

Re-evaluate FTS5 if a user hits search latency >200ms on >10k
messages.

### 6.8 Disk footprint estimate

- Average message: ~1KB (markdown is small).
- 1000 messages = ~1MB.
- Heavy users won't push past 100MB even after years.
- No autoclean or rotation in v1. Settings → AI Tools shows total
  rows + disk size; a "Delete all archived conversations" button
  rounds out the lifecycle.

## 7. SPA chat view

New section in `app/frontend/index.html`: `#view-aitools`. Replaces
the current `#view-suggestions` nav entry.

### 7.1 Layout

Three-column layout on desktop (≥1100px):

```
┌──────────────┬────────────────────────────────────┬──────────────┐
│ Conversations│  Quick suggestions (Sesja 67 cards)│ Prompt       │
│ rail         │  ────────────────────────────────  │ library      │
│              │  Chat thread                        │              │
│ • New chat   │  user message                      │ Diagnostics  │
│ • Today      │  assistant reply + chips           │ Setup        │
│   ├ ...      │  user message                      │ Customize    │
│ • Yesterday  │  assistant reply + chips           │              │
│ • This week  │  [Ask anything…]   [Backend: claude]│              │
│              │                                    │              │
│ Search [__]  │                                    │              │
└──────────────┴────────────────────────────────────┴──────────────┘
```

Narrow viewport (<1100px): side rails collapse into bottom-sheet
toggles (📜 conversations, 📋 prompts).

### 7.2 Components

| Component | Responsibility |
|---|---|
| `aitools.init()` | Wire DOM, load conversations, load prompt library, restore last-opened conversation from localStorage |
| `aitools.renderConversations(list)` | Left rail with date grouping; search; right-click → pin/archive |
| `aitools.openConversation(id)` | Load messages, focus input, scroll to bottom |
| `aitools.send(text, templateId?)` | POST `/ai/chat`, open SSE, stream into pending message div |
| `aitools.streamHandler(event)` | Tokens append; action_proposal renders chip; done commits |
| `aitools.parseActions(content)` | Buffer + extract `ascendo-action` fences |
| `aitools.executeAction(chip)` | POST `/ai/chat/action`, inject synthetic system message |
| `aitools.renderQuickCards()` | Top rail — calls existing `/suggestions/library`; renders Sesja 67 cards unchanged |
| `aitools.renderPromptLibrary()` | Right rail — loads `/ai/chat/library`, renders grouped cards |
| `aitools.backendPicker()` | Footer chip showing current backend; click → Settings AI section |

### 7.3 Streaming UX

- Pending assistant message: `<div class="msg msg-pending">` from
  token 1, with a blinking caret.
- Markdown renders incrementally; code fences (including
  `ascendo-action`) buffer until close to avoid flashing
  half-parsed JSON.
- Cancel button (×) replaces caret while streaming; click →
  POST `/ai/chat/cancel/{turn_id}`.
- On `done.status=error`, swap pending div for `.msg-error` with
  retry button + collapsible details.

### 7.4 Empty state

First-time visitor:
- Quick-suggestion cards (Sesja 67) render normally — users get
  value before configuring anything.
- Below them, a friendly card: "Want a back-and-forth chat?
  [Install Claude Code] · [Install Gemini CLI] · [Install Codex CLI]
  · [Install opencode] · [Add API key]". Each link opens Settings AI
  with that backend pre-selected.
- If at least one backend resolvable, replace friendly card with a
  "Start a chat" CTA.

### 7.5 Prompt library cards

Right rail. Loaded from `/ai/chat/library` (new endpoint reading
`core/ascendo/ai/prompts/library.toml`).

Three groups (matching brainstorming decisions):
- **Diagnostics**: Why did my last run fail? · What does this exit
  code mean? · Why is my package manager locked? · ...
- **Setup**: First-run guide · Recommend an update schedule · How do
  I add a custom web app? · How do I enable Touch ID sudo?
- **Customize**: What apps should I exclude? · Find stale apps · Tune
  my schedule · Add a domain-specific app override

Each card: title (locale-matched) + small icon + tooltip describing
what context it will inject. Click → `aitools.send(starter_prompt,
card.id)`.

### 7.6 i18n

Every UI string in `aitools.*` namespace in `app/frontend/i18n.js`.
EN + PL parity from day one — mirrors Sesja 67/69 pattern.

Prompt-library titles and starter prompts come from `library.toml`,
not from `i18n.js`, so they're authored alongside the prompt itself
(easier to keep them in sync).

## 8. Error handling, cancellation, edge cases

| Failure | Detection | Surface to user |
|---|---|---|
| Selected CLI binary not found | `shutil.which` returns None at turn start | Auto-fallback to next backend; banner above pending message |
| CLI authentication expired | Probe call returns non-zero with auth error pattern | Stop, error message with [Re-auth] button → opens vendor docs |
| CLI subprocess crashes mid-stream | `process.returncode` non-zero on exit | Convert pending to `.msg-error` + stderr tail (last 12 lines) + [Retry] |
| CLI hangs (no output 30s) | Watchdog timer per chunk | Auto-cancel + same error UI |
| API 4xx (bad key, rate limited) | `urllib.HTTPError` | Error UI; "Add API key" or "Wait 60s and retry" |
| API 5xx or timeout | `urllib.URLError` / `TimeoutError` | Error UI with [Retry] |
| Context exceeds 4k tokens | Budget guard truncates low-priority | Silent + ℹ chip below reply: "trimmed inventory context" |
| Action proposal JSON malformed | `json.loads` fails in SPA parser | Render fence as plain code block; warn in console; no chip |
| Action dispatcher rejects unknown id | Whitelist check fails server | Chip greys out with tooltip "Unsupported action"; click is no-op |
| Action execution fails | Dispatcher returns error JSON | Synthetic system message; LLM sees on next turn |
| `chats.db` write fails | `sqlite3.OperationalError` | Toast; turn still completes in-memory; persistent failure → banner offers "Disable chat history" |
| Conversation load fails | Schema check or `OperationalError` | Show list minus corrupt one; "Open SQL diagnostic" link |
| User closes tab mid-stream | `request.is_disconnected()` polled per chunk | Cancel subprocess + mark turn cancelled; partial content persisted |
| User clicks Cancel button | POST `/ai/chat/cancel/{turn_id}` | Subprocess killed; "(cancelled)" suffix on partial message |
| SSE connection drops (network blip) | `EventSource.onerror` | Auto-reconnect once to `/ai/chat/stream/{turn_id}`; resume if still streaming, fetch saved if done |

### 8.1 Cancellation sequence

```
SPA                          FastAPI                         Subprocess
─────                        ─────────                       ──────────
[Cancel chip clicked]
  │
  ├─ POST /ai/chat/cancel/{tid} ───►  TurnRegistry.get(tid)
  │                                      │
  │                                      ├─ task.cancel()
  │                                      │     │
  │                                      │     └─ asyncio.CancelledError
  │                                      │           │
  │                                      │           └─ process.terminate()
  │                                      │                 │
  │                                      │                 └─ SIGTERM
  │                                      │                       (2s grace)
  │                                      │                 process.kill() if alive
  │                                      │
  │                                      └─ SSE emits {event: done, status: cancelled}
  │                                              │
  │                                              └─ SPA closes stream
  │
  └─ chat UI replaces caret with "(cancelled)"
       and persists partial content to chats.db
```

### 8.2 No retries on server side

If a CLI fails, the server reports it and stops. Retry is a user
gesture (clicking [Retry] in the error UI). Reason: silent retries
can rack up subscription usage / API costs on flaky networks without
the user knowing.

### 8.3 Logging surface

Every turn writes one structured log line to
`~/.ascendo/logs/ai/<date>.log` (new path, mirrors `~/.ascendo/runs/`).

Line includes: `turn_id, backend, model, status, tokens_in, tokens_out, duration_ms, action_clicked`.

NO content — content lives in `chats.db`. Log is the ops surface,
useful when a user reports "chat felt slow yesterday". Rotation:
30 days, deleted on startup.

### 8.4 Telemetry stance

Nothing about chat usage leaves the machine beyond what the user's
chosen CLI / API transmits as part of normal operation. No
"phoned-home" metrics, no usage stats. Opt-in remote telemetry would
be a separate ADR per ADR-0005's principle.

## 9. Tests and verification

### 9.1 Unit tests

| Test file | What it pins | Count |
|---|---|---|
| `test_ai_backend_resolver.py` | Resolution order, preference override, fallback chain, "no backend" case, 24h probe cache | ~15 |
| `test_ai_cli_drivers.py` | Each driver parametrized: availability, auth probe, stream parsing | ~24 |
| `test_ai_context_injector.py` | Base context, per-tag resolvers, budget truncation, hard-exclusion filter | ~20 |
| `test_ai_actions_parser.py` | Fence extraction, partial-fence buffering, malformed JSON, locale labels | ~12 |
| `test_ai_actions_dispatcher.py` | Whitelist enforcement, confirm gate, unknown action 400, bad body 422 | ~20 |
| `test_ai_persistence.py` | Schema migration, CRUD, cascade delete, search, auto-title, perms, dev-sync exclusion | ~18 |
| `test_ai_chat_endpoints.py` | POST `/ai/chat` returns turn_id; SSE sequence; cancel kills + emits cancelled | ~15 |
| `test_ai_chat_sse_disconnect.py` | Client disconnect → cancel; partial persisted; reconnect replays | ~6 |
| `test_aitools_view.py` | DOM smoke: view renders, quick suggestions still load, library mounts, empty state | ~10 |

**Total: ~140 new tests.** None require a real CLI or API key —
everything goes through fakes.

### 9.2 Fake CLI fixtures

`tests/fixtures/ai_cli/`:

```
ai_cli/
├── fake-claude       # bash script; reads args, emits scripted stream-json
├── fake-gemini       # ditto, text stream
├── fake-codex        # ditto, exec stream
├── fake-opencode     # ditto
├── auth_expired/     # variants for auth-failure tests
│   ├── fake-claude
│   └── ...
└── hanging/          # variants that sleep forever (for timeout tests)
    ├── fake-claude
    └── ...
```

Each fake reads a `FIXTURE_CASE` env var to switch behavior:
`success | auth_expired | crash | hang | partial_then_eof`.

Tests `PATH`-shadow real binaries: `PATH=tests/fixtures/ai_cli/:$PATH`
and `chmod +x` the fakes in conftest.

Windows: `.cmd` shims wrap the same scripts under WSL or bash if
available; otherwise PowerShell equivalents.

### 9.3 validate-*.sh new stages

- **`validate-macos.sh` Stage 14 (8 sub-steps)**:
  1. doctor reports backend slot
  2. `/ai/chat/backends` enumerates 5 entries with status
  3. create + delete a throwaway conversation via API
  4. POST a turn against `fake-claude`, verify SSE sequence
  5. verify action chip parsing from a known scripted reply
  6. verify dispatcher rejects unknown action
  7. verify `chats.db` file perms `0600`
  8. verify dev-sync HARD_EXCLUDE includes `chats.db`

- **`validate-windows.ps1`**: mirror with `fake-claude.cmd` shims.

- **`validate-ubuntu.sh`**: mirror.

All three harnesses currently expect `ALL CHECKS PASSED.` as their
final line. These stages add to the count:
- macOS: 44 → 52
- Windows: variable (depends on prior stages)
- Ubuntu: 23 → 31

### 9.4 Manual smoke test runbook

Lands in `MACOS_QUICKSTART.md` §14 / `WINDOWS_QUICKSTART.md` §13 /
`LINUX_QUICKSTART.md` mirror:

```
1. ascendo web restart
2. Open dashboard, navigate to AI Tools tab
3. Quick suggestions cards still appear at top (unchanged from Sesja 67)
4. Click "New chat" — empty conversation, prompt library on right
5. Click "Why did my last run fail?" card
   → should send canned prompt with latest_failed_sidecar context
   → assistant replies with diagnosis
   → should render an inline [Run winget check] chip
6. Click the chip — confirm modal does NOT appear (low-risk check)
   → run_id appears as a synthetic system message
7. Switch UI language to PL — open new chat — verify response in Polish
8. Open Settings → AI → switch backend → reload chat → verify backend pill
9. Stop dashboard process mid-stream — reload — verify partial message preserved
```

### 9.5 Performance budgets

- Time-to-first-token: <3s API backends, <5s CLI backends (CLI
  startup overhead).
- Time-to-done: no hard cap, but watchdog cancels at 90s with no
  output between chunks.
- Context build: <100ms (cached InventoryDB + filesystem reads).
- Persistence write per turn: <50ms.

### 9.6 Spec-level acceptance gate

1. All unit tests pass on all three OSes.
2. `validate-{macos,windows,ubuntu}.sh` exits 0 with new stages.
3. Manual smoke runbook passes end-to-end on at least one real
   machine per OS.
4. `claude`, `gemini`, `codex`, `opencode` each verified to work
   with their `MIN_VERSIONS` pinned version.
5. EN+PL i18n parity unchanged from pre-feature baseline.
6. No new lint / mypy violations.
7. Spec self-review pass + user spec review pass.

## 10. Rollout plan

### 10.1 Phasing

This is a substantial milestone. Recommended phasing:

**Phase A** (week 1): backend module + context injector + persistence.
End state: API works end-to-end with fake CLIs; no SPA changes yet.

**Phase B** (week 1.5): SPA chat view + prompt library.
End state: real chat works in browser; backed by fake CLIs in tests
and one real CLI for live testing on dev machine.

**Phase C** (week 2): action proposals + dispatcher + confirm gates.
End state: one-click chips work; whitelist enforced; existing
Categories-tab confirm modal reused.

**Phase D** (week 2.5): polish, EN+PL i18n, validate-* stages, smoke
runbooks, real-Mac/Windows/Ubuntu validation. Tag `v0.5.0`.

### 10.2 Compatibility with existing Suggestions

- `routes/suggestions.py` and `/suggestions/library` endpoint:
  unchanged.
- `routes/ai.py::call_provider_inference()`: unchanged; becomes the
  `ApiKeyBackend` path.
- The "Suggestions" nav entry renames to "AI Tools" but the URL
  path / route name stays for any external link / bookmark.
- Sesja 67's existing rule-based + AI-augmented cards keep working.

### 10.3 Documentation updates

- `MACOS_QUICKSTART.md` / `WINDOWS_QUICKSTART.md` / `LINUX_QUICKSTART.md`:
  new "AI Tools" section.
- `USER_GUIDE.md`: chapter on conversational diagnosis.
- `app/frontend/i18n.js`: ~80 new keys × 2 locales (en + pl).
- `core/ascendo/ai/prompts/library.toml`: 15-20 starter entries.
- HANDOFF.md: closing Sesja entry after Phase D ships.

## 11. Open questions deferred to v2

- **Cross-host conversation sync**: encrypted, opt-in, P2P or via a
  user-owned cloud overlay (Proton Drive, Dropbox)?
- **Full tool-use API integration**: native Anthropic-tools /
  OpenAI-function-calling vs. the current fence-based protocol.
  Native APIs would give better streaming UX for actions; trade-off
  is per-backend complexity.
- **Auto-apply mode** for trusted action sequences (e.g. "run check
  on all categories every Monday morning and DM me a summary"). Big
  scope: needs scheduler integration + LLM-as-cron, security review.
- **Multi-modal**: paste a screenshot of an error and ask "what does
  this mean?". Trivial for Claude / GPT-4o vision; harder for CLI
  flow without changes.
- **Prompt library curation by community**: PRs adding starter
  prompts. Needs spec validator + review process.
- **Encrypted chats.db**: OS-keychain-derived key, "forgot
  passphrase" recovery story, dev-sync compatibility.
- **Cost telemetry** (opt-in): track tokens / cost per backend so
  users on metered plans see usage trends. ADR-level discussion.

## 12. References

- ADR-0003 — JSON v1 sidecar contract
- ADR-0005 — Six-layer architecture + threat model
- ADR-0007 — Plugin manifest v1
- Sesja 67 — `routes/ai.py::call_provider_inference()` (the 6-provider
  API path being reused as `ApiKeyBackend`)
- Sesja 67 — `routes/suggestions.py` and `/suggestions/library`
  (kept verbatim as the Quick suggestions rail)
- Sesja 67 — Schedule tab pattern (`routes/scheduler_real.py`),
  mirrored for the action dispatcher
- M2.10 — async run + SSE pattern (`RunRegistry`), mirrored as
  `TurnRegistry`
- Sesja 19 — dev-sync `HARD_EXCLUDE_PATTERNS`
- HANDOFF.md Sesja 67 — Schedule tab + Suggestions AI augmentation
- HANDOFF.md Sesja 36 — Touch ID sudo cache (referenced as a context
  resolver for the "Why is mas asking for sudo?" prompt)
