"""Unit tests for aristotlebot.app — Slack Bolt app creation and event routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestCreateApp:
    """Tests for the create_app factory function."""

    def test_creates_app_with_explicit_token(self):
        from aristotlebot.app import create_app

        # Disable token verification to avoid hitting Slack API in tests
        app = create_app(
            bot_token="xoxb-test-token-12345",
            token_verification_enabled=False,
        )
        assert app is not None

    def test_creates_app_from_env(self):
        from aristotlebot.app import create_app

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token"}):
            app = create_app(token_verification_enabled=False)
            assert app is not None

    def test_missing_token_raises(self):
        from aristotlebot.app import create_app

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(KeyError):
                create_app()


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
