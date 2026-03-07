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
Slack event → app.py (classify + telemetry) → handlers.py (dispatch) → aristotlelib
                ↓                                                           ↓
           health.py (HTTP /health)                              ┌──────────┴──────────┐
                                                                 ↓                     ↓
                                                          summary message      .lean file upload
                                                          (via say())       (via Slack file API)
```

1. **app.py** — Creates a Slack Bolt `App` with sync event listeners. At startup, calls `auth.test` to dynamically discover the bot's own `bot_id` — only messages from this bot_id are filtered (other bots' messages are processed normally). Messages are classified by `utils.classify_message()` into one of three `MessageKind` variants, then dispatched to the async `handle_message()` via `asyncio.run()`. Maintains an `EventTelemetry` singleton that tracks all received events.

2. **handlers.py** — Three handler functions (file upload, URL, natural language). Each:
   - Adds a hourglass reaction
   - Downloads/prepares input
   - Resolves Lean 4 imports and fetches dependency files (ARI-6, for URL and file upload handlers)
   - When context files are present, uses the lower-level `Project.create()` + `add_context()` + `solve()` API with explicit `project_root=tmp_dir` to ensure correct relative paths.  Without context, uses the simpler `prove_from_file()` API.
   - Detects Aristotle API errors in output files via `_detect_api_error()` (ARI-14) — if the output contains sentinel phrases like "Aristotle encountered an error", it's treated as FAILED instead of silently returned as a solution
   - Posts a brief summary in-thread (✅/❌ + theorem name + one-line description)
   - Uploads the solution as a `.lean` file attachment (via `upload_slack_file()`)
   - Generates a Lean 4 playground link (via `lean_playground_url()`) for interactive verification, formatted as a Slack mrkdwn hyperlink `<URL|🔗 Open in Lean Playground>` to hide the long encoded URL (ARI-13)
   - Cleans up temp files in `finally` blocks
   - `_post_result()` — Central helper that posts both the summary, file attachment, and playground link. Falls back gracefully if file upload fails.
   - `_detect_api_error()` — Sentinel-based detection of Aristotle errors in output files (ARI-14). Checks for phrases like "Aristotle encountered an error", "Internal server error", etc. Returns the first line of the error or None.
   - `_resolve_imports_safe()` — Error-handling wrapper for import resolution. Never raises; returns empty `ResolvedImports` on failure.
   - `_write_context_files()` — Writes resolved dependency files to disk for aristotlelib.
   - `_report_import_status()` — Posts import resolution status (resolved count, external deps, warnings) to the Slack thread.

3. **utils.py** — Pure helpers:
   - `classify_message()` — Classifies Slack events into `MessageKind` enum. Handles Slack's angle-bracket URL wrapping (`<https://...>`) by stripping brackets before matching.
   - `_strip_slack_angle_brackets()` — Preprocesses Slack event text to unwrap `<URL>` and `<URL|label>` patterns into bare URLs. Leaves non-URL angle brackets (e.g. `<@U12345>`) untouched.
   - `download_slack_file()` / `download_url()` — Async file downloaders. `download_url()` automatically converts GitHub blob URLs to `raw.githubusercontent.com` URLs via `_github_blob_to_raw()`.
   - `_github_blob_to_raw()` — Converts `github.com/.../blob/...` URLs to `raw.githubusercontent.com/...` so the raw file is downloaded instead of the HTML page view.
   - `format_result_summary()` — Brief summary for Slack (no inline code). Extracts theorem name from solution text.
   - `format_result_message()` — **Legacy** formatter that embeds code inline. Kept for backward compatibility.
   - `upload_slack_file()` — Two-step Slack file upload via `files.getUploadURLExternal` + `files.completeUploadExternal`.
   - `AristotleResult` — NamedTuple for structured result passing between Aristotle submission helpers and result posting.
   - `_extract_theorem_name()` — Extracts first theorem/lemma/def name from Lean source code.
   - `_make_solution_filename()` — Generates descriptive `.lean` filenames from solution text.
   - `read_solution_file()` — Reads `.lean` or `.tar.gz` solution files

