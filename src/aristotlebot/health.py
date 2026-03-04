"""HTTP health-check server for the Aristotle Slack bot.

Exposes a lightweight HTTP endpoint on a configurable port (default 8080)
that reports bot health and event telemetry. Designed to be started in a
daemon thread alongside the Socket Mode handler.

Invariants:
    - The health server never blocks the main Socket Mode thread.
    - All telemetry reads are from the shared ``EventTelemetry`` singleton.
    - The server binds to ``0.0.0.0`` so it's reachable from container probes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from .app import telemetry

logger = logging.getLogger(__name__)

# Default port; override with HEALTH_CHECK_PORT env var.
_DEFAULT_PORT = 8080


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP request handler that serves ``GET /health``."""

    def do_GET(self) -> None:  # noqa: N802 — method name required by BaseHTTPRequestHandler
        """Respond to GET requests with JSON health status.

        Postconditions:
            - Always returns 200 with a JSON body (never 500).
        """
        if self.path not in ("/health", "/healthz", "/"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found\n")
            return

        now = time.time()
        uptime_seconds = now - telemetry.start_ts

        last_event_ago: float | None = None
        last_event_iso: str | None = None
        if telemetry.last_event_ts > 0:
            last_event_ago = round(now - telemetry.last_event_ts, 1)
            last_event_iso = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(telemetry.last_event_ts)
            )

        body = {
            "status": "ok",
            "uptime_seconds": round(uptime_seconds, 1),
            "socket_mode_connected": True,  # If we're alive, SM is running
            "events": {
                "total_received": telemetry.total_events,
                "message_events": telemetry.message_events,
                "app_mention_events": telemetry.app_mention_events,
                "ignored_events": telemetry.ignored_events,
            },
            "last_event": {
                "timestamp_iso": last_event_iso,
                "seconds_ago": last_event_ago,
            },
            "registered_listeners": telemetry.registered_listeners,
        }

        payload = json.dumps(body, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress default stderr logging; route through Python logging instead."""
        logger.debug("Health check request: %s", format % args)


def start_health_server(port: int | None = None) -> HTTPServer:
    """Start the health-check HTTP server in a daemon thread.

    Args:
        port: TCP port to listen on. Defaults to ``HEALTH_CHECK_PORT`` env var
              or 8080.

    Returns:
        The running ``HTTPServer`` instance (useful for testing / shutdown).

    Postconditions:
        - A daemon thread is started; it will be killed when the main process exits.
        - The server is bound and listening by the time this function returns.
    """
    if port is None:
        port = int(os.environ.get("HEALTH_CHECK_PORT", str(_DEFAULT_PORT)))

    server = HTTPServer(("0.0.0.0", port), _HealthHandler)

    thread = threading.Thread(
        target=server.serve_forever,
        name="health-check",
        daemon=True,
    )
    thread.start()
    logger.info("Health-check server listening on http://0.0.0.0:%d/health", port)
    return server
