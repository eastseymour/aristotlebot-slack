# Aristotle Slack Bot

A Slack bot that wraps [Aristotle Agent v2](https://aristotle.ai) for Lean theorem proving. Send `.lean` files, URLs, or natural language prompts, and the bot submits them to Aristotle and posts the results back in-thread.

## Features

- **`.lean` file uploads** — Upload a `.lean` file to the bot. It downloads the file, submits it to Aristotle in formal mode, and posts the proof back as a downloadable `.lean` file attachment.
- **URLs to `.lean` files** — Paste a URL ending in `.lean` (e.g., from GitHub). The bot downloads and processes it the same as an upload. GitHub blob URLs (e.g. `https://github.com/owner/repo/blob/main/File.lean`) are automatically converted to raw content URLs. Slack's angle-bracket URL wrapping (e.g. `<https://example.com/file.lean>`) is handled automatically. For GitHub URLs, the bot also resolves `import` statements and fetches dependency files from the same repo.
- **Natural language** — Send any other message and the bot submits it to Aristotle in informal mode (e.g., "Prove that 1 + 1 = 2").
- **Import resolution** — When processing `.lean` files (via URL or upload), the bot parses Lean 4 `import` statements, fetches local dependencies from the same GitHub repository, and includes them as context. External packages (Mathlib, Std, VCVio, CompPoly, etc.) are reported but not fetched. Resolution is recursive with configurable depth (default: 10 levels) and file count (default: 50 files) limits. Supports `refs/heads/BRANCH` and `refs/tags/TAG` style GitHub URLs.
- **Lean 4 playground links** — For successful proofs, the bot generates a [Lean 4 playground](https://live.lean-lang.org/) link so users can interactively verify the code in their browser. The link is included in the Slack response alongside the file attachment.
- **Solution file attachments** — Completed proofs are uploaded as `.lean` file attachments (not inline code blocks). The message contains a brief summary with ✅/❌ status, theorem name, and description. Filenames are derived from the theorem name when possible (e.g., `Nat.add_comm.lean`).
- **Smart bot filtering** — Only filters the bot's own messages to prevent feedback loops. Messages from other bots/apps (e.g., Klaw) are processed normally. The bot's identity is discovered dynamically at startup via `auth.test`.
- **Health check endpoint** — HTTP health check on port 8080 reporting Socket Mode connection status, event counts, and registered listeners.
- **Diagnostic logging** — Verbose logging of all incoming events with raw payloads at DEBUG level for troubleshooting.

## Setup

### Prerequisites

- Python 3.11+
- A [Slack app](https://api.slack.com/apps) with Socket Mode enabled
- An [Aristotle API key](https://aristotle.ai)

### Slack App Configuration

> **This is the most common cause of "zero events received".** If the bot connects to Socket Mode successfully but never receives any events, verify every step below.

1. **Create a new Slack app** at https://api.slack.com/apps
2. **Enable Socket Mode**:
   - Go to **Socket Mode** in the sidebar and toggle it ON
   - Generate an **App-Level Token** (`xapp-...`) with the `connections:write` scope
3. **Enable Event Subscriptions**:
   - Go to **Event Subscriptions** in the sidebar and toggle it ON
   - Under **Subscribe to bot events**, add:
     - `app_mention` — fires when someone @-mentions the bot
     - `message.channels` — fires for messages in public channels the bot is in
     - `message.im` — fires for direct messages to the bot
   - **Save Changes** (this is easy to forget!)
4. **Set Bot Token Scopes** (under **OAuth & Permissions**):
   - `app_mentions:read` — required for `app_mention` events
   - `chat:write` — required to post messages
   - `channels:read` — required to see channel info
   - `channels:history` — required for `message.channels` events
   - `im:history` — required for `message.im` events
   - `files:read` — required to download uploaded files
   - `files:write` — required to upload solution `.lean` files as attachments
   - `reactions:write` — required to add/remove emoji reactions
5. **Install the app** to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`)
6. **Invite the bot to channels**: The bot must be a member of any channel where it should receive events. Use `/invite @aristotlebot` in each channel.

### Troubleshooting: Bot Receives Zero Events

If the bot connects via Socket Mode but receives no events:

1. **Check Event Subscriptions**: Go to https://api.slack.com/apps → your app → Event Subscriptions. Ensure it's toggled ON and the bot events (`app_mention`, `message.channels`, `message.im`) are listed.
2. **Check the health endpoint**: `curl http://localhost:8080/health` — look at `events.total_received` and `registered_listeners`.
3. **Enable DEBUG logging**: Set `LOG_LEVEL=DEBUG` to see raw event payloads. Lines prefixed with `[DIAG]` show exactly what events arrive.
4. **Invite the bot to the channel**: The bot must be a member of channels to receive `message.channels` events.
5. **Reinstall the app**: After changing scopes or event subscriptions, you may need to reinstall the app to your workspace.

### Environment Variables

| Variable             | Description                                 | Required |
| -------------------- | ------------------------------------------- | -------- |
| `SLACK_BOT_TOKEN`    | Bot User OAuth Token (`xoxb-...`)           | Yes      |
| `SLACK_APP_TOKEN`    | App-Level Token (`xapp-...`) for Socket Mode| Yes      |
| `ARISTOTLE_API_KEY`  | API key for aristotlelib                    | Yes      |
| `LOG_LEVEL`          | Logging level (default: `INFO`)             | No       |
| `HEALTH_CHECK_PORT`  | Health server port (default: `8080`)        | No       |

### Install & Run

```bash
# Clone the repo
git clone https://github.com/eastseymour/aristotlebot-slack.git
cd aristotlebot-slack

# Install dependencies
pip install -r requirements.txt

# Install the package (required for module imports)
pip install -e .

# Set environment variables
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export ARISTOTLE_API_KEY="..."

# Run the bot
python -m aristotlebot          # preferred
python main.py                  # also works
```

### Docker

```bash
docker build -t aristotlebot-slack .
docker run -e SLACK_BOT_TOKEN -e SLACK_APP_TOKEN -e ARISTOTLE_API_KEY aristotlebot-slack
```

## Usage

### Direct Messages

Send any message directly to the bot:

- **Natural language**: `Prove that for all natural numbers n, n + 0 = n`
- **URL**: `https://raw.githubusercontent.com/user/repo/main/MyTheorem.lean`
- **File**: Upload a `.lean` file to the DM

### Channel @-mentions

Mention the bot in a channel:

```
@aristotlebot Prove that the square root of 2 is irrational
```

### Response Format

The bot responds in-thread with:
- An hourglass reaction while processing
- A progress message
- A brief summary: ✅ or ❌ prefix, theorem name (if detected), and a one-line description
- The completed proof as a downloadable `.lean` file attachment (not inline code)
- A [Lean 4 playground](https://live.lean-lang.org/) link for interactive verification (on successful proofs)
- On error: the error message in the summary text (no file attachment)

### Health Check

The bot exposes an HTTP health endpoint (default: port 8080):

```bash
curl http://localhost:8080/health
```

Example response:

```json
{
  "status": "ok",
  "uptime_seconds": 3600.5,
  "socket_mode_connected": true,
  "events": {
    "total_received": 42,
    "message_events": 30,
    "app_mention_events": 10,
    "ignored_events": 2
  },
  "last_event": {
    "timestamp_iso": "2026-03-04T12:00:00Z",
    "seconds_ago": 5.2
  },
  "registered_listeners": ["message", "app_mention"]
}
```

## Development

### Run Tests

```bash
pip install -r requirements-dev.txt
pip install -e .
python3 -m pytest tests/ -v
```

### Project Structure

```
aristotlebot-slack/
├── main.py                    # Entry point (legacy)
├── src/aristotlebot/
│   ├── __init__.py
│   ├── __main__.py            # python -m aristotlebot entry point
│   ├── app.py                 # Slack Bolt app factory + Socket Mode startup + telemetry
│   ├── handlers.py            # Message handlers for the three input modes + import resolution
│   ├── health.py              # HTTP health-check server (port 8080)
│   ├── lean_imports.py        # Lean 4 import parsing and dependency resolution
│   ├── playground.py          # Lean 4 playground link generation (LZ-String encoding)
│   └── utils.py               # File download/upload, message classification, formatting helpers
├── tests/
│   ├── test_app.py            # App creation, env validation, telemetry tests
│   ├── test_bot_filtering.py  # Bot message filtering tests (own vs other bot_ids)
│   ├── test_handlers.py       # Handler tests (mocked aristotlelib + Slack + imports)
│   ├── test_health.py         # Health endpoint tests
│   ├── test_lean_imports.py   # Import parsing, resolution, and context tests
│   ├── test_playground.py     # Playground URL generation + round-trip encoding tests
│   └── test_utils.py          # Utils tests (classification, formatting, download)
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
└── CLAUDE.md
```

## Production Deployment

The bot is deployed as a **systemd service** on a GCP Compute Engine VM:

| Property        | Value                                            |
| --------------- | ------------------------------------------------ |
| **VM**          | `klaw-controller` (e2-medium, Ubuntu 24.04)      |
| **Zone**        | `us-central1-a`                                  |
| **GCP Project** | `klaw-488307`                                    |
| **Service**     | `aristotlebot.service` (systemd, auto-restart)   |
| **Health**      | `http://localhost:8080/health`                   |
| **Logs**        | `journalctl -u aristotlebot.service -f`          |

See [`reports/ari-10-deployment-investigation.md`](reports/ari-10-deployment-investigation.md) for the full deployment investigation report and architecture diagram.

## License

Internal use.