4. **lean_imports.py** — Lean 4 import parsing and recursive dependency resolution (ARI-6, ARI-14):
   - `parse_lean_imports()` — Regex-based parser that extracts `import` statements from Lean 4 source, classifying each as LOCAL or EXTERNAL via the `ImportKind` enum.
   - `import_to_file_path()` — Converts dotted module paths to POSIX file paths (e.g. `ArkLib.Data.Fin.Basic` → `ArkLib/Data/Fin/Basic.lean`).
   - `extract_github_repo_info()` — Extracts owner, repo, and ref from GitHub raw/blob URLs. Handles `refs/heads/BRANCH` and `refs/tags/TAG` URL patterns. Returns `GitHubRepoInfo` or `None`.
   - `resolve_imports()` — Recursively resolves same-repo imports by fetching files from GitHub. Uses an **allowlist approach** (ARI-14): only imports whose top-level module matches `repo_info.repo` are fetched. All other imports are external by definition. Bounded by `MAX_DEPTH=10` and `MAX_FILES=50`.
   - `_resolve_recursive()` — Internal recursive resolver. Takes a `project_name` parameter (derived from `repo_info.repo`) and only fetches imports where `imp.top_level_package == project_name`. This prevents 404 errors from trying to fetch Mathlib/Std/VCVio/etc. from the source repo.
   - `format_import_context()` — Formats resolved files into a context string for the LLM.
   - Key types: `LeanImport` (frozen dataclass), `GitHubRepoInfo` (frozen dataclass), `ResolvedImports` (discriminated result with resolved files and unresolved imports), `UnresolvedImport`.
   - Invariants: recursive resolution always terminates (bounded depth + file count); cycle detection via visited set; only same-repo imports fetched (allowlist, ARI-14); all network errors caught and degraded gracefully.
   - `EXTERNAL_PACKAGES` frozenset is kept for `parse_lean_imports()` classification but is **not** used by the resolver — the resolver uses the repo name as the authoritative filter.

6. **playground.py** — Lean 4 playground link generation:
   - `lean_playground_url()` — Generates a `live.lean-lang.org` URL from Lean source code using LZ-String base64 compression (`codez=` URL fragment parameter). Returns `None` for empty/whitespace input.
   - `decode_playground_url()` — Inverse: decodes a `codez=` parameter back to Lean source. Used for testing round-trip correctness.
   - Encoding scheme: `LZString.compressToBase64(code).rstrip('=')` → placed in URL hash fragment.
   - Reference: lean4web source at `client/src/editor/code-atoms.ts` in `leanprover-community/lean4web`.

5. **health.py** — HTTP health-check server running on a daemon thread (default port 8080). Reports:
   - Socket Mode connection status
   - Total events received, broken down by type
   - Last event timestamp
   - Registered event listeners

### Key Design Decisions

