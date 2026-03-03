"""Allow running the bot via ``python -m aristotlebot``.

This module delegates to the main() entry point defined in the
top-level main.py script.
"""

from __future__ import annotations

import logging
import os
import sys

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

    from aristotlebot.app import create_app, start_socket_mode

    app = create_app()
    start_socket_mode(app)


if __name__ == "__main__":
    main()
