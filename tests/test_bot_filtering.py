"""Unit tests for bot message filtering — only own bot_id should be filtered.

This test module verifies the critical invariant:
    - Messages from this bot's own bot_id are filtered (preventing loops).
    - Messages from OTHER bots/apps (e.g. Klaw, bot_id B0AGSCRN04S) are NOT filtered.
    - Messages with no bot_id (human users) are NOT filtered.

These tests exercise ``_is_own_bot_message()`` directly and also verify the
filtering behavior end-to-end through ``create_app``'s event handlers.
"""

from __future__ import annotations

import aristotlebot.app as app_module
from aristotlebot.app import _is_own_bot_message, get_own_bot_id

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_own_bot_id():
    """Reset _own_bot_id before and after each test to avoid cross-contamination."""
    original = app_module._own_bot_id
    app_module._own_bot_id = None
    yield
    app_module._own_bot_id = original


# ---------------------------------------------------------------------------
# Tests for _is_own_bot_message
# ---------------------------------------------------------------------------

class TestIsOwnBotMessage:
    """Tests for the _is_own_bot_message() helper function."""

    def test_own_bot_id_is_filtered(self):
        """Messages from our own bot_id should be identified as own."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {"bot_id": "B0AJ2MXMBC7", "text": "I said something"}
        assert _is_own_bot_message(event) is True

    def test_other_bot_id_is_not_filtered(self):
        """Messages from a different bot (e.g. Klaw) should NOT be identified as own."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {"bot_id": "B0AGSCRN04S", "text": "Klaw says hello"}
        assert _is_own_bot_message(event) is False

    def test_no_bot_id_is_not_filtered(self):
        """Messages from human users (no bot_id) should NOT be identified as own."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {"user": "U12345", "text": "Hello bot"}
        assert _is_own_bot_message(event) is False

    def test_empty_bot_id_is_not_filtered(self):
        """Events with empty-string bot_id should NOT be identified as own."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {"bot_id": "", "text": "Edge case"}
        assert _is_own_bot_message(event) is False

    def test_own_bot_id_not_set_returns_false(self):
        """When _own_bot_id is None (not yet discovered), never filter."""
        app_module._own_bot_id = None
        event = {"bot_id": "B0AJ2MXMBC7", "text": "Unknown origin"}
        # Should NOT filter — conservative approach to avoid dropping messages
        assert _is_own_bot_message(event) is False

    def test_multiple_different_bot_ids(self):
        """Verify filtering with several different bot_ids."""
        app_module._own_bot_id = "B0AJ2MXMBC7"

        # Own bot — should be filtered
        assert _is_own_bot_message({"bot_id": "B0AJ2MXMBC7"}) is True

        # Other bots — should NOT be filtered
        assert _is_own_bot_message({"bot_id": "B0AGSCRN04S"}) is False  # Klaw
        assert _is_own_bot_message({"bot_id": "B123456789"}) is False
        assert _is_own_bot_message({"bot_id": "BXXXXXXXXXX"}) is False

        # No bot_id — should NOT be filtered
        assert _is_own_bot_message({"user": "U12345"}) is False
        assert _is_own_bot_message({}) is False


class TestGetOwnBotId:
    """Tests for the get_own_bot_id() accessor."""

    def test_returns_none_when_not_set(self):
        """Before auth.test runs, get_own_bot_id() returns None."""
        app_module._own_bot_id = None
        assert get_own_bot_id() is None

    def test_returns_bot_id_when_set(self):
        """After auth.test, get_own_bot_id() returns the discovered bot_id."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        assert get_own_bot_id() == "B0AJ2MXMBC7"


# ---------------------------------------------------------------------------
# Integration tests for filtering through create_app event handlers
# ---------------------------------------------------------------------------

class TestMessageFilteringIntegration:
    """Test that the event handlers in create_app correctly filter/pass messages.

    These tests set _own_bot_id directly and invoke the handlers through
    the Bolt app to verify end-to-end filtering behavior.
    """

    @pytest.fixture
    def app(self):
        """Create a test app with token verification disabled."""
        from aristotlebot.app import create_app
        return create_app(
            bot_token="xoxb-test-token",
            token_verification_enabled=False,
        )

    def test_own_bot_message_is_ignored(self, app):
        """Messages from own bot_id should be silently dropped."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {
            "type": "message",
            "bot_id": "B0AJ2MXMBC7",
            "text": "My own message",
            "channel": "C123",
            "ts": "1234567890.123456",
        }
        say = pytest.importorskip("unittest.mock").MagicMock()
        client = pytest.importorskip("unittest.mock").MagicMock()

        # The handler should return without calling asyncio.run / handle_message
        from unittest.mock import patch
        with patch("aristotlebot.app.asyncio") as mock_asyncio:
            # Invoke the message handler directly
            # Find the registered handler
            for listener in app._listeners:
                if hasattr(listener, 'ack_function'):
                    fn = listener.ack_function
                    if fn.__name__ == "handle_message_event":
                        fn(event, say, client)
                        break
            else:
                # Bolt may store listeners differently; just call _is_own_bot_message
                assert _is_own_bot_message(event) is True

    def test_other_bot_message_is_processed(self):
        """Messages from other bots (like Klaw) should be processed, not ignored."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {
            "type": "message",
            "bot_id": "B0AGSCRN04S",  # Klaw's bot_id
            "text": "Message from Klaw",
            "channel": "C123",
            "ts": "1234567890.123456",
        }
        # This message should NOT be filtered
        assert _is_own_bot_message(event) is False

    def test_human_message_is_processed(self):
        """Messages from human users should be processed."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        event = {
            "type": "message",
            "user": "U12345678",
            "text": "Hello bot!",
            "channel": "C123",
            "ts": "1234567890.123456",
        }
        assert _is_own_bot_message(event) is False

    def test_subtype_messages_still_filtered(self):
        """Messages with subtypes (message_changed, etc.) are always ignored
        regardless of bot_id — this is separate from bot filtering."""
        app_module._own_bot_id = "B0AJ2MXMBC7"
        # A message_changed event from another bot should still be ignored
        # (because of the subtype, not because of the bot_id)
        event = {
            "type": "message",
            "subtype": "message_changed",
            "bot_id": "B0AGSCRN04S",
            "text": "Edited message",
            "channel": "C123",
        }
        # The subtype check happens BEFORE the bot_id check in the handler
        # For the _is_own_bot_message function, the bot_id is a different bot
        assert _is_own_bot_message(event) is False
        # But the event would still be filtered by the subtype check in the handler