- **File attachments over inline code (LEA-24)**: Solution code is uploaded as `.lean` file attachments via Slack's two-step external upload API (`files.getUploadURLExternal` → POST to presigned URL → `files.completeUploadExternal`). The message body contains only a brief summary with ✅/❌ prefix, theorem name, and one-line description. This avoids cluttering threads with large code blocks and makes solutions downloadable.
- **AristotleResult NamedTuple**: Structured result type separates Aristotle submission from result formatting/posting. Invariant: `solution_text` and `error` are never both non-None.
- **Graceful file upload fallback**: If the file upload fails (e.g. missing `files:write` scope), the summary is still posted with a note that the file upload failed. The bot never silently loses results.
- **Sync Bolt + async handlers**: We use sync `App` (not `AsyncApp`) because Socket Mode only works reliably with sync Bolt. Async aristotlelib calls run inside `asyncio.run()`.
- **`say()` is synchronous**: In the sync Bolt context, `say` and `client` are sync. Handlers do NOT `await` them. `upload_slack_file()` is also synchronous (uses `client` methods + `urllib.request`).
- **MessageKind enum**: Discriminated union prevents invalid classification states.
- **GitHub blob URL normalization**: `download_url()` calls `_github_blob_to_raw()` to convert GitHub blob view URLs (`github.com/{owner}/{repo}/blob/{ref}/{path}`) to raw content URLs (`raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}`). Without this, the bot downloads the HTML page instead of the actual `.lean` file, causing Aristotle to fail. The conversion is transparent to callers.
- **Slack angle-bracket stripping**: Slack wraps URLs in `<>` in event text (e.g. `<https://example.com/file.lean>`). The `_strip_slack_angle_brackets()` helper normalizes these before URL matching. This is done as a preprocessing step rather than complicating the URL regex, keeping concerns separated.
- **Import resolution (ARI-6, ARI-14)**: When a `.lean` file is submitted (via URL or upload), the bot parses its `import` statements, fetches dependency files from the same GitHub repo, and passes them as `context_file_paths` to aristotlelib. This gives the LLM visibility into types, theorems, and definitions from imported files. Import resolution is best-effort: failures degrade gracefully (the file is still submitted without context).
- **Allowlist import filtering (ARI-14)**: The resolver uses an allowlist approach: only imports whose top-level module matches the repo name (`repo_info.repo`) are fetched. For example, when processing a file from `Verified-zkEVM/ArkLib`, only `ArkLib.*` imports are fetched; `Mathlib.*`, `Std.*`, `VCVio.*`, `CompPoly.*` etc. are all treated as external. This replaced the previous blocklist approach (static `EXTERNAL_PACKAGES` frozenset) which would miss unknown external packages and trigger 404 errors. The `EXTERNAL_PACKAGES` frozenset is retained for `parse_lean_imports()` classification only, not for resolution decisions.
- **API error detection (ARI-14)**: When Aristotle returns an error in the output file (e.g. "Aristotle encountered an error processing this file"), the bot now detects this via sentinel string matching (`_detect_api_error()`) and reports it as a failure with `:x:` emoji, rather than silently presenting the error text as a "solution". This applies to both formal and informal mode handlers.
- **Playground link generation (ARI-13)**: For successful proofs, the bot generates a `live.lean-lang.org` playground URL so users can interactively verify the code in their browser. The encoding uses LZ-String base64 compression (reverse-engineered from the lean4web source). The link is formatted using Slack mrkdwn syntax `<URL|🔗 Open in Lean Playground>` so the long encoded URL is hidden behind a clean clickable hyperlink. Invariant: the raw URL is never displayed outside `<>` brackets.
- **Temp dir cleanup**: Always in `finally` blocks. Never leak temp files.
- **Dynamic bot_id discovery**: At startup, `create_app()` calls `auth.test` to discover the bot's own `bot_id`. This is stored in `_own_bot_id` and used to filter ONLY the bot's own messages. Messages from other bots/apps (like Klaw) are processed normally. The bot_id is never hardcoded.
- **`_is_own_bot_message()` helper**: Encapsulates the bot message filtering logic. Returns `True` only when the event's `bot_id` matches our own. When `_own_bot_id` is None (e.g., in tests), it conservatively returns `False` (never drops messages).
- **EventTelemetry singleton**: Module-level dataclass shared between app.py and health.py. All event counts are recorded here for observability.
- **Diagnostic logging**: All event handlers log with `[DIAG]` prefix at INFO level. Raw payloads logged at DEBUG level. Set `LOG_LEVEL=DEBUG` for full visibility.

### Slack File Upload API Pattern

