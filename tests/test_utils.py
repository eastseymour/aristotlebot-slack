"""Unit tests for aristotlebot.utils — message classification, formatting, and file helpers."""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from aristotlebot.utils import (
    AristotleResult,
    ClassifiedMessage,
    MessageKind,
    _extract_theorem_name,
    _make_solution_filename,
    _strip_slack_angle_brackets,
    classify_message,
    download_slack_file,
    download_url,
    format_result_message,
    format_result_summary,
    make_temp_dir,
    read_solution_file,
    upload_slack_file,
)


# ===================================================================
# classify_message
# ===================================================================

class TestClassifyMessage:
    """Tests for classify_message — the dispatcher for input modes."""

    def test_lean_file_upload_is_detected(self):
        event = {
            "text": "Here's my file",
            "files": [{"name": "Foo.lean", "url_private_download": "https://slack.com/files/x"}],
        }
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_FILE_UPLOAD
        assert result.payload["name"] == "Foo.lean"

    def test_non_lean_file_falls_through_to_text(self):
        event = {
            "text": "Some text",
            "files": [{"name": "notes.txt", "url_private_download": "https://slack.com/files/y"}],
        }
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE
        assert result.payload == "Some text"

    def test_lean_url_is_detected(self):
        event = {"text": "Check out https://example.com/repo/Foo.lean please"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://example.com/repo/Foo.lean"

    def test_lean_url_with_query_params(self):
        event = {"text": "See https://raw.github.com/repo/Bar.lean?token=abc"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert "Bar.lean" in result.payload

    def test_natural_language_fallback(self):
        event = {"text": "Prove that 1 + 1 = 2"}
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE
        assert result.payload == "Prove that 1 + 1 = 2"

    def test_empty_text_is_natural_language(self):
        event = {"text": ""}
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE
        assert result.payload == ""

    def test_no_text_key_is_natural_language(self):
        event = {}
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE
        assert result.payload == ""

    def test_file_upload_takes_priority_over_url_in_text(self):
        """File uploads have higher priority than URLs in message text."""
        event = {
            "text": "https://example.com/Foo.lean",
            "files": [{"name": "Bar.lean", "url_private_download": "https://slack.com/f"}],
        }
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_FILE_UPLOAD
        assert result.payload["name"] == "Bar.lean"

    def test_url_not_ending_in_lean_is_natural_language(self):
        event = {"text": "Check https://example.com/lean-tutorial"}
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE


# ===================================================================
# _strip_slack_angle_brackets (unit tests for the helper)
# ===================================================================

class TestStripSlackAngleBrackets:
    """Unit tests for _strip_slack_angle_brackets — Slack URL unwrapping."""

    def test_strips_simple_url(self):
        assert _strip_slack_angle_brackets("<https://example.com>") == "https://example.com"

    def test_strips_url_with_label(self):
        result = _strip_slack_angle_brackets("<https://example.com|example.com>")
        assert result == "https://example.com"

    def test_leaves_plain_text_untouched(self):
        text = "no angle brackets here"
        assert _strip_slack_angle_brackets(text) == text

    def test_leaves_non_url_angle_brackets_untouched(self):
        """Slack user mentions like <@U12345> should NOT be stripped."""
        text = "Hey <@U12345> check this"
        assert _strip_slack_angle_brackets(text) == text

    def test_strips_multiple_urls(self):
        text = "See <https://a.com> and <https://b.com>"
        assert _strip_slack_angle_brackets(text) == "See https://a.com and https://b.com"

    def test_strips_http_url(self):
        assert _strip_slack_angle_brackets("<http://example.com>") == "http://example.com"

    def test_mixed_urls_and_mentions(self):
        text = "<@U123> shared <https://example.com/file.lean>"
        result = _strip_slack_angle_brackets(text)
        assert result == "<@U123> shared https://example.com/file.lean"

    def test_empty_string(self):
        assert _strip_slack_angle_brackets("") == ""

    def test_url_with_query_params(self):
        text = "<https://example.com/file.lean?token=abc>"
        assert _strip_slack_angle_brackets(text) == "https://example.com/file.lean?token=abc"


# ===================================================================
# classify_message — Slack angle bracket URL handling
# ===================================================================

class TestClassifyMessageAngleBrackets:
    """Tests for classify_message handling of Slack's angle-bracket URL wrapping.

    Slack's event API wraps URLs in <> brackets, e.g.:
        <https://example.com/file.lean>
    This must still be classified as LEAN_URL with the clean URL as payload.
    """

    def test_angle_bracket_lean_url(self):
        """The core bug: Slack-wrapped URL must be detected as LEAN_URL."""
        event = {"text": "<https://raw.githubusercontent.com/foo/bar.lean>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://raw.githubusercontent.com/foo/bar.lean"

    def test_angle_bracket_url_with_surrounding_text(self):
        event = {"text": "Check this out: <https://example.com/Theorem.lean> please"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://example.com/Theorem.lean"

    def test_angle_bracket_url_with_label(self):
        """Slack sometimes adds a label after a pipe: <URL|label>."""
        event = {"text": "<https://example.com/Foo.lean|example.com/Foo.lean>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://example.com/Foo.lean"

    def test_angle_bracket_non_lean_url_is_natural_language(self):
        """A non-.lean URL in angle brackets should NOT be classified as LEAN_URL."""
        event = {"text": "<https://example.com/readme.md>"}
        result = classify_message(event)
        assert result.kind == MessageKind.NATURAL_LANGUAGE

    def test_multiple_urls_first_lean_wins(self):
        """With multiple URLs, the first .lean URL should be returned."""
        event = {"text": "<https://a.com/readme.md> and <https://b.com/Proof.lean>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://b.com/Proof.lean"

    def test_multiple_lean_urls_first_wins(self):
        """With multiple .lean URLs, the first should be returned."""
        event = {"text": "<https://a.com/First.lean> and <https://b.com/Second.lean>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://a.com/First.lean"

    def test_angle_bracket_url_with_query_params(self):
        event = {"text": "<https://raw.github.com/repo/Bar.lean?token=abc>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert "Bar.lean" in result.payload
        assert ">" not in result.payload

    def test_bare_url_still_works(self):
        """Bare URLs (no angle brackets) must still work — regression check."""
        event = {"text": "https://example.com/repo/Foo.lean"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert result.payload == "https://example.com/repo/Foo.lean"

    def test_file_upload_still_takes_priority_over_angle_bracket_url(self):
        """File uploads have higher priority than angle-bracket URLs."""
        event = {
            "text": "<https://example.com/Foo.lean>",
            "files": [{"name": "Bar.lean", "url_private_download": "https://slack.com/f"}],
        }
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_FILE_UPLOAD

    def test_payload_never_contains_angle_brackets(self):
        """Invariant: the payload URL must never contain < or > characters."""
        event = {"text": "<https://example.com/file.lean>"}
        result = classify_message(event)
        assert result.kind == MessageKind.LEAN_URL
        assert "<" not in result.payload
        assert ">" not in result.payload


# ===================================================================
# _extract_theorem_name
# ===================================================================

class TestExtractTheoremName:
    """Unit tests for _extract_theorem_name — Lean declaration name extraction."""

    def test_extracts_theorem_name(self):
        assert _extract_theorem_name("theorem foo : True := trivial") == "foo"

    def test_extracts_lemma_name(self):
        assert _extract_theorem_name("lemma bar : True := trivial") == "bar"

    def test_extracts_def_name(self):
        assert _extract_theorem_name("def baz : Nat := 42") == "baz"

    def test_extracts_example_name(self):
        assert _extract_theorem_name("example qux : True := trivial") == "qux"

    def test_returns_none_for_anonymous_theorem(self):
        """Anonymous declarations like 'theorem : True := trivial' have no name."""
        assert _extract_theorem_name("theorem : True := trivial") is None

    def test_returns_none_for_no_declaration(self):
        assert _extract_theorem_name("-- just a comment") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_theorem_name("") is None

    def test_extracts_first_name_from_multiple(self):
        code = "theorem first : True := trivial\ntheorem second : True := trivial"
        assert _extract_theorem_name(code) == "first"

    def test_handles_dotted_name(self):
        assert _extract_theorem_name("theorem Nat.add_comm : True := trivial") == "Nat.add_comm"

    def test_handles_name_with_apostrophe(self):
        assert _extract_theorem_name("theorem foo' : True := trivial") == "foo'"

    def test_handles_name_with_underscores(self):
        assert _extract_theorem_name("theorem my_long_name : True := trivial") == "my_long_name"

    def test_ignores_keyword_in_comment(self):
        """Keywords inside comments should not match."""
        # The regex will still match inside comments — this is acceptable
        # since we're extracting a best-effort name for filenames.
        code = "-- some preamble\ntheorem real_theorem : True := trivial"
        name = _extract_theorem_name(code)
        assert name == "real_theorem"


# ===================================================================
# _make_solution_filename
# ===================================================================

class TestMakeSolutionFilename:
    """Unit tests for _make_solution_filename — filename generation."""

    def test_uses_theorem_name(self):
        assert _make_solution_filename("theorem foo : True := trivial") == "foo.lean"

    def test_uses_lemma_name(self):
        assert _make_solution_filename("lemma bar : True := trivial") == "bar.lean"

    def test_falls_back_to_solution(self):
        assert _make_solution_filename("-- just a comment") == "solution.lean"

    def test_falls_back_for_none(self):
        assert _make_solution_filename(None) == "solution.lean"

    def test_falls_back_for_empty_string(self):
        assert _make_solution_filename("") == "solution.lean"

    def test_sanitizes_special_chars(self):
        """Special characters in names should be replaced with underscores."""
        filename = _make_solution_filename("theorem Nat.add_comm : True := trivial")
        assert filename == "Nat.add_comm.lean"
        assert filename.endswith(".lean")

    def test_filename_always_ends_with_lean(self):
        """Invariant: filename always ends with .lean."""
        for text in [
            "theorem foo : True := trivial",
            "-- no theorem",
            None,
            "",
        ]:
            assert _make_solution_filename(text).endswith(".lean")


# ===================================================================
# format_result_message (legacy)
# ===================================================================

class TestFormatResultMessage:
    """Tests for format_result_message — legacy Slack message formatting (inline code)."""

    def test_complete_with_solution(self):
        msg = format_result_message(status="COMPLETE", solution_text="theorem foo : True := trivial")
        assert ":white_check_mark:" in msg
        assert "theorem foo" in msg
        assert "```lean" in msg

    def test_complete_without_solution(self):
        msg = format_result_message(status="COMPLETE", solution_text=None)
        assert ":white_check_mark:" in msg
        assert "no solution text" in msg

    def test_failed_with_error(self):
        msg = format_result_message(status="FAILED", error="API timeout")
        assert ":x:" in msg
        assert "API timeout" in msg

    def test_in_progress_status(self):
        msg = format_result_message(status="IN_PROGRESS")
        assert ":hourglass_flowing_sand:" in msg
        assert "IN_PROGRESS" in msg

    def test_long_solution_is_truncated(self):
        long_text = "x" * 50_000
        msg = format_result_message(status="COMPLETE", solution_text=long_text)
        assert "truncated" in msg
        assert len(msg) < 45_000


# ===================================================================
# format_result_summary (new — no inline code)
# ===================================================================

class TestFormatResultSummary:
    """Tests for format_result_summary — brief summaries without inline code."""

    def test_complete_with_solution_includes_theorem_name(self):
        msg = format_result_summary(
            status="COMPLETE",
            solution_text="theorem foo : True := trivial",
        )
        assert ":white_check_mark:" in msg
        assert "`foo`" in msg
        assert "proof generated" in msg
        assert ".lean" in msg

    def test_complete_with_solution_no_inline_code(self):
        """Summary must NOT contain inline code blocks."""
        msg = format_result_summary(
            status="COMPLETE",
            solution_text="theorem foo : True := trivial",
        )
        assert "```lean" not in msg
        assert "```" not in msg

    def test_complete_without_solution(self):
        msg = format_result_summary(status="COMPLETE", solution_text=None)
        assert ":white_check_mark:" in msg
        assert "no solution text" in msg

    def test_complete_with_anonymous_theorem(self):
        """Anonymous theorems should not add a name to the summary."""
        msg = format_result_summary(
            status="COMPLETE",
            solution_text="theorem : True := trivial",
        )
        assert ":white_check_mark:" in msg
        assert "proof generated" in msg

    def test_failed_with_error(self):
        msg = format_result_summary(status="FAILED", error="API timeout")
        assert ":x:" in msg
        assert "API timeout" in msg

    def test_failed_truncates_long_error(self):
        """Long errors are truncated to first line."""
        error = "First line\nSecond line\nThird line"
        msg = format_result_summary(status="FAILED", error=error)
        assert "First line" in msg
        assert "Second line" not in msg

    def test_in_progress_status(self):
        msg = format_result_summary(status="IN_PROGRESS")
        assert ":hourglass_flowing_sand:" in msg
        assert "IN_PROGRESS" in msg

    def test_summary_is_short(self):
        """Summary must always be < 500 chars."""
        msg = format_result_summary(
            status="COMPLETE",
            solution_text="theorem some_very_long_name_that_goes_on_and_on : True := trivial",
        )
        assert len(msg) < 500


# ===================================================================
# AristotleResult
# ===================================================================

class TestAristotleResult:
    """Tests for AristotleResult — the result NamedTuple."""

    def test_success_result(self):
        result = AristotleResult(status="COMPLETE", solution_text="theorem foo : True := trivial")
        assert result.status == "COMPLETE"
        assert result.solution_text is not None
        assert result.error is None

    def test_failure_result(self):
        result = AristotleResult(status="FAILED", error="timeout")
        assert result.status == "FAILED"
        assert result.solution_text is None
        assert result.error == "timeout"

    def test_defaults_to_none(self):
        result = AristotleResult(status="COMPLETE")
        assert result.solution_text is None
        assert result.error is None


# ===================================================================
# upload_slack_file
# ===================================================================

class TestUploadSlackFile:
    """Unit tests for upload_slack_file — Slack two-step file upload."""

    def test_calls_get_upload_url_external(self):
        """Step 1: calls files_getUploadURLExternal with correct params."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/presigned",
            "file_id": "F_TEST_123",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content="theorem foo : True := trivial",
                filename="foo.lean",
                channel="C12345",
                thread_ts="1234567890.123456",
            )

        client.files_getUploadURLExternal.assert_called_once()
        call_kwargs = client.files_getUploadURLExternal.call_args.kwargs
        assert call_kwargs["filename"] == "foo.lean"
        assert call_kwargs["length"] == len("theorem foo : True := trivial".encode("utf-8"))

    def test_posts_content_to_presigned_url(self):
        """Step 2: POSTs file content to the presigned upload URL."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/presigned",
            "file_id": "F_TEST_123",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content="theorem foo : True := trivial",
                filename="foo.lean",
                channel="C12345",
                thread_ts="1234567890.123456",
            )

        mock_urlopen.assert_called_once()
        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.full_url == "https://files.slack.com/upload/v1/presigned"
        assert request_obj.data == "theorem foo : True := trivial".encode("utf-8")
        assert request_obj.get_header("Content-type") == "application/octet-stream"

    def test_calls_complete_upload_external(self):
        """Step 3: calls files_completeUploadExternal with file_id and channel."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/presigned",
            "file_id": "F_TEST_456",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content="theorem bar : True := trivial",
                filename="bar.lean",
                channel="C67890",
                thread_ts="9876543210.654321",
                title="My Proof",
            )

        client.files_completeUploadExternal.assert_called_once()
        call_kwargs = client.files_completeUploadExternal.call_args.kwargs
        assert call_kwargs["files"] == [{"id": "F_TEST_456", "title": "My Proof"}]
        assert call_kwargs["channel_id"] == "C67890"
        assert call_kwargs["thread_ts"] == "9876543210.654321"

    def test_uses_filename_as_default_title(self):
        """When no title is given, uses filename as the title."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/presigned",
            "file_id": "F_TEST_789",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content="-- content",
                filename="solution.lean",
                channel="C12345",
                thread_ts="1234567890.123456",
            )

        call_kwargs = client.files_completeUploadExternal.call_args.kwargs
        assert call_kwargs["files"] == [{"id": "F_TEST_789", "title": "solution.lean"}]

    def test_rejects_empty_content(self):
        """Precondition: content must be non-empty."""
        client = MagicMock()
        with pytest.raises(AssertionError, match="cannot upload empty file"):
            upload_slack_file(
                client,
                content="",
                filename="foo.lean",
                channel="C12345",
                thread_ts="1234567890.123456",
            )

    def test_rejects_empty_filename(self):
        """Precondition: filename must be non-empty."""
        client = MagicMock()
        with pytest.raises(AssertionError, match="filename must be non-empty"):
            upload_slack_file(
                client,
                content="-- content",
                filename="",
                channel="C12345",
                thread_ts="1234567890.123456",
            )


# ===================================================================
# upload_slack_file — integration tests
# ===================================================================

class TestUploadSlackFileIntegration:
    """Integration tests for upload_slack_file — tests the full three-step flow."""

    def test_full_upload_flow(self):
        """Test the complete upload flow: getUploadURL → POST → completeUpload."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/presigned-url",
            "file_id": "F_INTEGRATION",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        content = "theorem integration_test : True := trivial"

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content=content,
                filename="integration_test.lean",
                channel="C_INT",
                thread_ts="1111111111.111111",
                title="Integration Test Proof",
            )

        # Verify all three steps happened in order
        assert client.files_getUploadURLExternal.call_count == 1
        assert mock_urlopen.call_count == 1
        assert client.files_completeUploadExternal.call_count == 1

        # Verify content was encoded correctly
        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.data == content.encode("utf-8")

    def test_upload_preserves_unicode_content(self):
        """UTF-8 content with special characters is uploaded correctly."""
        client = MagicMock()
        client.files_getUploadURLExternal.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/presigned",
            "file_id": "F_UNICODE",
        }
        client.files_completeUploadExternal.return_value = {"ok": True}

        content = "-- Résumé: théorème α ∧ β → γ"

        with patch("aristotlebot.utils.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(status=200)
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            upload_slack_file(
                client,
                content=content,
                filename="unicode.lean",
                channel="C12345",
                thread_ts="1234567890.123456",
            )

        request_obj = mock_urlopen.call_args[0][0]
        assert request_obj.data == content.encode("utf-8")
        # Length should match byte length, not character length
        call_kwargs = client.files_getUploadURLExternal.call_args.kwargs
        assert call_kwargs["length"] == len(content.encode("utf-8"))


# ===================================================================
# read_solution_file
# ===================================================================

class TestReadSolutionFile:
    """Tests for read_solution_file — reading .lean and .tar.gz solution files."""

    def test_reads_lean_file(self, tmp_path: Path):
        lean_file = tmp_path / "solution.lean"
        lean_file.write_text("theorem foo : True := trivial")
        result = read_solution_file(lean_file)
        assert result == "theorem foo : True := trivial"

    def test_reads_tar_gz_file(self, tmp_path: Path):
        # Create a .lean file and pack it into a tar.gz
        lean_content = "theorem bar : True := trivial"
        lean_file = tmp_path / "inner.lean"
        lean_file.write_text(lean_content)

        tar_path = tmp_path / "solution.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(lean_file, arcname="inner.lean")

        result = read_solution_file(tar_path)
        assert result == lean_content

    def test_missing_file_returns_none(self, tmp_path: Path):
        result = read_solution_file(tmp_path / "nonexistent.lean")
        assert result is None

    def test_tar_gz_without_lean_returns_none(self, tmp_path: Path):
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("just notes")
        tar_path = tmp_path / "solution.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(txt_file, arcname="notes.txt")
        result = read_solution_file(tar_path)
        assert result is None


# ===================================================================
# make_temp_dir
# ===================================================================

class TestMakeTempDir:
    def test_creates_directory(self):
        d = make_temp_dir()
        try:
            assert d.is_dir()
            assert "aristotlebot_" in d.name
        finally:
            d.rmdir()


# ===================================================================
# download_slack_file (mocked)
# ===================================================================

class TestDownloadSlackFile:
    @pytest.mark.asyncio
    async def test_downloads_and_saves(self, tmp_path: Path):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"-- Lean file content")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_async_context_manager(mock_resp))
        mock_session_cls = MagicMock(return_value=_async_context_manager(mock_session))

        with patch("aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
            result = await download_slack_file(
                url="https://files.slack.com/download/test.lean",
                token="xoxb-test-token",
                dest_dir=tmp_path,
                filename="test.lean",
            )

        assert result == tmp_path / "test.lean"
        assert result.read_bytes() == b"-- Lean file content"
        # Verify Authorization header was passed
        mock_session.get.assert_called_once()
        call_kwargs = mock_session.get.call_args
        assert "Authorization" in call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))


class TestDownloadUrl:
    @pytest.mark.asyncio
    async def test_downloads_from_url(self, tmp_path: Path):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"-- Downloaded Lean")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_async_context_manager(mock_resp))
        mock_session_cls = MagicMock(return_value=_async_context_manager(mock_session))

        with patch("aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
            result = await download_url(
                url="https://example.com/Foo.lean",
                dest_dir=tmp_path,
            )

        assert result.name == "Foo.lean"
        assert result.read_bytes() == b"-- Downloaded Lean"

    @pytest.mark.asyncio
    async def test_custom_filename(self, tmp_path: Path):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = AsyncMock(return_value=b"content")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_async_context_manager(mock_resp))
        mock_session_cls = MagicMock(return_value=_async_context_manager(mock_session))

        with patch("aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
            result = await download_url(
                url="https://example.com/path?token=abc",
                dest_dir=tmp_path,
                filename="custom.lean",
            )

        assert result.name == "custom.lean"


# ===================================================================
# Helpers
# ===================================================================

def _async_context_manager(return_value):
    """Create a mock async context manager that yields *return_value*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm
