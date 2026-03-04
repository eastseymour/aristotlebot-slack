"""Slack Socket Mode bot using slack-bolt.

This module configures the Bolt app, registers message listeners, and provides
the ``create_app`` factory for both production and testing.

Invariants:
    - The app ONLY responds to direct messages and @-mentions (not every channel message).
    - Bot's own messages are ignored to prevent infinite loops.
    - Every incoming event is logged with its type and channel for diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .handlers import handle_message
from .utils import classify_message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event statistics — lightweight in-process counters for diagnostics.
# ---------------------------------------------------------------------------

class EventStats:
    """Track event delivery statistics for health-check reporting.

    Invariants:
        - ``total_events`` is always >= 0.
        - ``last_event_ts`` is None only when no events have been received.
        - ``events_by_type`` keys are always non-empty strings.
    """

    __slots__ = ("total_events", "last_event_ts", "events_by_type", "_start_time")

    def __init__(self) -> None:
        self.total_events: int = 0
        self.last_event_ts: float | None = None
        self.events_by_type: dict[str, int] = {}
        self._start_time: float = time.time()

    def record(self, event_type: str) -> None:
        """Record receipt of a single event."""
        assert event_type, "event_type must be a non-empty string"
        self.total_events += 1
        self.last_event_ts = time.time()
        self.events_by_type[event_type] = self.events_by_type.get(event_type, 0) + 1

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def to_dict(self) -> dict[str, Any]:
        """Serialize stats for the health-check endpoint."""
        return {
            "total_events": self.total_events,
            "last_event_ts": self.last_event_ts,
            "last_event_age_seconds": (
                round(time.time() - self.last_event_ts, 1)
                if self.last_event_ts is not None
                else None
            ),
            "events_by_type": dict(self.events_by_type),
            "uptime_seconds": round(self.uptime_seconds, 1),
        }


# Module-level singleton so the health-check server can read it.
_event_stats = EventStats()


def get_event_stats() -> EventStats:
    """Return the module-level EventStats singleton."""
    return _event_stats


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

    # ------------------------------------------------------------------
    # Catch-all middleware: log EVERY incoming event for diagnostics.
    # This fires before any specific listener, so if events arrive but
    # listeners don't fire, we'll see them here.
    # ------------------------------------------------------------------
    @app.middleware
    def log_all_events(body, next):
        """Log every incoming Socket Mode envelope for diagnostics.

        This middleware runs before event-specific listeners.  If events
        reach this point, Socket Mode delivery is working.
        """
        event = body.get("event", {})
        event_type = event.get("type", "unknown")
        event_subtype = event.get("subtype", "")
        channel = event.get("channel", "N/A")

        _event_stats.record(event_type)

        logger.info(
            "📨 Raw event received — type=%s subtype=%s channel=%s ts=%s",
            event_type,
            event_subtype or "(none)",
            channel,
            event.get("ts", "N/A"),
        )
        logger.debug(
            "📨 Full event payload: %s",
            json.dumps(body, indent=2, default=str),
        )
        next()

    # ------------------------------------------------------------------
    # Event: message (DMs and channel messages)
    # ------------------------------------------------------------------
    @app.event("message")
    def handle_message_event(event, say, client):
        """Handle incoming Slack messages.

        Ignores bot messages to prevent feedback loops, then classifies
        and dispatches the message to the appropriate handler.
        """
        logger.info(
            "🔔 message event listener fired — user=%s channel=%s subtype=%s bot_id=%s",
            event.get("user", "N/A"),
            event.get("channel", "N/A"),
            event.get("subtype", "(none)"),
            event.get("bot_id", "(none)"),
        )

        # Ignore bot messages and message_changed subtypes
        if event.get("bot_id") or event.get("subtype"):
            logger.info(
                "⏭️  Skipping event: bot_id=%s subtype=%s",
                event.get("bot_id"),
                event.get("subtype"),
            )
            return

        classified = classify_message(event)
        logger.info(
            "📋 Classified message as %s in channel %s",
            classified.kind.name,
            event.get("channel"),
        )

        # Run the async handler from the sync Bolt context
        asyncio.run(handle_message(event, say, client, classified))

    # ------------------------------------------------------------------
    # Event: app_mention (@-mentions in channels)
    # ------------------------------------------------------------------
    @app.event("app_mention")
    def handle_app_mention(event, say, client):
        """Handle @-mentions of the bot in channels.

        Same logic as DMs but triggered via @-mention.
        """
        logger.info(
            "🔔 app_mention event listener fired — user=%s channel=%s bot_id=%s",
            event.get("user", "N/A"),
            event.get("channel", "N/A"),
            event.get("bot_id", "(none)"),
        )

        if event.get("bot_id"):
            logger.info("⏭️  Skipping app_mention from bot: bot_id=%s", event.get("bot_id"))
            return

        classified = classify_message(event)
        logger.info(
            "📋 Classified @-mention as %s in channel %s",
            classified.kind.name,
            event.get("channel"),
        )

        asyncio.run(handle_message(event, say, client, classified))

    # Log registered listeners for diagnostics
    logger.info(
        "✅ Registered event listeners: %s",
        [
            f"{l.ack_function.__name__} -> {l.matchers}"
            for l in getattr(app, "_listeners", [])
        ] if hasattr(app, "_listeners") else "(unable to inspect)",
    )

    return app


def get_registered_listeners(app: App) -> list[str]:
    """Return a list of registered event listener names for diagnostics.

    This introspects the Bolt app's internal listener registry.
    """
    listeners: list[str] = []
    # Bolt stores listeners in _listener_runner._listeners or _listeners
    listener_store = getattr(app, "_listeners", None)
    if listener_store is None:
        listener_store = getattr(
            getattr(app, "_listener_runner", None), "_listeners", None
        )
    if listener_store:
        for listener_list in listener_store:
            if isinstance(listener_list, list):
                for li in listener_list:
                    name = getattr(li, "ack_function", None)
                    if name:
                        listeners.append(getattr(name, "__name__", str(name)))
            else:
                name = getattr(listener_list, "ack_function", None)
                if name:
                    listeners.append(getattr(name, "__name__", str(name)))
    return listeners or ["(unable to inspect Bolt listener registry)"]


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
    logger.info("🚀 Starting Aristotle Slack bot in Socket Mode…")
    logger.info(
        "📋 Registered listeners: %s",
        get_registered_listeners(app),
    )
    logger.info(
        "💡 If no events arrive, check that your Slack app has Event Subscriptions "
        "enabled with bot events: app_mention, message.channels, message.im"
    )
    handler.start()