```python
# Two-step external file upload (used by upload_slack_file):
# Step 1: Get presigned upload URL
resp = client.files_getUploadURLExternal(filename="solution.lean", length=len(content_bytes))
upload_url = resp["upload_url"]
file_id = resp["file_id"]

# Step 2: POST file content to presigned URL
urllib.request.urlopen(Request(upload_url, data=content_bytes, method="POST"))

# Step 3: Finalize and share in channel/thread
client.files_completeUploadExternal(
    files=[{"id": file_id, "title": "solution.lean"}],
    channel_id=channel,
    thread_ts=thread_ts,
)
```

**Required Slack scope**: `files:write` (in addition to existing scopes).

### aristotlelib API Patterns

```python
# Formal mode WITH context files (lower-level API for correct file paths)
# IMPORTANT: We use the lower-level API instead of prove_from_file when context
# files are present, because prove_from_file auto-computes project_root as the
# common parent of context files, which gives wrong relative paths.
project = await Project.create(
    project_input_type=ProjectInputType.FORMAL_LEAN,
    validate_lean_project_root=False,
)
await project.add_context(
    context_file_paths=[path_to_dep1, path_to_dep2],
    validate_lean_project_root=False,
    project_root=tmp_dir,  # Ensures correct relative paths like ArkLib/Data/Fin/Basic.lean
)
await project.solve(input_file_path=path_to_lean)
result_path = await project.wait_for_completion(output_file_path=output_path)

# Formal mode WITHOUT context files (simpler high-level API)
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
├── handlers.py        # Three input mode handlers + import resolution + _post_result helper
├── health.py          # HTTP health-check server
├── lean_imports.py    # Lean 4 import parsing and recursive dependency resolution (ARI-6)
├── playground.py      # Lean 4 playground link generation (LZ-String encoding)
└── utils.py           # Classification, download, formatting, file upload helpers

tests/
├── test_app.py            # App creation, env validation, telemetry tests
├── test_bot_filtering.py  # Bot message filtering tests (own vs other bot_ids)
├── test_handlers.py       # Handler tests (mock aristotlelib + Slack + imports + playground links)
├── test_health.py         # Health endpoint tests
├── test_lean_imports.py   # Import parsing, resolution, context formatting tests (ARI-6)
├── test_playground.py     # Playground URL generation + round-trip encoding tests
└── test_utils.py          # Classification, formatting, file upload, file reading tests
```

## Testing Notes

- Tests mock `aristotlelib.Project.prove_from_file` and Slack's `say`/`client`
- File upload tests mock `upload_slack_file()` at the handler level and `urllib.request.urlopen` at the utils level
- `create_app()` accepts `token_verification_enabled=False` for testing (skips `auth.test` API call)
- Integration test stubs exist in `test_handlers.py` — they require live credentials and are skipped by default
- All async tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Test imports use the canonical `aristotlebot` package path (not `src.aristotlebot`)
- Package must be installed (`pip install -e .`) for tests and module invocation to work

## Diagnosing "Zero Events" Problem

The most likely root cause is that the Slack app doesn't have Event Subscriptions properly configured:

1. Event Subscriptions must be **enabled** (toggled ON) in the Slack API dashboard
2. Bot must be subscribed to `app_mention`, `message.channels`, and `message.im` events
3. Bot must have the required scopes: `app_mentions:read`, `chat:write`, `channels:read`, `channels:history`, `im:history`, `files:write`, `files:read`
4. Bot must be **invited to the channel** (`/invite @aristotlebot`)
5. After changing scopes/events, the app may need to be **reinstalled** to the workspace

Use `LOG_LEVEL=DEBUG` and check the health endpoint (`curl localhost:8080/health`) to verify whether events are arriving.

## Production Deployment (ARI-10)

The bot runs as a **systemd service** on a GCP Compute Engine VM. See `reports/ari-10-deployment-investigation.md` for the full investigation report.

### Quick Reference

