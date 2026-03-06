# ARI-8: Aristotlebot-Slack Repository Summary

> **Task**: Read the repo and summarize what Aristotlebot currently does, how it handles Slack messages, whether it has Lean-related functionality, and the current project structure.
>
> **Date**: 2026-03-06
> **Branch**: `task/ari-8-repo-summary`
> **Status**: Read-only analysis — no code changes made.

---

## 1) What Aristotlebot Currently Does

**Aristotlebot is a Slack bot that wraps [Aristotle Agent v2](https://aristotle.ai) for Lean 4 theorem proving.** Users send `.lean` files, URLs to `.lean` files, or natural language prompts via Slack, and the bot submits them to Aristotle's proving engine and posts the results back in-thread as downloadable `.lean` file attachments.

### LLM / AI Integration

- Uses **`aristotlelib`** (Python SDK, `>=0.1.0`) which wraps the Aristotle Agent v2 API.
- Two submission modes via `aristotlelib.project.Project.prove_from_file()`:
  - **Formal mode** (`ProjectInputType.FORMAL_LEAN`): For `.lean` files. Called with `validate_lean_project=False`, `auto_add_imports=False`, and optional `context_file_paths` for resolved dependencies.
  - **Informal mode** (`ProjectInputType.INFORMAL`): For natural language prompts (e.g., "Prove that 1 + 1 = 2") using `input_content=`.
- There is **no direct OpenAI/Anthropic API integration** — all LLM interaction is abstracted behind `aristotlelib`.

### Core Features

| Feature | Description |
|---------|-------------|
| `.lean` file uploads | Upload a `.lean` file; bot downloads, submits to Aristotle in formal mode, posts proof as `.lean` attachment |
| URLs to `.lean` files | Paste a URL ending in `.lean`; bot downloads and processes identically to uploads. GitHub blob URLs auto-converted to raw. |
| Natural language | Any other message submitted to Aristotle in informal mode |
| Import resolution | Parses Lean 4 `import` statements, recursively fetches local dependencies from GitHub (max depth 3, max 20 files) |
| Solution file attachments | Proofs uploaded as `.lean` file attachments (not inline code). Summary includes ✅/❌ status + theorem name |
| Smart bot filtering | Filters only its own messages (discovered via `auth.test`). Other bots' messages are processed normally |
| Health check endpoint | HTTP `/health` on port 8080 with Socket Mode status, event counts, registered listeners |
| Diagnostic logging | `[DIAG]`-prefixed logging at INFO level; raw payloads at DEBUG level |

---

## 2) How It Handles Incoming Slack Messages

### Connection Method

Uses Slack's **Socket Mode** via `slack-bolt`'s `SocketModeHandler`. The bot connects over WebSocket — no public HTTP endpoint is needed for event delivery.

### Message Flow

```
Slack Event → app.py (filter + classify + telemetry) → handlers.py (dispatch) → aristotlelib → Slack response
                ↓                                                                    ↓
           health.py (HTTP /health)                                       ┌──────────┴──────────┐
                                                                          ↓                     ↓
                                                                   summary message      .lean file upload
                                                                   (via say())       (via Slack file API)
```

### Step-by-Step

1. **Event Registration** (`app.py`): Listens for two Slack events:
   - `message` — DMs and channel messages
   - `app_mention` — @-mentions of the bot

2. **Filtering** (`app.py`):
   - Ignores **own** messages only (bot_id discovered dynamically via `auth.test` at startup) to prevent feedback loops
   - Ignores message subtypes (edits, deletions, etc.)
   - Messages from **other** bots/apps (e.g., Klaw) are processed normally

3. **Classification** (`utils.py → classify_message()`): Each message is classified into a `MessageKind` enum (discriminated union):
   - **`LEAN_FILE_UPLOAD`** — if the event has a `.lean` file attachment (highest priority)
   - **`LEAN_URL`** — if the message text contains a URL ending in `.lean` (with Slack `<URL>` unwrapping)
   - **`NATURAL_LANGUAGE`** — everything else (fallback)

4. **Dispatch** (`handlers.py → handle_message()`): Routes to one of three handlers:
   - **File upload handler** (`_handle_lean_file_upload`): Downloads Slack file, parses imports (can't resolve without repo info), submits formal mode
   - **URL handler** (`_handle_lean_url`): Downloads from URL (auto-converts GitHub blob URLs), resolves imports recursively, submits formal mode with context files
   - **Natural language handler** (`_handle_natural_language`): Submits text in informal mode

5. **Each handler follows the same contract**:
   - Adds ⏳ hourglass reaction to acknowledge
   - Downloads/prepares input
   - Resolves Lean 4 imports (if applicable)
   - Calls `aristotlelib.Project.prove_from_file()` (async, via `asyncio.run()`)
   - Posts brief summary (✅/❌ + theorem name) in-thread
   - Uploads solution as `.lean` file attachment (two-step Slack external upload API)
   - Cleans up temp files in `finally` block
   - Removes hourglass reaction

### Key Design Decisions

- **Sync Bolt + async handlers**: Uses sync `App` (not `AsyncApp`) because Socket Mode works reliably only with sync Bolt. Async `aristotlelib` calls run inside `asyncio.run()`.
- **`say()` is synchronous**: In the sync Bolt context, `say` and `client` are sync. Handlers do NOT `await` them.
- **Graceful file upload fallback**: If file upload fails, the summary is still posted with a note that the upload failed. Results are never silently lost.

---

## 3) Lean-Related Functionality

**Yes, Aristotlebot has extensive Lean-specific functionality**, primarily in `lean_imports.py` and `utils.py`:

| Feature | Module | Details |
|---------|--------|---------|
| **Lean 4 import parsing** | `lean_imports.py` | Regex-based parser extracts `import` statements, classifies as LOCAL vs EXTERNAL via `EXTERNAL_PACKAGES` frozenset |
| **External package detection** | `lean_imports.py` | Recognizes: Mathlib, Std, Init, Lean, Lake, Batteries, Qq, Aesop, ProofWidgets, Cli |
| **Recursive dependency resolution** | `lean_imports.py` | Fetches local imports from GitHub repos recursively. Bounded by `MAX_DEPTH=3` and `MAX_FILES=20`. Cycle detection via visited set |
| **Module path → file path** | `lean_imports.py` | Converts dotted paths: `ArkLib.Data.Fin.Basic` → `ArkLib/Data/Fin/Basic.lean` |
| **GitHub repo info extraction** | `lean_imports.py` | Extracts owner/repo/ref from `raw.githubusercontent.com` and `github.com/blob/` URLs |
| **Import context formatting** | `lean_imports.py` | Formats resolved files into LLM-readable context with headers for each file |
| **Theorem name extraction** | `utils.py` | Regex extracts first `theorem`/`lemma`/`def`/`example` name from Lean source |
| **Solution filenames** | `utils.py` | Generates descriptive filenames from theorem names (e.g., `Nat.add_comm.lean`) |
| **GitHub blob → raw URL conversion** | `utils.py` | Transparently converts `github.com/.../blob/...` to `raw.githubusercontent.com/...` |
| **Formal mode submission** | `handlers.py` | Submits `.lean` files with `ProjectInputType.FORMAL_LEAN` and optional `context_file_paths` |
| **Informal mode submission** | `handlers.py` | Submits natural language with `ProjectInputType.INFORMAL` |

### Key Lean Types

- `LeanImport` (frozen dataclass): Parsed import with module_path, kind (LOCAL/EXTERNAL), top_level_package
- `ImportKind` (Enum): LOCAL or EXTERNAL
- `GitHubRepoInfo` (frozen dataclass): owner, repo, ref — with `raw_url_for()` builder
- `ResolvedImports` (dataclass): resolved_files dict, unresolved list, depth_reached, total_fetched
- `UnresolvedImport` (frozen dataclass): module_path + reason string

---

## 4) Current Project Structure

```
aristotlebot-slack/                  (repo root)
├── main.py                          # Legacy entry point
├── pyproject.toml                   # Build config (setuptools), deps, pytest config
├── requirements.txt                 # Production: slack-bolt, slack-sdk, aristotlelib, aiohttp
├── requirements-dev.txt             # Dev: pytest, pytest-asyncio, pytest-mock
├── Dockerfile                       # Container build
├── CLAUDE.md                        # Developer guide (architecture, patterns, API usage)
├── README.md                        # User-facing docs (setup, usage, troubleshooting)
├── AGENTS.md                        # Agent instructions
├── MEMORY.md                        # Project memory/notes
├── .gitignore
│
├── src/aristotlebot/                # Main package (1,849 lines)
│   ├── __init__.py          (3)     # Package version ("0.1.0")
│   ├── __main__.py          (49)    # `python -m aristotlebot` entry point
│   ├── app.py               (290)   # Slack Bolt app factory, event listeners, bot identity, EventTelemetry
│   ├── handlers.py          (451)   # Three message handlers + Aristotle submission + import resolution
│   ├── health.py            (111)   # HTTP health-check server (daemon thread, port 8080)
│   ├── lean_imports.py      (481)   # Lean 4 import parsing + recursive GitHub-based dependency resolution
│   └── utils.py             (464)   # Message classification, file download/upload, formatting helpers
│
├── tests/                           # 214 passing, 2 skipped (integration) = 216 total (2,811 lines)
│   ├── test_app.py          (126)   # App creation, env validation, telemetry
│   ├── test_ari3_fetch_mathlib_init.py (81)  # Mathlib Init.lean fetch tests
│   ├── test_bot_filtering.py (191)  # Bot message filtering (own vs other bot_ids)
│   ├── test_handlers.py     (735)   # Handler tests (mocked aristotlelib + Slack + imports)
│   ├── test_health.py       (118)   # Health endpoint tests
│   ├── test_lean_imports.py (606)   # Import parsing, resolution, context formatting
│   └── test_utils.py        (954)   # Classification, formatting, file upload, file reading
│
└── reports/                         # Analysis reports
    └── ari-3-mathlib-init-lean.md   # Previous ARI-3 analysis
```

### Dependencies

| Package | Version Spec | Purpose |
|---------|-------------|---------|
| `slack-bolt` | `>=1.18.0` | Slack framework (Bolt) |
| `slack-sdk` | `>=3.27.0` | Slack Web API client |
| `aristotlelib` | `>=0.1.0` | Aristotle Agent v2 SDK |
| `aiohttp` | `>=3.9.0` | Async HTTP client for file downloads |
| `pytest` | `>=8.0` | Test framework |
| `pytest-asyncio` | `>=0.23` | Async test support |
| `pytest-mock` | `>=3.12` | Mocking utilities |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | App-Level Token (`xapp-...`) for Socket Mode |
| `ARISTOTLE_API_KEY` | Yes | API key for aristotlelib |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `HEALTH_CHECK_PORT` | No | Health server port (default: `8080`) |

### Architectural Patterns

- **Discriminated unions**: `MessageKind` enum prevents invalid classification states
- **Structured results**: `AristotleResult` NamedTuple separates submission from formatting. Invariant: `solution_text` and `error` are never both non-None
- **Graceful degradation**: Import resolution, file uploads — failures degrade gracefully, never silently lose results
- **Dynamic identity**: Bot's own `bot_id` discovered at startup via `auth.test`, never hardcoded
- **Temp file safety**: Always cleaned up in `finally` blocks
- **Correctness by Construction**: Frozen dataclasses with `__post_init__` assertions, bounded recursion (MAX_DEPTH + MAX_FILES), cycle detection

---

## Test Results

```
214 passed, 2 skipped in 6.56s
```

The 2 skipped tests are integration tests in `test_handlers.py` that require live Slack + Aristotle credentials.
