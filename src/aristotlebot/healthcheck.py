"""HTTP health-check server for monitoring the Slack bot.

Runs on a separate thread (default port 8080) and exposes a single
``GET /health`` endpoint that reports:

    - Whether the process is running
    - How many events have been received since startup
    - Last event timestamp and age
    - Registered event listeners

Invariants:
    - The health server never blocks the main Socket Mode thread.
    - The server binds only to the configured port; port 0 is allowed for tests.
    - JSON responses are always valid, even when no events have been received.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slack_bolt import App

logger = logging.getLogger(__name__)

# Default port; overridable via HEALTH_CHECK_PORT env var.
DEFAULT_PORT = 8080


class _HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /health endpoint.

    Attributes set by the factory:
        _event_stats: EventStats instance from app module.
        _app: Optional Slack Bolt App for listener introspection.
    """

    _event_stats = None  # type: ignore[assignment]
    _app = None  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802 — HTTP handler convention
        """Handle GET requests.  Only /health is supported; all else → 404."""
        if self.path == "/health" or self.path == "/":
            self._handle_health()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "Not found. Use GET /health"}')

    def _handle_health(self) -> None:
        """Return a JSON health-check payload."""
        from .app import get_event_stats, get_registered_listeners

        stats = get_event_stats()
        listeners = (
            get_registered_listeners(self._app) if self._app else ["(app not set)"]
        )

        payload = {
            "status": "ok",
            "socket_mode": "running",
            "events": stats.to_dict(),
            "registered_listeners": listeners,
        }

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Suppress default stderr logging; route through Python logging instead."""
        logger.debug("HealthCheck %s", format % args)


def start_health_server(
    app: App | None = None,
    port: int = DEFAULT_PORT,
) -> HTTPServer:
    """Start the health-check HTTP server in a daemon thread.

    Args:
        app: The Slack Bolt App (for listener introspection).
        port: TCP port to listen on. Use 0 for a random available port (tests).

    Returns:
        The running HTTPServer instance (for shutdown in tests).

    Postconditions:
        - Server is listening on the requested port.
        - The thread is a daemon thread and will not block process exit.
    """
    _HealthHandler._app = app

    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    actual_port = server.server_address[1]

    thread = threading.Thread(
        target=server.serve_forever,
        name="health-check",
        daemon=True,
    )
    thread.start()

    logger.info("🏥 Health-check server listening on port %d", actual_port)
    return server