| Property          | Value                                                |
| ----------------- | ---------------------------------------------------- |
| **VM**            | `klaw-controller`                                    |
| **Zone**          | `us-central1-a`                                      |
| **GCP Project**   | `klaw-488307`                                        |
| **Machine Type**  | `e2-medium` (Ubuntu 24.04)                           |
| **Internal IP**   | `10.0.0.2`                                           |
| **Service**       | `aristotlebot.service` (systemd)                     |
| **Working Dir**   | `/var/lib/openclaw/agents/aristotlebot-slack`         |
| **Entry Point**   | `.venv/bin/python main.py`                           |
| **Secrets**       | `/etc/klaw/aristotlebot.env` (+ GCP Secret Manager)  |
| **Health Check**  | `http://localhost:8080/health`                       |
| **Logs**          | `journalctl -u aristotlebot.service`                 |

### Service Management

```bash
systemctl status aristotlebot.service    # Check status
systemctl restart aristotlebot.service   # Restart after code update
journalctl -u aristotlebot.service -f    # Follow logs
curl http://localhost:8080/health        # Health check
```

### Deployment / Redeployment Process

There is no CI/CD pipeline. To deploy code changes:
1. SSH into `klaw-controller` (or run from the VM)
2. `cd /var/lib/openclaw/agents/aristotlebot-slack`
3. `git checkout main && git pull origin main` — **must checkout main first** (the working directory may be on a feature branch)
4. `.venv/bin/pip install -e .` (in case of any dependency changes)
5. `sudo systemctl restart aristotlebot.service`
6. `sudo systemctl status aristotlebot.service` — confirm `active (running)`
7. `sudo journalctl -u aristotlebot.service -n 30 --no-pager` — confirm no startup errors
8. `curl http://localhost:8080/health` — confirm `"status": "ok"` and `"socket_mode_connected": true`

⚠️ **Important**: The systemd service runs from the working directory. If the repo is checked out on a feature branch, the service will run that branch's code — not main. Always verify with `git branch --show-current` before restarting.

### Service Configuration

The systemd unit file is at `/etc/systemd/system/aristotlebot.service`:
- **Restart policy**: `on-failure` with 10-second delay
- **Enabled on boot**: Yes
- **Environment**: Loaded from `/etc/klaw/aristotlebot.env`
- **Runs as**: root (recommendation: switch to dedicated user)

## Klaw Worker Architecture (ARI-14)

This bot is deployed and managed by Klaw, which uses a worker-based architecture.
Understanding the worker lifecycle is important when diagnosing deployment issues.

### Worker Status Progression

```
Linear: Todo -> In Progress -> In Review -> Done
                  ^worker       ^verification    ^verified
                   runs          worker runs      & merged
```

### Key Concepts

- **Workers**: Isolated Claude agent sessions that perform tasks (code changes, verification, etc.)
- **Verification workers**: After a regular worker creates a PR and sets the Linear issue to "In Review", a verification worker is spawned to review the code, run tests, and merge the PR
- **Deduplication**: Klaw tracks which Linear issues have active workers via `_linear_issues_with_workers` set and `_get_running_issue_keys()` to prevent duplicate workers
- **Failure tracking**: `_failed_verification_attempts` dict caps verification retries at `MAX_VERIFICATION_ATTEMPTS` (default: 2) to prevent runaway respawn loops (see ARI-14 investigation report)

### Runaway Worker Loop (ARI-14 Bug)

Workers w43-w75 kept respawning for the same verification task because failed verification workers unconditionally cleared the tracking set, allowing the periodic scan to respawn them every 10 minutes. The fix: use a separate failure counter (`_failed_verification_attempts`) as the primary guard, and always clear the tracking set to allow controlled retries. See `reports/ari-14-runaway-worker-loop-investigation.md` for the full root cause analysis.

### Periodic Loops (Klaw Controller)

| Loop | Interval | Purpose |
|------|----------|---------|
| `_message_poll_loop` | 30s | Slack messages |
| `_worker_monitor_loop` | 60s | Worker health + completion |
| `_linear_task_check_loop` | 600s | New work + verification + stale recovery |
| `_budget_poll_loop` | configurable | Budget enforcement |
| `_log_sync_loop` | 300s | Log synchronization |
