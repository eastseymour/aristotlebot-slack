"""Slack Socket Mode bot using slack-bolt.

This module configures the Bolt app, registers message listeners, and provides
the ``create_app`` factory for both production and testing.

Invariants:
    - The app ONLY responds to direct messages and @-mentions (not every channel message).
    - Only the bot's OWN messages are ignored (by matching its dynamically-discovered bot_id).
    - Messages from other bots/apps are processed normally.
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
# Bot identity — discovered dynamically at startup via auth.test
# ---------------------------------------------------------------------------

_own_bot_id: str | None = None


def get_own_bot_id() -> str | None:
    """Return the bot's own bot_id, discovered at startup.

    Returns None if the bot_id has not been discovered yet (e.g. in tests
    with token_verification_enabled=False).
    """
    return _own_bot_id


def _is_own_bot_message(event: dict) -> bool:
    """Check whether an event was sent by this bot itself.

    Preconditions:
        - ``_own_bot_id`` should be set (via ``auth.test``) before this is called
          in production. In tests where it's None, this returns False (safe default:
          process the message rather than silently dropping it).

    Returns:
        True only if the event's ``bot_id`` matches this bot's own bot_id.
        Returns False if the event has no ``bot_id``, or if the bot_id belongs
        to a different bot/app.
    """
    event_bot_id = event.get("bot_id")
    if not event_bot_id:
        return False
    if _own_bot_id is None:
        # bot_id not yet discovered — conservative: don't filter
        logger.warning(
            "[DIAG] _own_bot_id not set; cannot determine if bot_id=%s is ours. "
            "Processing message to avoid dropping external bot messages.",
            event_bot_id,
        )
        return False
    return event_bot_id == _own_bot_id


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

    At startup (when ``token_verification_enabled=True``), calls ``auth.test``
    to dynamically discover this bot's own ``bot_id``. This bot_id is then used
    to filter only the bot's own messages — messages from other bots/apps are
    processed normally.

    Args:
        bot_token: Slack bot token. If None, reads from SLACK_BOT_TOKEN env var.
        token_verification_enabled: Whether to verify the token via auth.test on
            startup. Set to False in tests to avoid network calls.

    Returns:
        Configured Slack Bolt App instance.

    Preconditions:
        - Either *bot_token* is provided or SLACK_BOT_TOKEN env var is set.

    Postconditions:
        - When ``token_verification_enabled=True``, ``_own_bot_id`` is set to
          the bot's bot_id from auth.test.
    """
    global _own_bot_id

    token = bot_token or os.environ["SLACK_BOT_TOKEN"]
    app = App(token=token, token_verification_enabled=token_verification_enabled)

    # Discover the bot's own bot_id via auth.test so we can filter only
    # our own messages (not messages from other bots/apps).
    if token_verification_enabled:
        try:
            auth_response = app.client.auth_test()
            _own_bot_id = auth_response.get("bot_id")
            logger.info(
                "[DIAG] Discovered own bot_id=%s (user_id=%s, team=%s)",
                _own_bot_id,
                auth_response.get("user_id"),
                auth_response.get("team"),
            )
            assert _own_bot_id, (
                "auth.test returned no bot_id — is this token a bot token?"
            )
        except Exception:
            logger.exception(
                "[DIAG] Failed to call auth.test to discover bot_id. "
                "Bot message filtering will be disabled (all messages processed)."
            )
            _own_bot_id = None
    else:
        logger.info(
            "[DIAG] Token verification disabled; skipping auth.test. "
            "_own_bot_id will not be set."
        )

    @app.event("message")
    def handle_message_event(event, say, client):
        """Handle incoming Slack messages.

        Ignores only this bot's OWN messages (to prevent feedback loops)
        and message_changed subtypes.  Messages from other bots/apps
        (e.g. Klaw) are processed normally.
        """
        logger.info(
            "[DIAG] Received 'message' event: channel=%s user=%s subtype=%s bot_id=%s",
            event.get("channel"),
            event.get("user"),
            event.get("subtype"),
            event.get("bot_id"),
        )
        logger.debug("[DIAG] Raw message event payload: %s", json.dumps(event, default=str))

        # Ignore message_changed and other subtypes (edits, deletions, etc.)
        if event.get("subtype"):
            telemetry.record_event("message", ignored=True)
            logger.info(
                "[DIAG] Ignoring message event with subtype=%s",
                event.get("subtype"),
            )
            return

        # Only ignore messages from THIS bot — let other bots' messages through
        if _is_own_bot_message(event):
            telemetry.record_event("message", ignored=True)
            logger.info(
                "[DIAG] Ignoring own bot message (bot_id=%s, own_bot_id=%s)",
                event.get("bot_id"),
                _own_bot_id,
            )
            return

        telemetry.record_event("message")
        telemetry.message_events += 1

        classified = classify_message(event)
        logger.info(
            "Classified message as %s in channel %s (bot_id=%s)",
            classified.kind.name,
            event.get("channel"),
            event.get("bot_id", "(none)"),
        )

        # Run the async handler from the sync Bolt context
        asyncio.run(handle_message(event, say, client, classified))

    @app.event("app_mention")
    def handle_app_mention(event, say, client):
        """Handle @-mentions of the bot in channels.

        Same logic as DMs but triggered via @-mention.  Only ignores
        mentions from this bot itself.
        """
        logger.info(
            "[DIAG] Received 'app_mention' event: channel=%s user=%s bot_id=%s",
            event.get("channel"),
            event.get("user"),
            event.get("bot_id"),
        )
        logger.debug("[DIAG] Raw app_mention event payload: %s", json.dumps(event, default=str))

        # Only ignore mentions from THIS bot — let other bots through
        if _is_own_bot_message(event):
            telemetry.record_event("app_mention", ignored=True)
            logger.info(
                "[DIAG] Ignoring own bot app_mention (bot_id=%s, own_bot_id=%s)",
                event.get("bot_id"),
                _own_bot_id,
            )
            return

        telemetry.record_event("app_mention")
        telemetry.app_mention_events += 1

        classified = classify_message(event)
        logger.info(
            "Classified @-mention as %s in channel %s (bot_id=%s)",
            classified.kind.name,
            event.get("channel"),
            event.get("bot_id", "(none)"),
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
