# Aristotle Slack Bot

A Slack bot that wraps [Aristotle Agent v2](https://aristotle.ai) for Lean theorem proving. Send `.lean` files, URLs, or natural language prompts, and the bot submits them to Aristotle and posts the results back in-thread.

## Features

- **`.lean` file uploads** — Upload a `.lean` file to the bot. It downloads the file, submits it to Aristotle in formal mode, and posts the proof back in-thread.
- **URLs to `.lean` files** — Paste a URL ending in `.lean` (e.g., from GitHub). The bot downloads and processes it the same as an upload.
- **Natural language** — Send any other message and the bot submits it to Aristotle in informal mode (e.g., "Prove that 1 + 1 = 2").
- **Health check** — HTTP endpoint on port 8080 reports connection status, event counts, and registered listeners.
- **Diagnostic logging** — Verbose event-level logging to diagnose Socket Mode delivery issues.

## Setup

### Prerequisites

- Python 3.11+
- A [Slack app](https://api.slack.com/apps) with Socket Mode enabled
- An [Aristotle API key](https://aristotle.ai)

### Slack App Configuration (CRITICAL)

> **If the bot connects but receives zero events**, this is almost certainly the problem.
> The Slack API dashboard must have Event Subscriptions configured correctly.

1. Go to https://api.slack.com/apps and select your app.

2. **Socket Mode** (left sidebar):
   - Toggle **Enable Socket Mode** to ON.
   - Generate an **App-Level Token** with the `connections:write` scope.
   - Copy this token — it becomes `SLACK_APP_TOKEN` (`xapp-…`).

3. **OAuth & Permissions** (left sidebar) → **Bot Token Scopes**:
   | Scope | Purpose |
   |-------|---------|
   | `app_mentions:read` | Receive @-mention events |
   | `chat:write` | Post messages/replies |
   | `channels:read` | List channels the bot is in |
   | `channels:history` | Read channel message history (required for `message.channels` events) |
   | `files:read` | Download uploaded `.lean` files |
   | `im:history` | Read DM history (required for `message.im` events) |
   | `reactions:write` | Add/remove hourglass reactions |

4. **Install the app** to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`).

5. **Event Subscriptions** (left sidebar):
   - Toggle **Enable Events** to ON.
   - Under **Subscribe to bot events**, add **ALL THREE** of these:
     | Bot Event | Purpose |
     |-----------|---------|
     | `app_mention` | Fires when someone @-mentions the bot in a channel |
     | `message.channels` | Fires when a message is posted in a public channel the bot is in |
     | `message.im` | Fires when a DM is sent to the bot |
   - Click **Save Changes** at the bottom.

6. **Invite the bot to channels**:
   - In Slack, go to the channel where you want the bot active.
   - Type `/invite @aristotlebot` (or whatever your bot is named).
   - The bot **will not receive events** from channels it hasn't been invited to.

### Troubleshooting: Zero Events

If the bot connects (you see `Starting Aristotle Slack bot in Socket Mode` in logs) but no events arrive:

1. **Check Event Subscriptions** — The #1 cause. Go to your Slack app's **Event Subscriptions** page and verify all three bot events (`app_mention`, `message.channels`, `message.im`) are listed.
2. **Check the health endpoint** — `curl http://localhost:8080/health` shows `total_events: 0` if no events are being delivered.
3. **Check bot is in the channel** — The bot must be invited to each channel with `/invite @botname`.
4. **Check LOG_LEVEL=DEBUG** — Set `LOG_LEVEL=DEBUG` to see the full Socket Mode envelope. The middleware logs every incoming event.
5. **Reinstall the app** — After changing scopes or event subscriptions, you may need to reinstall the app to your workspace (OAuth & Permissions → Install to Workspace).
6. **Verify tokens** — `SLACK_BOT_TOKEN` must be `xoxb-...` (Bot User OAuth Token), not `xoxp-...` (User Token). `SLACK_APP_TOKEN` must be `xapp-...` (App-Level Token).

### Environment Variables

| Variable            | Description                                       | Required |
| ------------------- | ------------------------------------------------- | -------- |
| `SLACK_BOT_TOKEN`   | Bot User OAuth Token (`xoxb-…`)                   | Yes      |
| `SLACK_APP_TOKEN`   | App-Level Token (`xapp-…`) for Socket Mode        | Yes      |
| `ARISTOTLE_API_KEY` | API key for aristotlelib                          | Yes      |
| `LOG_LEVEL`         | Logging level (default: `INFO`)                   | No       |
| `HEALTH_CHECK_PORT` | Port for health-check HTTP server (default: `8080`, `0` to disable) | No |

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

### systemd Service

```ini
[Unit]
Description=Aristotle Slack Bot
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m aristotlebot
Environment=SLACK_BOT_TOKEN=xoxb-...
Environment=SLACK_APP_TOKEN=xapp-...
Environment=ARISTOTLE_API_KEY=...
Environment=LOG_LEVEL=INFO
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
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
- The completed proof in a Lean code block, or an error message

### Health Check

```bash
curl http://localhost:8080/health
```

Returns JSON:
```json
{
  "status": "ok",
  "socket_mode": "running",
  "events": {
    "total_events": 42,
    "last_event_ts": 1709567890.5,
    "last_event_age_seconds": 12.3,
    "events_by_type": {
      "message": 30,
      "app_mention": 12
    },
    "uptime_seconds": 3600.0
  },
  "registered_listeners": [
    "handle_message_event",
    "handle_app_mention"
  ]
}
```

## Development

### Run Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

### Project Structure

```
aristotlebot-slack/
├── main.py                    # Entry point (legacy)
├── src/aristotlebot/
│   ├── __init__.py
│   ├── __main__.py            # python -m aristotlebot entry point
│   ├── app.py                 # Slack Bolt app factory + Socket Mode startup
│   ├── handlers.py            # Message handlers for the three input modes
│   ├── healthcheck.py         # HTTP health-check server (port 8080)
│   └── utils.py               # File download, message classification, formatting
├── tests/
│   ├── test_app.py            # App creation and env validation tests
│   ├── test_handlers.py       # Handler tests (mocked aristotlelib + Slack)
│   ├── test_healthcheck.py    # Health-check server tests
│   └── test_utils.py          # Utils tests (classification, formatting, download)
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
└── CLAUDE.md
```

## License

Internal use.
