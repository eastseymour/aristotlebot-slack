"""Unit tests for aristotlebot.app — Slack Bolt app creation, event routing, and telemetry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aristotlebot.app import EventTelemetry, create_app, telemetry


class TestCreateApp:
    """Tests for the create_app factory function."""

    def test_creates_app_with_explicit_token(self):
        # Disable token verification to avoid hitting Slack API in tests
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

    def test_registers_event_listeners(self):
        """create_app should register 'message' and 'app_mention' listeners."""
        app = create_app(
            bot_token="xoxb-test-token",
            token_verification_enabled=False,
        )
        assert "message" in telemetry.registered_listeners
        assert "app_mention" in telemetry.registered_listeners


class TestEventTelemetry:
    """Tests for the EventTelemetry dataclass."""

    def test_initial_state(self):
        t = EventTelemetry()
        assert t.total_events == 0
        assert t.message_events == 0
        assert t.app_mention_events == 0
        assert t.ignored_events == 0
        assert t.last_event_ts == 0.0
        assert t.start_ts > 0

    def test_record_event_increments_total(self):
        t = EventTelemetry()
        t.record_event("message")
        assert t.total_events == 1
        assert t.last_event_ts > 0

    def test_record_ignored_event(self):
        t = EventTelemetry()
        t.record_event("message", ignored=True)
        assert t.total_events == 1
        assert t.ignored_events == 1

    def test_multiple_events(self):
        t = EventTelemetry()
        t.record_event("message")
        t.record_event("app_mention")
        t.record_event("message", ignored=True)
        assert t.total_events == 3
        assert t.ignored_events == 1


class TestMainEntryPoint:
    """Tests for main.py environment validation."""

    def test_validate_env_missing_vars(self):
        """_validate_env should exit when required env vars are missing."""
        import sys
        sys.path.insert(0, "/root/aristotlebot-slack")
        from main import _validate_env

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit):
                _validate_env()

    def test_validate_env_all_present(self):
        import sys
        sys.path.insert(0, "/root/aristotlebot-slack")
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
