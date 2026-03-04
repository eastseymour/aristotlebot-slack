"""Unit tests for aristotlebot.handlers — mock aristotlelib and Slack."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aristotlebot.handlers import handle_message, _post_result
from aristotlebot.utils import AristotleResult, ClassifiedMessage, MessageKind


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
    """Mock Slack client with reactions and file upload support."""
    c = MagicMock()
    c.reactions_add = MagicMock()
    c.reactions_remove = MagicMock()
    # Set up file upload mocks
    c.files_getUploadURLExternal = MagicMock(return_value={
        "ok": True,
        "upload_url": "https://files.slack.com/upload/v1/test-presigned-url",
        "file_id": "F_TEST_123",
    })
    c.files_completeUploadExternal = MagicMock(return_value={"ok": True})
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
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value="theorem foo : 1+1=2 := rfl"),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
        ):
            await handle_message(slack_event, say, client, classified)

        # Should have posted at least a progress message and a summary
        assert say.call_count >= 2
        # The final call should contain a summary (not inline code)
        final_call = say.call_args_list[-1]
        final_text = final_call.kwargs.get("text", final_call[1].get("text", ""))
        assert ":white_check_mark:" in final_text
        assert "Aristotle completed" in final_text
        # Solution should NOT be inline
        assert "```lean" not in final_text

        # prove_from_file should have been called with informal mode
        mock_prove.assert_called_once()
        call_kwargs = mock_prove.call_args.kwargs
        assert call_kwargs["input_content"] == "Prove that 1+1=2"

        # File upload should have been called
        mock_upload.assert_called_once()

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
        with patch("aristotlebot.handlers.Project.prove_from_file", mock_prove):
            await handle_message(slack_event, say, client, classified)

        # Should have posted an error message
        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        error_msgs = [c for c in calls if ":x:" in c]
        assert len(error_msgs) >= 1

    @pytest.mark.asyncio
    async def test_natural_language_uploads_solution_file(self, slack_event, say, client):
        """Verify that natural language results are uploaded as .lean file attachments."""
        classified = ClassifiedMessage(
            kind=MessageKind.NATURAL_LANGUAGE,
            payload="Prove that 1+1=2",
        )

        solution = "theorem one_plus_one : 1+1=2 := rfl"
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")
        with (
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value=solution),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
        ):
            await handle_message(slack_event, say, client, classified)

        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["content"] == solution
        assert upload_kwargs["filename"].endswith(".lean")
        assert upload_kwargs["channel"] == "C12345"
        assert upload_kwargs["thread_ts"] == "1234567890.123456"


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
            patch("aristotlebot.handlers.download_slack_file", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        mock_download.assert_called_once()
        mock_prove.assert_called_once()
        # Result should be posted as summary + file
        assert say.call_count >= 2
        mock_upload.assert_called_once()

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
            patch("aristotlebot.handlers.download_slack_file", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
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
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        # Should post an error about missing download URL
        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        assert any("Could not get download URL" in c for c in calls)

    @pytest.mark.asyncio
    async def test_file_upload_handler_uploads_solution(self, slack_event, say, client):
        """Verify .lean file upload results are uploaded as file attachments."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_FILE_UPLOAD,
            payload={"name": "Foo.lean", "url_private_download": "https://slack.com/files/x"},
        )

        solution = "theorem bar : True := trivial"
        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("aristotlebot.handlers.download_slack_file", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value=solution),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}),
        ):
            await handle_message(slack_event, say, client, classified)

        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["content"] == solution
        assert upload_kwargs["filename"] == "bar.lean"
        assert upload_kwargs["channel"] == "C12345"


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
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- proved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
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
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- proved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
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
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
        ):
            await handle_message(slack_event, say, client, classified)

        calls = [c.kwargs.get("text", c[1].get("text", "")) for c in say.call_args_list]
        error_msgs = [c for c in calls if ":x:" in c]
        assert len(error_msgs) >= 1

    @pytest.mark.asyncio
    async def test_url_handler_uploads_solution(self, slack_event, say, client):
        """Verify URL results are uploaded as file attachments."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://example.com/Foo.lean",
        )

        solution = "lemma my_lemma : True := trivial"
        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value=solution),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
        ):
            await handle_message(slack_event, say, client, classified)

        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["content"] == solution
        assert upload_kwargs["filename"] == "my_lemma.lean"


# ===================================================================
# _post_result helper
# ===================================================================

class TestPostResult:
    """Tests for _post_result — the result posting helper."""

    def test_posts_summary_and_uploads_file_on_success(self, say, client):
        """Success with solution → posts summary + uploads .lean file."""
        result = AristotleResult(
            status="COMPLETE",
            solution_text="theorem foo : True := trivial",
        )

        with patch("aristotlebot.handlers.upload_slack_file") as mock_upload:
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        # Summary posted
        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert ":white_check_mark:" in text
        assert "`foo`" in text
        assert "```lean" not in text

        # File uploaded
        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["content"] == "theorem foo : True := trivial"
        assert upload_kwargs["filename"] == "foo.lean"

    def test_posts_summary_only_on_error(self, say, client):
        """Error result → posts summary, no file upload."""
        result = AristotleResult(
            status="FAILED",
            error="API timeout",
        )

        with patch("aristotlebot.handlers.upload_slack_file") as mock_upload:
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert ":x:" in text
        assert "API timeout" in text
        mock_upload.assert_not_called()

    def test_posts_summary_only_when_no_solution(self, say, client):
        """Complete without solution_text → posts summary, no file upload."""
        result = AristotleResult(status="COMPLETE", solution_text=None)

        with patch("aristotlebot.handlers.upload_slack_file") as mock_upload:
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert "no solution text" in text
        mock_upload.assert_not_called()

    def test_falls_back_on_upload_failure(self, say, client):
        """If file upload fails, still post summary with fallback note."""
        result = AristotleResult(
            status="COMPLETE",
            solution_text="theorem bar : True := trivial",
        )

        with patch("aristotlebot.handlers.upload_slack_file", side_effect=RuntimeError("upload failed")):
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert ":white_check_mark:" in text
        assert "File upload failed" in text


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
        """End-to-end: send NL prompt → Aristotle → Slack thread reply + file attachment."""
        # This would be filled in for integration testing with real credentials
        pass

    @pytest.mark.skipif(True, reason="Requires live Slack credentials")
    @pytest.mark.asyncio
    async def test_full_file_upload_flow(self):
        """End-to-end: upload .lean file → Aristotle → Slack thread reply + file attachment."""
        pass
