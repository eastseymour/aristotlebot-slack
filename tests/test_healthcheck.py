"""Tests for aristotlebot.healthcheck — HTTP health-check server."""

from __future__ import annotations

import json
import time
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from aristotlebot.healthcheck import start_health_server, _HealthHandler


class TestHealthServer:
    """Tests for the health-check HTTP server."""

    def test_health_endpoint_returns_200(self):
        """GET /health returns 200 with JSON body."""
        server = start_health_server(app=None, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/health"
            resp = urllib.request.urlopen(url, timeout=5)
            assert resp.status == 200

            body = json.loads(resp.read().decode())
            assert body["status"] == "ok"
            assert body["socket_mode"] == "running"
            assert "events" in body
            assert "registered_listeners" in body
        finally:
            server.shutdown()

    def test_health_endpoint_event_stats(self):
        """Health endpoint reflects event stats from the EventStats singleton."""
        from aristotlebot.app import get_event_stats

        stats = get_event_stats()
        # Record a fake event
        initial_count = stats.total_events
        stats.record("message")
        stats.record("app_mention")

        server = start_health_server(app=None, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/health"
            resp = urllib.request.urlopen(url, timeout=5)
            body = json.loads(resp.read().decode())

            events = body["events"]
            assert events["total_events"] >= initial_count + 2
            assert events["last_event_ts"] is not None
            assert events["last_event_age_seconds"] is not None
            assert events["uptime_seconds"] > 0
            assert "message" in events["events_by_type"]
            assert "app_mention" in events["events_by_type"]
        finally:
            server.shutdown()

    def test_root_path_returns_health(self):
        """GET / also returns health check (convenience)."""
        server = start_health_server(app=None, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/"
            resp = urllib.request.urlopen(url, timeout=5)
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body["status"] == "ok"
        finally:
            server.shutdown()

    def test_unknown_path_returns_404(self):
        """GET /unknown returns 404."""
        server = start_health_server(app=None, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/unknown"
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(url, timeout=5)
            assert exc_info.value.code == 404
        finally:
            server.shutdown()

    def test_health_with_app_shows_listeners(self):
        """When an App is provided, listeners are reported."""
        from aristotlebot.app import create_app

        app = create_app(
            bot_token="xoxb-test-health-check",
            token_verification_enabled=False,
        )
        server = start_health_server(app=app, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/health"
            resp = urllib.request.urlopen(url, timeout=5)
            body = json.loads(resp.read().decode())
            assert "registered_listeners" in body
            # Should have at least the listener introspection result
            assert len(body["registered_listeners"]) >= 1
        finally:
            server.shutdown()


class TestHealthHandlerLogSuppression:
    """Test that health handler suppresses default HTTP logging."""

    def test_log_message_does_not_raise(self):
        """_HealthHandler.log_message should not raise."""
        handler = _HealthHandler.__new__(_HealthHandler)
        # Should not raise
        handler.log_message("test %s", "value")
