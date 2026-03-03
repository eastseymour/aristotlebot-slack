#!/usr/bin/env python3
"""Entry point for the Aristotle Slack bot.

Reads configuration from environment variables and starts the Socket Mode handler.

Required environment variables:
    SLACK_BOT_TOKEN  — Bot User OAuth Token (xoxb-…)
    SLACK_APP_TOKEN  — App-Level Token (xapp-…)
    ARISTOTLE_API_KEY — API key for aristotlelib
"""

from __future__ import annotations

import logging
import os
import sys

# ---------------------------------------------------------------------------
# Validate required env vars up-front (fail fast)
# ---------------------------------------------------------------------------

_REQUIRED_ENV_VARS = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ARISTOTLE_API_KEY")


def _validate_env() -> None:
    """Assert all required environment variables are set.

    Raises:
        SystemExit if any are missing.
    """
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(
            f"ERROR: Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    """Configure logging, validate environment, and start the bot."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _validate_env()

    # Import after env validation so modules can rely on env vars existing
    from src.aristotlebot.app import create_app, start_socket_mode

    app = create_app()
    start_socket_mode(app)


if __name__ == "__main__":
    main()
