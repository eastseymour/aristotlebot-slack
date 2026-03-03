"""Unit tests for aristotlebot.handlers — mock aristotlelib and Slack."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.aristotlebot.handlers import handle_message
from src.aristotlebot.utils import ClassifiedMessage, MessageKind


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def slack_event():
    """A minimal Slack message event."""
    return {
        "channel": "C12345",
        "ts": "1234567890.123456",
        "text": "Prove that 1+1=2",
        "user": "U12345",
    }


@pytest.fixture
def say():
    """Mock Slack 'say' function."""
    return MagicMock()


@pytest.fixture
def client():
    """Mock Slack client with reactions_add/remove."""
    c = MagicMock()
    c.reactions_add = MagicMock()
    c.reactions_remove = MagicMock()
    return c


# ===================================================================
# Natural language handler
# ===================================================================

class TestHandleNaturalLanguage:
    @pytest.mark.asyncio
    async def test_submits_to_aristotle_informal(self, slack_event, say, client):
        classified = ClassifiedMessage(
            kind=MessageKind.NATURAL_LANGUAGE,
            payload="Prove that 1+1=2",
        )

        mock_prove = AsyncMock(return_value="/tmp/solution.lean")
        with (
            patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("src.aristotlebot.handlers.read_solution_file", return_value="theorem : 1+1=2 := rfl"),
        ):
            await handle_message(slack_event, say, client, classified)

        # Should have posted at least a progress message and a result
        assert say.call_count >= 2
        # The final call should contain the solution
        final_call = say.call_args_list[-1]
        assert "1+1=2" in final_call.kwargs.get("text", final_call[1].get("text", ""))

        # prove_from_file should have been called with informal mode
        mock_prove.assert_called_once()
        call_kwargs = mock_prove.call_args.kwargs
        assert call_kwargs["input_content"] == "Prove that 1+1=2"

    @pytest.mark.asyncio
    async def test_empty_message_is_ignored(self, slack_event, say, client):
        slack_event["text"] = ""
        classified = ClassifiedMessage(
            kind=MessageKind.NATURAL_LANGUAGE,
            payload="",
        )
        await handle_message(slack_event, say, client, classified)
        say.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_posts_error_message(self, slack_event, say, client):
        classified = ClassifiedMessage(
            kind=MessageKind.NATURAL_LANGUAGE,
            payload="Prove something",
        )

        mock_prove = AsyncMock(side_effect=RuntimeError("API error"))
        with patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove):
            await handle_message(slack_event, say, client, classified)

        # Should have posted an error message
        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        error_msgs = [c for c in calls if ":x:" in c]
        assert len(error_msgs) >= 1


# ===================================================================
# .lean file upload handler
# ===================================================================

class TestHandleLeanFileUpload:
    @pytest.mark.asyncio
    async def test_downloads_and_submits_file(self, slack_event, say, client):
        slack_event["files"] = [
            {"name": "Foo.lean", "url_private_download": "https://slack.com/files/x"}
        ]
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_FILE_UPLOAD,
            payload={"name": "Foo.lean", "url_private_download": "https://slack.com/files/x"},
        )

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("src.aristotlebot.handlers.download_slack_file", mock_download),
            patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("src.aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        mock_download.assert_called_once()
        mock_prove.assert_called_once()
        # Result should be posted
        assert say.call_count >= 2

    @pytest.mark.asyncio
    async def test_formal_mode_disables_auto_add_imports(self, slack_event, say, client):
        """Verify auto_add_imports=False is passed for formal mode (prevents assertion in aristotlelib)."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_FILE_UPLOAD,
            payload={"name": "Foo.lean", "url_private_download": "https://slack.com/files/x"},
        )

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("src.aristotlebot.handlers.download_slack_file", mock_download),
            patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("src.aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        call_kwargs = mock_prove.call_args.kwargs
        assert call_kwargs["auto_add_imports"] is False, \
            "formal mode must pass auto_add_imports=False when validate_lean_project=False"
        assert call_kwargs["validate_lean_project"] is False

    @pytest.mark.asyncio
    async def test_missing_download_url_posts_error(self, slack_event, say, client):
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_FILE_UPLOAD,
            payload={"name": "Foo.lean", "url_private_download": ""},
        )

        with (
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        # Should post an error about missing download URL
        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        assert any("Could not get download URL" in c for c in calls)


# ===================================================================
# URL handler
# ===================================================================

class TestHandleLeanUrl:
    @pytest.mark.asyncio
    async def test_downloads_url_and_submits(self, slack_event, say, client):
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://example.com/Foo.lean",
        )

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("src.aristotlebot.handlers.download_url", mock_download),
            patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("src.aristotlebot.handlers.read_solution_file", return_value="-- proved"),
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
        ):
            await handle_message(slack_event, say, client, classified)

        mock_download.assert_called_once()
        mock_prove.assert_called_once()

    @pytest.mark.asyncio
    async def test_url_formal_mode_disables_auto_add_imports(self, slack_event, say, client):
        """Verify auto_add_imports=False is passed for URL formal mode too."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://example.com/Foo.lean",
        )

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("src.aristotlebot.handlers.download_url", mock_download),
            patch("src.aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("src.aristotlebot.handlers.read_solution_file", return_value="-- proved"),
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
        ):
            await handle_message(slack_event, say, client, classified)

        call_kwargs = mock_prove.call_args.kwargs
        assert call_kwargs["auto_add_imports"] is False
        assert call_kwargs["validate_lean_project"] is False

    @pytest.mark.asyncio
    async def test_download_failure_posts_error(self, slack_event, say, client):
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://example.com/Bad.lean",
        )

        mock_download = AsyncMock(side_effect=Exception("404 Not Found"))

        with (
            patch("src.aristotlebot.handlers.download_url", mock_download),
            patch("src.aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("src.aristotlebot.handlers.shutil.rmtree"),
        ):
            await handle_message(slack_event, say, client, classified)

        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        error_msgs = [c for c in calls if ":x:" in c]
        assert len(error_msgs) >= 1


# ===================================================================
# Integration test structure
# ===================================================================

class TestIntegrationStructure:
    """Placeholder integration tests — require live Slack and Aristotle credentials.

    These tests verify the structural contract but don't make real API calls.
    Mark with @pytest.mark.integration and skip by default.
    """

    @pytest.mark.skipif(True, reason="Requires live Slack credentials")
    @pytest.mark.asyncio
    async def test_full_natural_language_flow(self):
        """End-to-end: send NL prompt → Aristotle → Slack thread reply."""
        # This would be filled in for integration testing with real credentials
        pass

    @pytest.mark.skipif(True, reason="Requires live Slack credentials")
    @pytest.mark.asyncio
    async def test_full_file_upload_flow(self):
        """End-to-end: upload .lean file → Aristotle → Slack thread reply."""
        pass
