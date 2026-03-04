# CLAUDE.md — Aristotle Slack Bot

## Quick Reference

```bash
# Install the package (required for module imports and tests)
pip install -e .

# Run tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_utils.py -v

# Run the bot (requires env vars)
python3 -m aristotlebot              # preferred (module entry point)
python3 main.py                      # also works

# Install dependencies
pip install -r requirements.txt          # production
pip install -r requirements-dev.txt      # development (includes pytest)

# Lint (no formal linter configured yet; use ruff or flake8 ad hoc)
# ruff check src/ tests/
```

## Architecture

### Message Flow

```
Slack event → app.py (classify + telemetry) → handlers.py (dispatch) → aristotlelib → Slack thread reply
                ↓
           health.py (HTTP /health endpoint reports event counts)
```

1. **app.py** — Creates a Slack Bolt `App` with sync event listeners. At startup, calls `auth.test` to dynamically discover the bot's own `bot_id` — only messages from this bot_id are filtered (other bots' messages are processed normally). Messages are classified by `utils.classify_message()` into one of three `MessageKind` variants, then dispatched to the async `handle_message()` via `asyncio.run()`. Maintains an `EventTelemetry` singleton that tracks all received events.

2. **handlers.py** — Three handler functions (file upload, URL, natural language). Each:
   - Adds a hourglass reaction
   - Downloads/prepares input
   - Calls `aristotlelib.Project.prove_from_file()`
   - Posts the result in-thread
   - Cleans up temp files in `finally` blocks

3. **utils.py** — Pure helpers:
   - `classify_message()` — Classifies Slack events into `MessageKind` enum. Handles Slack's angle-bracket URL wrapping (`<https://...>`) by stripping brackets before matching.
   - `_strip_slack_angle_brackets()` — Preprocesses Slack event text to unwrap `<URL>` and `<URL|label>` patterns into bare URLs. Leaves non-URL angle brackets (e.g. `<@U12345>`) untouched.
   - `download_slack_file()` / `download_url()` — Async file downloaders
   - `format_result_message()` — Formats Aristotle results for Slack
   - `read_solution_file()` — Reads `.lean` or `.tar.gz` solution files

4. **health.py** — HTTP health-check server running on a daemon thread (default port 8080). Reports:
   - Socket Mode connection status
   - Total events received, broken down by type
   - Last event timestamp
   - Registered event listeners

### Key Design Decisions

- **Sync Bolt + async handlers**: We use sync `App` (not `AsyncApp`) because Socket Mode only works reliably with sync Bolt. Async aristotlelib calls run inside `asyncio.run()`.
- **`say()` is synchronous**: In the sync Bolt context, `say` and `client` are sync. Handlers do NOT `await` them.
- **MessageKind enum**: Discriminated union prevents invalid classification states.
- **Slack angle-bracket stripping**: Slack wraps URLs in `<>` in event text (e.g. `<https://example.com/file.lean>`). The `_strip_slack_angle_brackets()` helper normalizes these before URL matching. This is done as a preprocessing step rather than complicating the URL regex, keeping concerns separated.
- **Temp dir cleanup**: Always in `finally` blocks. Never leak temp files.
- **Dynamic bot_id discovery**: At startup, `create_app()` calls `auth.test` to discover the bot's own `bot_id`. This is stored in `_own_bot_id` and used to filter ONLY the bot's own messages. Messages from other bots/apps (like Klaw) are processed normally. The bot_id is never hardcoded.
- **`_is_own_bot_message()` helper**: Encapsulates the bot message filtering logic. Returns `True` only when the event's `bot_id` matches our own. When `_own_bot_id` is None (e.g., in tests), it conservatively returns `False` (never drops messages).
- **EventTelemetry singleton**: Module-level dataclass shared between app.py and health.py. All event counts are recorded here for observability.
- **Diagnostic logging**: All event handlers log with `[DIAG]` prefix at INFO level. Raw payloads logged at DEBUG level. Set `LOG_LEVEL=DEBUG` for full visibility.

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

| Variable             | Description                                |
| -------------------- | ------------------------------------------ |
| `SLACK_BOT_TOKEN`    | Bot User OAuth Token (`xoxb-...`)          |
| `SLACK_APP_TOKEN`    | App-Level Token (`xapp-...`) for Socket Mode |
| `ARISTOTLE_API_KEY`  | API key for aristotlelib                   |
| `LOG_LEVEL`          | Optional. Default: `INFO`                  |
| `HEALTH_CHECK_PORT`  | Optional. Default: `8080`                  |

## File Layout

```
src/aristotlebot/
├── __init__.py        # Package version
├── __main__.py        # python -m aristotlebot entry point
├── app.py             # Bolt app factory, event listeners, EventTelemetry
├── handlers.py        # Three input mode handlers
├── health.py          # HTTP health-check server
└── utils.py           # Classification, download, formatting helpers

tests/
├── test_app.py            # App creation, env validation, telemetry tests
├── test_bot_filtering.py  # Bot message filtering tests (own vs other bot_ids)
├── test_handlers.py       # Handler tests (mock aristotlelib + Slack)
├── test_health.py         # Health endpoint tests
└── test_utils.py          # Classification, formatting, file reading tests
```

## Testing Notes

- Tests mock `aristotlelib.Project.prove_from_file` and Slack's `say`/`client`
- `create_app()` accepts `token_verification_enabled=False` for testing (skips `auth.test` API call)
- Integration test stubs exist in `test_handlers.py` — they require live credentials and are skipped by default
- All async tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Test imports use the canonical `aristotlebot` package path (not `src.aristotlebot`)
- Package must be installed (`pip install -e .`) for tests and module invocation to work

## Diagnosing "Zero Events" Problem

The most likely root cause is that the Slack app doesn't have Event Subscriptions properly configured:

1. Event Subscriptions must be **enabled** (toggled ON) in the Slack API dashboard
2. Bot must be subscribed to `app_mention`, `message.channels`, and `message.im` events
3. Bot must have the required scopes: `app_mentions:read`, `chat:write`, `channels:read`, `channels:history`, `im:history`
4. Bot must be **invited to the channel** (`/invite @aristotlebot`)
5. After changing scopes/events, the app may need to be **reinstalled** to the workspace

Use `LOG_LEVEL=DEBUG` and check the health endpoint (`curl localhost:8080/health`) to verify whether events are arriving.
