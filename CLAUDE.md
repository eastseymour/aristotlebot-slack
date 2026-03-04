# CLAUDE.md — Aristotle Slack Bot

## Quick Reference

```bash
# Run tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_utils.py -v

# Run tests with coverage
python3 -m pytest tests/ -v --cov=aristotlebot --cov-report=term-missing

# Lint (if ruff/flake8 installed)
ruff check src/ tests/       # or: flake8 src/ tests/

# Run the bot (requires env vars)
python3 -m aristotlebot              # preferred (module entry point)
python3 main.py                      # also works

# Install the package (required for module imports)
pip install -e .

# Install dependencies
pip install -r requirements.txt          # production
pip install -r requirements-dev.txt      # development (includes pytest)

# Check health (while bot is running)
curl http://localhost:8080/health
```

## Architecture

### Message Flow

```
Slack event
  → Socket Mode WebSocket
    → Bolt middleware (log_all_events — logs every event for diagnostics)
      → @app.event("message") or @app.event("app_mention")
        → classify_message() → MessageKind enum
          → handle_message() dispatch
            → _handle_lean_file_upload / _handle_lean_url / _handle_natural_language
              → aristotlelib.Project.prove_from_file()
                → format_result_message() → say() back to Slack thread
```

### Module Responsibilities

1. **app.py** — Creates a Slack Bolt `App` with sync event listeners. Messages are classified by `utils.classify_message()` into one of three `MessageKind` variants, then dispatched to the async `handle_message()` via `asyncio.run()`. Includes:
   - **Middleware** (`log_all_events`): logs every incoming event at INFO level and full payload at DEBUG level
   - **EventStats**: singleton that tracks total events, last event timestamp, and events by type
   - **get_registered_listeners()**: introspects Bolt's listener registry for health-check reporting

2. **handlers.py** — Three handler functions (file upload, URL, natural language). Each:
   - Adds a hourglass reaction
   - Downloads/prepares input
   - Calls `aristotlelib.Project.prove_from_file()`
   - Posts the result in-thread
   - Cleans up temp files in `finally` blocks

3. **utils.py** — Pure helpers:
   - `classify_message()` — Classifies Slack events → `MessageKind` enum
   - `download_slack_file()` / `download_url()` — Async file downloaders
   - `format_result_message()` — Formats Aristotle results for Slack
   - `read_solution_file()` — Reads `.lean` or `.tar.gz` solution files

4. **healthcheck.py** — HTTP health-check server (default port 8080):
   - Runs in a daemon thread, never blocks Socket Mode
   - `GET /health` returns JSON with event stats, uptime, registered listeners
   - Port configurable via `HEALTH_CHECK_PORT` env var (0 to disable)

### Key Design Decisions

- **Sync Bolt + async handlers**: We use sync `App` (not `AsyncApp`) because Socket Mode only works reliably with sync Bolt. Async aristotlelib calls run inside `asyncio.run()`.
- **`say()` is synchronous**: In the sync Bolt context, `say` and `client` are sync. Handlers do NOT `await` them.
- **MessageKind enum**: Discriminated union prevents invalid classification states.
- **Temp dir cleanup**: Always in `finally` blocks. Never leak temp files.
- **Middleware-first logging**: The `log_all_events` middleware fires before any specific listener, so if events reach the app but no listener fires, we'll see them in the logs.
- **EventStats singleton**: Thread-safe enough for counters (GIL-protected int increments); no lock needed for diagnostics.

### aristotlelib API Patterns

```python
# Formal mode (for .lean files)
# IMPORTANT: auto_add_imports=False is required when validate_lean_project=False,
# otherwise aristotlelib asserts that validate_lean_project must be True.
result_path = await Project.prove_from_file(
    input_file_path=path_to_lean,
    validate_lean_project=False,
    auto_add_imports=False,
    wait_for_completion=True,
    output_file_path=output_path,
    project_input_type=ProjectInputType.FORMAL_LEAN,
)

# Informal mode (for natural language)
result_path = await Project.prove_from_file(
    input_content="Prove that 1+1=2",
    wait_for_completion=True,
    output_file_path=output_path,
    project_input_type=ProjectInputType.INFORMAL,
)
```

### ProjectStatus Values

`NOT_STARTED`, `QUEUED`, `IN_PROGRESS`, `COMPLETE`, `FAILED`, `CANCELED`, `UNKNOWN`

## Environment Variables

| Variable             | Description                                          |
| -------------------- | ---------------------------------------------------- |
| `SLACK_BOT_TOKEN`    | Bot User OAuth Token (`xoxb-…`)                      |
| `SLACK_APP_TOKEN`    | App-Level Token (`xapp-…`) for Socket Mode           |
| `ARISTOTLE_API_KEY`  | API key for aristotlelib                             |
| `LOG_LEVEL`          | Optional. Default: `INFO`. Set to `DEBUG` for full event payloads |
| `HEALTH_CHECK_PORT`  | Optional. Default: `8080`. Set to `0` to disable     |

## File Layout

```
src/aristotlebot/
├── __init__.py        # Package version
├── __main__.py        # python -m aristotlebot entry point
├── app.py             # Bolt app factory, event listeners, middleware, EventStats
├── handlers.py        # Three input mode handlers
├── healthcheck.py     # HTTP health-check server (port 8080)
└── utils.py           # Classification, download, formatting helpers

tests/
├── test_app.py        # App creation, env validation, event listener registration
├── test_handlers.py   # Handler tests (mock aristotlelib + Slack)
├── test_healthcheck.py# Health-check endpoint tests
└── test_utils.py      # Classification, formatting, file reading tests
```

## Testing Notes

- Tests mock `aristotlelib.Project.prove_from_file` and Slack's `say`/`client`
- `create_app()` accepts `token_verification_enabled=False` for testing (skips `auth.test` API call)
- Integration test stubs exist in `test_handlers.py` — they require live credentials and are skipped by default
- All async tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Test imports use the canonical `aristotlebot` package path (not `src.aristotlebot`)
- Package must be installed (`pip install -e .`) for tests and module invocation to work
- Health-check tests use port 0 (OS-assigned) to avoid port conflicts

## Debugging Zero Events

If the bot connects but receives no events:

1. Check `curl http://localhost:8080/health` — if `total_events` is 0, events are not reaching the app.
2. Check logs for `Raw event received` — the middleware logs every event before listeners fire.
3. If no `Raw event received` logs: the problem is upstream (Slack app config, not code):
   - Event Subscriptions not enabled in the Slack API dashboard
   - Bot events (`app_mention`, `message.channels`, `message.im`) not subscribed
   - Bot not invited to the channel
   - App needs reinstall after scope changes
4. If `Raw event received` appears but no `message event listener fired`:
   - Check that the event type matches a registered listener
   - Check `events_by_type` in the health check to see what types arrive
