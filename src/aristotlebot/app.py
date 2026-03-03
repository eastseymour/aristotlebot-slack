"""Slack Socket Mode bot using slack-bolt.

This module configures the Bolt app, registers message listeners, and provides
the ``create_app`` factory for both production and testing.

Invariants:
    - The app ONLY responds to direct messages and @-mentions (not every channel message).
    - Bot's own messages are ignored to prevent infinite loops.
"""

from __future__ import annotations

import asyncio
import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .handlers import handle_message
from .utils import classify_message

logger = logging.getLogger(__name__)


def create_app(
    bot_token: str | None = None,
    *,
    token_verification_enabled: bool = True,
) -> App:
    """Create and configure a Slack Bolt app.

    Args:
        bot_token: Slack bot token. If None, reads from SLACK_BOT_TOKEN env var.
        token_verification_enabled: Whether to verify the token via auth.test on
            startup. Set to False in tests to avoid network calls.

    Returns:
        Configured Slack Bolt App instance.

    Preconditions:
        - Either *bot_token* is provided or SLACK_BOT_TOKEN env var is set.
    """
    token = bot_token or os.environ["SLACK_BOT_TOKEN"]
    app = App(token=token, token_verification_enabled=token_verification_enabled)

    @app.event("message")
    def handle_message_event(event, say, client):
        """Handle incoming Slack messages.

        Ignores bot messages to prevent feedback loops, then classifies
        and dispatches the message to the appropriate handler.
        """
        # Ignore bot messages and message_changed subtypes
        if event.get("bot_id") or event.get("subtype"):
            return

        classified = classify_message(event)
        logger.info(
            "Classified message as %s in channel %s",
            classified.kind.name,
            event.get("channel"),
        )

        # Run the async handler from the sync Bolt context
        asyncio.run(handle_message(event, say, client, classified))

    @app.event("app_mention")
    def handle_app_mention(event, say, client):
        """Handle @-mentions of the bot in channels.

        Same logic as DMs but triggered via @-mention.
        """
        if event.get("bot_id"):
            return

        classified = classify_message(event)
        logger.info(
            "Classified @-mention as %s in channel %s",
            classified.kind.name,
            event.get("channel"),
        )

        asyncio.run(handle_message(event, say, client, classified))

    return app


def start_socket_mode(app: App, app_token: str | None = None) -> None:
    """Start the bot in Socket Mode.

    Args:
        app: Configured Slack Bolt App.
        app_token: Slack app-level token (xapp-…). If None, reads from SLACK_APP_TOKEN env var.

    Preconditions:
        - Either *app_token* is provided or SLACK_APP_TOKEN env var is set.

    This function blocks until the process is killed.
    """
    token = app_token or os.environ["SLACK_APP_TOKEN"]
    handler = SocketModeHandler(app, token)
    logger.info("Starting Aristotle Slack bot in Socket Mode…")
    handler.start()
