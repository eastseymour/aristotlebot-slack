"""Unit tests for aristotlebot.health — HTTP health-check server."""

from __future__ import annotations

import json
import time
import urllib.request
from unittest.mock import patch

import pytest

from aristotlebot.app import EventTelemetry, telemetry
from aristotlebot.health import start_health_server


class TestHealthEndpoint:
    """Tests for the /health HTTP endpoint."""

    @pytest.fixture(autouse=True)
    def _reset_telemetry(self):
        """Reset telemetry counters before each test."""
        telemetry.total_events = 0
        telemetry.message_events = 0
        telemetry.app_mention_events = 0
        telemetry.ignored_events = 0
        telemetry.last_event_ts = 0.0
        telemetry.start_ts = time.time()
        telemetry.registered_listeners = ["message", "app_mention"]
        yield

    @pytest.fixture
    def health_server(self):
        """Start a health server on a random high port and tear it down after."""
        # Use a high port to avoid conflicts
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = start_health_server(port=port)
        yield port
        server.shutdown()

    def test_health_endpoint_returns_200(self, health_server):
        port = health_server
        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())

        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert data["socket_mode_connected"] is True

    def test_health_endpoint_reports_zero_events_initially(self, health_server):
        port = health_server
        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())

        assert data["events"]["total_received"] == 0
        assert data["events"]["message_events"] == 0
        assert data["events"]["app_mention_events"] == 0
        assert data["last_event"]["timestamp_iso"] is None
        assert data["last_event"]["seconds_ago"] is None

    def test_health_endpoint_reports_events_after_recording(self, health_server):
        port = health_server

        # Simulate receiving events
        telemetry.record_event("message")
        telemetry.message_events += 1
        telemetry.record_event("app_mention")
        telemetry.app_mention_events += 1
        telemetry.record_event("message", ignored=True)

        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())

        assert data["events"]["total_received"] == 3
        assert data["events"]["message_events"] == 1
        assert data["events"]["app_mention_events"] == 1
        assert data["events"]["ignored_events"] == 1
        assert data["last_event"]["timestamp_iso"] is not None
        assert data["last_event"]["seconds_ago"] is not None
        assert data["last_event"]["seconds_ago"] < 5.0  # Just recorded

    def test_health_endpoint_reports_registered_listeners(self, health_server):
        port = health_server
        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())

        assert "message" in data["registered_listeners"]
        assert "app_mention" in data["registered_listeners"]

    def test_healthz_alias_works(self, health_server):
        """The /healthz path should also work (Kubernetes convention)."""
        port = health_server
        url = f"http://127.0.0.1:{port}/healthz"
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200

    def test_root_path_works(self, health_server):
        """The / path should also serve health data."""
        port = health_server
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200

    def test_unknown_path_returns_404(self, health_server):
        port = health_server
        url = f"http://127.0.0.1:{port}/unknown"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url)
        assert exc_info.value.code == 404
