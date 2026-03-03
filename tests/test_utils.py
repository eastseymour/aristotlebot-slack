"""Unit tests for aristotlebot.utils — message classification, formatting, and file helpers."""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.aristotlebot.utils import (
    ClassifiedMessage,
    MessageKind,
    classify_message,
    download_slack_file,
    download_url,
    format_result_message,
    make_temp_dir,
    read_solution_file,
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
# format_result_message
# ===================================================================

class TestFormatResultMessage:
    """Tests for format_result_message — Slack message formatting."""

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

        with patch("src.aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
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

        with patch("src.aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
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

        with patch("src.aristotlebot.utils.aiohttp.ClientSession", mock_session_cls):
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
