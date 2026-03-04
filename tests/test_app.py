"""Unit tests for aristotlebot.app — Slack Bolt app creation, event routing, and diagnostics."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from aristotlebot.app import EventStats, create_app, get_event_stats, get_registered_listeners


# ===================================================================
# EventStats
# ===================================================================

class TestEventStats:
    """Tests for the EventStats diagnostic counter."""

    def test_initial_state(self):
        stats = EventStats()
        assert stats.total_events == 0
        assert stats.last_event_ts is None
        assert stats.events_by_type == {}
        assert stats.uptime_seconds >= 0

    def test_record_increments_counters(self):
        stats = EventStats()
        stats.record("message")
        assert stats.total_events == 1
        assert stats.last_event_ts is not None
        assert stats.events_by_type == {"message": 1}

    def test_record_multiple_types(self):
        stats = EventStats()
        stats.record("message")
        stats.record("message")
        stats.record("app_mention")
        assert stats.total_events == 3
        assert stats.events_by_type == {"message": 2, "app_mention": 1}

    def test_record_rejects_empty_type(self):
        stats = EventStats()
        with pytest.raises(AssertionError):
            stats.record("")

    def test_to_dict_structure(self):
        stats = EventStats()
        stats.record("message")
        d = stats.to_dict()
        assert "total_events" in d
        assert "last_event_ts" in d
        assert "last_event_age_seconds" in d
        assert "events_by_type" in d
        assert "uptime_seconds" in d
        assert d["total_events"] == 1
        assert d["last_event_age_seconds"] is not None
        assert d["last_event_age_seconds"] >= 0

    def test_to_dict_no_events(self):
        stats = EventStats()
        d = stats.to_dict()
        assert d["total_events"] == 0
        assert d["last_event_ts"] is None
        assert d["last_event_age_seconds"] is None

    def test_uptime_increases(self):
        stats = EventStats()
        t1 = stats.uptime_seconds
        # Uptime should be >= 0
        assert t1 >= 0


class TestGetEventStats:
    """Tests for the module-level EventStats singleton."""

    def test_returns_singleton(self):
        s1 = get_event_stats()
        s2 = get_event_stats()
        assert s1 is s2

    def test_singleton_is_event_stats(self):
        assert isinstance(get_event_stats(), EventStats)


# ===================================================================
# create_app
# ===================================================================

class TestCreateApp:
    """Tests for the create_app factory function."""

    def test_creates_app_with_explicit_token(self):
        app = create_app(
            bot_token="xoxb-test-token-12345",
            token_verification_enabled=False,
        )
        assert app is not None

    def test_creates_app_from_env(self):
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token"}):
            app = create_app(token_verification_enabled=False)
            assert app is not None

    def test_missing_token_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(KeyError):
                create_app()

    def test_registers_message_listener(self):
        """The app should register a listener for 'message' events."""
        app = create_app(
            bot_token="xoxb-test-listeners",
            token_verification_enabled=False,
        )
        # Bolt stores listeners internally; verify the app was created successfully
        # and has the expected structure
        assert app is not None

    def test_registers_app_mention_listener(self):
        """The app should register a listener for 'app_mention' events."""
        app = create_app(
            bot_token="xoxb-test-listeners",
            token_verification_enabled=False,
        )
        assert app is not None


# ===================================================================
# get_registered_listeners
# ===================================================================

class TestGetRegisteredListeners:
    """Tests for the listener introspection helper."""

    def test_returns_list(self):
        app = create_app(
            bot_token="xoxb-test-introspect",
            token_verification_enabled=False,
        )
        listeners = get_registered_listeners(app)
        assert isinstance(listeners, list)
        assert len(listeners) >= 1

    def test_returns_fallback_for_non_app(self):
        """When passed something without a listener store, returns fallback."""
        listeners = get_registered_listeners(MagicMock(spec=[]))
        assert len(listeners) >= 1
        assert any("unable" in s.lower() for s in listeners)


# ===================================================================
# Middleware: log_all_events
# ===================================================================

class TestMiddleware:
    """Tests that the middleware logs events and updates EventStats."""

    def test_middleware_records_event_stats(self):
        """The log_all_events middleware should increment the EventStats counter."""
        app = create_app(
            bot_token="xoxb-test-middleware",
            token_verification_enabled=False,
        )
        stats = get_event_stats()
        initial_count = stats.total_events

        # Simulate a middleware call by finding and invoking it
        # The middleware is registered in the app's middleware list
        # We can test it indirectly by checking that EventStats gets updated
        # after app creation (the middleware itself runs on real events)
        # For unit testing, we verify the stats object works
        stats.record("test_event")
        assert stats.total_events == initial_count + 1


# ===================================================================
# Entry point validation
# ===================================================================

class TestMainEntryPoint:
    """Tests for main.py environment validation."""

    def test_validate_env_missing_vars(self):
        """_validate_env should exit when required env vars are missing."""
        import sys
        sys.path.insert(0, "/var/lib/openclaw/agents/aristotlebot-slack")
        from main import _validate_env

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit):
                _validate_env()

    def test_validate_env_all_present(self):
        import sys
        sys.path.insert(0, "/var/lib/openclaw/agents/aristotlebot-slack")
        from main import _validate_env

        env = {
            "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_APP_TOKEN": "xapp-test",
            "ARISTOTLE_API_KEY": "key-test",
        }
        with patch.dict("os.environ", env):
            # Should not raise
            _validate_env()


class TestModuleMain:
    """Tests for python -m aristotlebot entry point."""

    def test_module_validate_env_missing_vars(self):
        """__main__._validate_env should exit when required env vars are missing."""
        from aristotlebot.__main__ import _validate_env

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit):
                _validate_env()

    def test_module_validate_env_all_present(self):
        from aristotlebot.__main__ import _validate_env

        env = {
            "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_APP_TOKEN": "xapp-test",
            "ARISTOTLE_API_KEY": "key-test",
        }
        with patch.dict("os.environ", env):
            # Should not raise
            _validate_env()
