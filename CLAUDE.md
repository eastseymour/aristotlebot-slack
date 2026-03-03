# CLAUDE.md — Aristotle Slack Bot

## Quick Reference

```bash
# Run tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_utils.py -v

# Run the bot (requires env vars)
python3 -m aristotlebot              # preferred (module entry point)
python3 main.py                      # also works

# Install the package (required for module imports)
pip install -e .

# Install dependencies
pip install -r requirements.txt          # production
pip install -r requirements-dev.txt      # development (includes pytest)
```

## Architecture

### Message Flow

```
Slack event → app.py (classify) → handlers.py (dispatch) → aristotlelib → Slack thread reply
```

1. **app.py** — Creates a Slack Bolt `App` with sync event listeners. Messages are classified by `utils.classify_message()` into one of three `MessageKind` variants, then dispatched to the async `handle_message()` via `asyncio.run()`.

2. **handlers.py** — Three handler functions (file upload, URL, natural language). Each:
   - Adds a ⏳ reaction
   - Downloads/prepares input
   - Calls `aristotlelib.Project.prove_from_file()`
   - Posts the result in-thread
   - Cleans up temp files in `finally` blocks

3. **utils.py** — Pure helpers:
   - `classify_message()` — Classifies Slack events → `MessageKind` enum
   - `download_slack_file()` / `download_url()` — Async file downloaders
   - `format_result_message()` — Formats Aristotle results for Slack
   - `read_solution_file()` — Reads `.lean` or `.tar.gz` solution files

### Key Design Decisions

- **Sync Bolt + async handlers**: We use sync `App` (not `AsyncApp`) because Socket Mode only works reliably with sync Bolt. Async aristotlelib calls run inside `asyncio.run()`.
- **`say()` is synchronous**: In the sync Bolt context, `say` and `client` are sync. Handlers do NOT `await` them.
- **MessageKind enum**: Discriminated union prevents invalid classification states.
- **Temp dir cleanup**: Always in `finally` blocks. Never leak temp files.

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

| Variable           | Description                                |
| ------------------ | ------------------------------------------ |
| `SLACK_BOT_TOKEN`  | Bot User OAuth Token (`xoxb-…`)            |
| `SLACK_APP_TOKEN`  | App-Level Token (`xapp-…`) for Socket Mode |
| `ARISTOTLE_API_KEY`| API key for aristotlelib                   |
| `LOG_LEVEL`        | Optional. Default: `INFO`                  |

## File Layout

```
src/aristotlebot/
├── __init__.py        # Package version
├── __main__.py        # python -m aristotlebot entry point
├── app.py             # Bolt app factory, event listeners
├── handlers.py        # Three input mode handlers
└── utils.py           # Classification, download, formatting helpers

tests/
├── test_app.py        # App creation, env validation
├── test_handlers.py   # Handler tests (mock aristotlelib + Slack)
└── test_utils.py      # Classification, formatting, file reading tests
```

## Testing Notes

- Tests mock `aristotlelib.Project.prove_from_file` and Slack's `say`/`client`
- `create_app()` accepts `token_verification_enabled=False` for testing (skips `auth.test` API call)
- Integration test stubs exist in `test_handlers.py` — they require live credentials and are skipped by default
- All async tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Test imports use the canonical `aristotlebot` package path (not `src.aristotlebot`)
- Package must be installed (`pip install -e .`) for tests and module invocation to work
