"""Slack Socket Mode bot using slack-bolt.

This module configures the Bolt app, registers message listeners, and provides
the ``create_app`` factory for both production and testing.

Invariants:
    - The app ONLY responds to direct messages and @-mentions (not every channel message).
    - Bot's own messages are ignored to prevent infinite loops.
    - Every received event is counted and logged for diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .handlers import handle_message
from .utils import classify_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event telemetry — singleton shared with the health-check server
# ---------------------------------------------------------------------------

@dataclass
class EventTelemetry:
    """Mutable counters tracking events received since process start.

    Invariants:
        - ``total_events`` >= ``message_events`` + ``app_mention_events``
          (total includes events we skip, e.g. bot messages).
        - ``last_event_ts`` is 0.0 until the first event arrives.
    """

    total_events: int = 0
    message_events: int = 0
    app_mention_events: int = 0
    ignored_events: int = 0
    last_event_ts: float = 0.0
    start_ts: float = field(default_factory=time.time)
    registered_listeners: list[str] = field(default_factory=list)

    def record_event(self, kind: str, *, ignored: bool = False) -> None:
        """Record that an event was received.

        Args:
            kind: Event type string (e.g. "message", "app_mention").
            ignored: Whether this event was skipped (bot message, subtype, etc.).
        """
        self.total_events += 1
        self.last_event_ts = time.time()
        if ignored:
            self.ignored_events += 1


# Module-level singleton so health.py can import it.
telemetry = EventTelemetry()


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
        logger.info(
            "[DIAG] Received 'message' event: channel=%s user=%s subtype=%s bot_id=%s",
            event.get("channel"),
            event.get("user"),
            event.get("subtype"),
            event.get("bot_id"),
        )
        logger.debug("[DIAG] Raw message event payload: %s", json.dumps(event, default=str))

        # Ignore bot messages and message_changed subtypes
        if event.get("bot_id") or event.get("subtype"):
            telemetry.record_event("message", ignored=True)
            logger.info(
                "[DIAG] Ignoring message event (bot_id=%s, subtype=%s)",
                event.get("bot_id"),
                event.get("subtype"),
            )
            return

        telemetry.record_event("message")
        telemetry.message_events += 1

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
        logger.info(
            "[DIAG] Received 'app_mention' event: channel=%s user=%s bot_id=%s",
            event.get("channel"),
            event.get("user"),
            event.get("bot_id"),
        )
        logger.debug("[DIAG] Raw app_mention event payload: %s", json.dumps(event, default=str))

        if event.get("bot_id"):
            telemetry.record_event("app_mention", ignored=True)
            logger.info("[DIAG] Ignoring app_mention from bot (bot_id=%s)", event.get("bot_id"))
            return

        telemetry.record_event("app_mention")
        telemetry.app_mention_events += 1

        classified = classify_message(event)
        logger.info(
            "Classified @-mention as %s in channel %s",
            classified.kind.name,
            event.get("channel"),
        )

        asyncio.run(handle_message(event, say, client, classified))

    # Record which listeners are registered for health-check reporting
    telemetry.registered_listeners = ["message", "app_mention"]
    logger.info(
        "[DIAG] Registered event listeners: %s",
        telemetry.registered_listeners,
    )

    return app


def start_socket_mode(app: App, app_token: str | None = None) -> None:
    """Start the bot in Socket Mode.

    Args:
        app: Configured Slack Bolt App.
        app_token: Slack app-level token (xapp-...). If None, reads from SLACK_APP_TOKEN env var.

    Preconditions:
        - Either *app_token* is provided or SLACK_APP_TOKEN env var is set.

    This function blocks until the process is killed.
    """
    token = app_token or os.environ["SLACK_APP_TOKEN"]
    handler = SocketModeHandler(app, token)
    logger.info("[DIAG] Starting Aristotle Slack bot in Socket Mode...")
    logger.info("[DIAG] Registered listeners: %s", telemetry.registered_listeners)
    logger.info(
        "[DIAG] Ensure your Slack app has Event Subscriptions enabled with "
        "bot events: app_mention, message.channels, message.im"
    )
    handler.start()
