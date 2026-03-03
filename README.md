# Aristotle Slack Bot

A Slack bot that wraps [Aristotle Agent v2](https://aristotle.ai) for Lean theorem proving. Send `.lean` files, URLs, or natural language prompts, and the bot submits them to Aristotle and posts the results back in-thread.

## Features

- **`.lean` file uploads** — Upload a `.lean` file to the bot. It downloads the file, submits it to Aristotle in formal mode, and posts the proof back in-thread.
- **URLs to `.lean` files** — Paste a URL ending in `.lean` (e.g., from GitHub). The bot downloads and processes it the same as an upload.
- **Natural language** — Send any other message and the bot submits it to Aristotle in informal mode (e.g., "Prove that 1 + 1 = 2").

## Setup

### Prerequisites

- Python 3.11+
- A [Slack app](https://api.slack.com/apps) with Socket Mode enabled
- An [Aristotle API key](https://aristotle.ai)

### Slack App Configuration

1. Create a new Slack app at https://api.slack.com/apps
2. Enable **Socket Mode** and generate an **App-Level Token** (`xapp-…`) with `connections:write` scope
3. Under **OAuth & Permissions**, add these Bot Token Scopes:
   - `chat:write`
   - `files:read`
   - `reactions:write`
   - `app_mentions:read`
   - `im:history` (for DMs)
   - `channels:history` (for channel messages)
4. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`)
5. Enable **Event Subscriptions** and subscribe to:
   - `message.im`
   - `message.channels`
   - `app_mention`

### Environment Variables

| Variable           | Description                                 | Required |
| ------------------ | ------------------------------------------- | -------- |
| `SLACK_BOT_TOKEN`  | Bot User OAuth Token (`xoxb-…`)             | Yes      |
| `SLACK_APP_TOKEN`  | App-Level Token (`xapp-…`) for Socket Mode  | Yes      |
| `ARISTOTLE_API_KEY`| API key for aristotlelib                    | Yes      |
| `LOG_LEVEL`        | Logging level (default: `INFO`)             | No       |

### Install & Run

```bash
# Clone the repo
git clone https://github.com/eastseymour/aristotlebot-slack.git
cd aristotlebot-slack

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export ARISTOTLE_API_KEY="..."

# Run the bot
python main.py
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
- ⏳ A hourglass reaction while processing
- 📝 A progress message
- ✅ The completed proof in a Lean code block, or ❌ an error message

## Development

### Run Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

### Project Structure

```
aristotlebot-slack/
├── main.py                    # Entry point
├── src/aristotlebot/
│   ├── __init__.py
│   ├── app.py                 # Slack Bolt app factory + Socket Mode startup
│   ├── handlers.py            # Message handlers for the three input modes
│   └── utils.py               # File download, message classification, formatting
├── tests/
│   ├── test_app.py            # App creation and env validation tests
│   ├── test_handlers.py       # Handler tests (mocked aristotlelib + Slack)
│   └── test_utils.py          # Utils tests (classification, formatting, download)
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
└── CLAUDE.md
```

## License

Internal use.
