"""Unit tests for aristotlebot.handlers — mock aristotlelib and Slack."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aristotlebot.handlers import (
    handle_message,
    _detect_api_error,
    _post_result,
    _report_import_status,
    _resolve_imports_safe,
    _write_context_files,
)
from aristotlebot.lean_imports import ResolvedImports, UnresolvedImport
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
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
            patch.object(Path, "read_text", return_value="-- mock lean source"),
        ):
            await handle_message(slack_event, say, client, classified)

        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["content"] == solution
        assert upload_kwargs["filename"] == "my_lemma.lean"

    @pytest.mark.asyncio
    async def test_github_blob_url_passes_through_to_download_url(self, slack_event, say, client):
        """Regression test: GitHub blob URL is passed to download_url which converts it.

        The URL handler passes the raw URL to download_url; the conversion to
        raw.githubusercontent.com happens inside download_url itself (via
        _github_blob_to_raw). This test verifies the handler passes the URL
        correctly and the full flow succeeds.
        """
        github_blob_url = (
            "https://github.com/Verified-zkEVM/ArkLib/blob/main/"
            "ArkLib/Data/Fin/Sigma.lean"
        )
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload=github_blob_url,
        )

        solution = "def sigma_equiv : True := trivial"
        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Sigma.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value=solution),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file") as mock_upload,
            patch.object(Path, "read_text", return_value="-- mock lean source"),
        ):
            await handle_message(slack_event, say, client, classified)

        # download_url should receive the GitHub blob URL (it handles conversion internally)
        mock_download.assert_called_once()
        call_kwargs = mock_download.call_args.kwargs
        assert call_kwargs["url"] == github_blob_url

        # Aristotle should be called
        mock_prove.assert_called_once()

        # Solution should be uploaded
        mock_upload.assert_called_once()


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
# Import resolution integration (ARI-6)
# ===================================================================

class TestImportResolutionInUrlHandler:
    """Tests verifying import resolution is integrated into the URL handler."""

    @pytest.mark.asyncio
    async def test_url_handler_resolves_imports(self, slack_event, say, client):
        """URL handler should resolve imports and pass context to Aristotle.

        When context files are present, the handler uses the lower-level
        Project API (create + add_context + solve) instead of prove_from_file.
        """
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/File.lean",
        )

        lean_source = "import ArkLib.Core\ntheorem foo : True := trivial"
        dep_content = "def core := 1"

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/File.lean"))

        resolved = ResolvedImports(
            resolved_files={"ArkLib/Core.lean": dep_content},
            total_fetched=1,
            depth_reached=1,
        )

        # Mock the lower-level Project API used when context files are present
        mock_project = AsyncMock()
        mock_project.wait_for_completion = AsyncMock(return_value="/tmp/solution.lean")
        mock_create = AsyncMock(return_value=mock_project)

        with (
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.create", mock_create),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
            patch("aristotlebot.handlers.resolve_imports", AsyncMock(return_value=resolved)),
            patch("aristotlebot.handlers.extract_github_repo_info", return_value=MagicMock()),
            patch.object(Path, "read_text", return_value=lean_source),
            patch("aristotlebot.handlers._write_context_files", return_value=[
                Path("/tmp/aristotlebot_test/ArkLib/Core.lean"),
            ]),
        ):
            await handle_message(slack_event, say, client, classified)

        # Project.create should have been called (lower-level API for context)
        mock_create.assert_called_once()
        # add_context should have been called with the context files
        mock_project.add_context.assert_called_once()
        ctx_kwargs = mock_project.add_context.call_args.kwargs
        assert ctx_kwargs["project_root"] == Path("/tmp/aristotlebot_test")
        # solve should have been called
        mock_project.solve.assert_called_once()

    @pytest.mark.asyncio
    async def test_url_handler_reports_import_status(self, slack_event, say, client):
        """URL handler should post import status to thread."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/File.lean",
        )

        resolved = ResolvedImports(
            resolved_files={"ArkLib/Core.lean": "-- core"},
            unresolved=[UnresolvedImport("Mathlib.Tactic", "external package: Mathlib")],
            total_fetched=1,
            depth_reached=1,
        )

        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/File.lean"))

        # Mock the lower-level Project API (used when context files present)
        mock_project = AsyncMock()
        mock_project.wait_for_completion = AsyncMock(return_value="/tmp/solution.lean")
        mock_create = AsyncMock(return_value=mock_project)

        with (
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.create", mock_create),
            patch("aristotlebot.handlers.read_solution_file", return_value="-- solved"),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
            patch("aristotlebot.handlers.resolve_imports", AsyncMock(return_value=resolved)),
            patch("aristotlebot.handlers.extract_github_repo_info", return_value=MagicMock()),
            patch.object(Path, "read_text", return_value="import ArkLib.Core"),
            patch("aristotlebot.handlers._write_context_files", return_value=[
                Path("/tmp/aristotlebot_test/ArkLib/Core.lean"),
            ]),
        ):
            await handle_message(slack_event, say, client, classified)

        # Should have posted import status (contains "Resolved" or "External")
        all_texts = [
            c.kwargs.get("text", c[1].get("text", ""))
            for c in say.call_args_list
        ]
        import_msgs = [t for t in all_texts if "import" in t.lower() or "Resolved" in t or "External" in t]
        assert len(import_msgs) >= 1

    @pytest.mark.asyncio
    async def test_url_handler_continues_without_imports(self, slack_event, say, client):
        """If import resolution fails, the handler should still submit to Aristotle."""
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
            patch("aristotlebot.handlers.resolve_imports", AsyncMock(side_effect=RuntimeError("boom"))),
            patch("aristotlebot.handlers.extract_github_repo_info", return_value=None),
            patch.object(Path, "read_text", return_value="-- no imports"),
        ):
            await handle_message(slack_event, say, client, classified)

        # Aristotle should still be called (graceful degradation)
        mock_prove.assert_called_once()


class TestWriteContextFiles:
    """Tests for _write_context_files helper."""

    def test_writes_resolved_files_to_disk(self, tmp_path):
        resolved = ResolvedImports(
            resolved_files={
                "ArkLib/Core.lean": "def core := 1",
                "ArkLib/Data/Fin/Basic.lean": "def fin_basic := 2",
            },
            total_fetched=2,
        )

        paths = _write_context_files(resolved, tmp_path)
        assert len(paths) == 2

        # Check files exist and have correct content
        core_path = tmp_path / "ArkLib" / "Core.lean"
        assert core_path.exists()
        assert core_path.read_text() == "def core := 1"

        basic_path = tmp_path / "ArkLib" / "Data" / "Fin" / "Basic.lean"
        assert basic_path.exists()
        assert basic_path.read_text() == "def fin_basic := 2"

    def test_empty_resolved_returns_empty_list(self, tmp_path):
        resolved = ResolvedImports()
        paths = _write_context_files(resolved, tmp_path)
        assert paths == []


class TestReportImportStatus:
    """Tests for _report_import_status helper."""

    def test_reports_resolved_count(self):
        say = MagicMock()
        resolved = ResolvedImports(
            resolved_files={"A.lean": "-- a", "B.lean": "-- b"},
            total_fetched=2,
            depth_reached=1,
        )
        _report_import_status(say, "ts", resolved)
        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert "2 import(s)" in text

    def test_reports_external_dependencies(self):
        say = MagicMock()
        resolved = ResolvedImports(
            unresolved=[
                UnresolvedImport("Mathlib.Tactic", "external package: Mathlib"),
                UnresolvedImport("Std.Data.HashMap", "external package: Std"),
            ],
        )
        _report_import_status(say, "ts", resolved)
        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert "External dependencies" in text
        assert "Mathlib" in text
        assert "Std" in text

    def test_no_imports_does_not_post(self):
        say = MagicMock()
        resolved = ResolvedImports()
        _report_import_status(say, "ts", resolved)
        say.assert_not_called()


class TestResolveImportsSafe:
    """Tests for _resolve_imports_safe — error-handling wrapper."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        """If resolve_imports raises, returns empty ResolvedImports."""
        with patch("aristotlebot.handlers.resolve_imports", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _resolve_imports_safe("import Foo", MagicMock())

        assert result.total_fetched == 0
        assert result.unresolved == []

    @pytest.mark.asyncio
    async def test_passes_through_on_success(self):
        expected = ResolvedImports(
            resolved_files={"A.lean": "-- a"},
            total_fetched=1,
        )
        with patch("aristotlebot.handlers.resolve_imports", AsyncMock(return_value=expected)):
            result = await _resolve_imports_safe("import A", MagicMock())

        assert result.total_fetched == 1


# ===================================================================
# Integration test structure
# ===================================================================

# ===================================================================
# Playground link in results
# ===================================================================

class TestPlaygroundLinkInResults:
    """Tests verifying playground links are included in successful results."""

    def test_success_result_includes_playground_link(self, say, client):
        """Successful proof should include a Lean Playground link."""
        result = AristotleResult(
            status="COMPLETE",
            solution_text="theorem foo : True := trivial",
        )

        with patch("aristotlebot.handlers.upload_slack_file"):
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        say.assert_called_once()
        text = say.call_args.kwargs["text"]
        assert "live.lean-lang.org" in text
        assert "Open in Lean Playground" in text
        assert "#codez=" in text

    def test_error_result_does_not_include_playground_link(self, say, client):
        """Error results should NOT include a playground link."""
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
        assert "live.lean-lang.org" not in text
        mock_upload.assert_not_called()

    def test_no_solution_does_not_include_playground_link(self, say, client):
        """Complete without solution should NOT include a playground link."""
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
        assert "live.lean-lang.org" not in text

    def test_playground_link_is_slack_formatted(self, say, client):
        """Playground link should use Slack mrkdwn link format: <URL|label>.

        The link emoji (🔗) must be INSIDE the <URL|label> so the entire
        element renders as a single clean clickable hyperlink in Slack,
        hiding the long encoded playground URL (ARI-13).
        """
        result = AristotleResult(
            status="COMPLETE",
            solution_text="theorem bar : 1 + 1 = 2 := rfl",
        )

        with patch("aristotlebot.handlers.upload_slack_file"):
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        text = say.call_args.kwargs["text"]
        # Slack mrkdwn link format: <URL|🔗 Open in Lean Playground>
        assert "<https://live.lean-lang.org/#codez=" in text
        assert "|\U0001f517 Open in Lean Playground>" in text
        # The raw URL should NOT appear outside the <> brackets
        assert "Open in Lean Playground: https://" not in text

    def test_playground_link_no_raw_url_visible(self, say, client):
        """Verify the raw playground URL is never displayed outside <> brackets.

        Postcondition: The long encoded URL is always wrapped in Slack's
        <URL|label> syntax so users see only the clean label text.
        """
        result = AristotleResult(
            status="COMPLETE",
            solution_text="theorem baz : True := trivial",
        )

        with patch("aristotlebot.handlers.upload_slack_file"):
            _post_result(
                say, client,
                channel="C12345",
                thread_ts="1234567890.123456",
                result=result,
            )

        text = say.call_args.kwargs["text"]
        # Every occurrence of the playground base URL must be inside <...>
        import re
        playground_mentions = re.findall(r"live\.lean-lang\.org", text)
        assert len(playground_mentions) >= 1, "Playground link should be present"
        # All playground URLs must be inside <URL|label> brackets
        naked_urls = re.findall(r"(?<![<])https://live\.lean-lang\.org", text)
        assert len(naked_urls) == 0, "Playground URL must be inside <URL|label> Slack link"


# ===================================================================
# Error detection in API responses (ARI-14)
# ===================================================================

class TestDetectApiError:
    """Tests for _detect_api_error — detecting Aristotle errors in output files."""

    def test_detects_aristotle_error_message(self):
        """Error sentinel in solution text should be detected."""
        text = "Aristotle encountered an error processing this file."
        error = _detect_api_error(text)
        assert error is not None
        assert "Aristotle encountered an error" in error

    def test_detects_error_case_insensitive(self):
        """Error detection should be case-insensitive."""
        text = "ARISTOTLE ENCOUNTERED AN ERROR processing this file"
        error = _detect_api_error(text)
        assert error is not None

    def test_detects_internal_server_error(self):
        text = "Internal server error: something went wrong"
        error = _detect_api_error(text)
        assert error is not None
        assert "Internal server error" in error

    def test_returns_none_for_valid_solution(self):
        """Valid Lean code should not be detected as an error."""
        text = "theorem foo : True := trivial"
        assert _detect_api_error(text) is None

    def test_returns_none_for_none_input(self):
        assert _detect_api_error(None) is None

    def test_returns_none_for_empty_string(self):
        assert _detect_api_error("") is None

    def test_truncates_long_error(self):
        """Very long error messages should be truncated."""
        text = "Aristotle encountered an error: " + "x" * 500
        error = _detect_api_error(text)
        assert error is not None
        assert len(error) <= 305  # 300 + "…"

    def test_returns_first_line_of_multiline_error(self):
        """Should return only the first line as error summary."""
        text = "Aristotle encountered an error\nLine 2 details\nLine 3 details"
        error = _detect_api_error(text)
        assert error is not None
        assert "Line 2" not in error

    def test_error_in_formal_mode_posts_failure(self, slack_event, say, client):
        """When Aristotle returns an error file, the bot should report failure."""
        classified = ClassifiedMessage(
            kind=MessageKind.LEAN_URL,
            payload="https://example.com/Foo.lean",
        )

        error_output = "Aristotle encountered an error processing this file."
        mock_download = AsyncMock(return_value=Path("/tmp/aristotlebot_test/Foo.lean"))
        mock_prove = AsyncMock(return_value="/tmp/solution.lean")

        with (
            patch("aristotlebot.handlers.download_url", mock_download),
            patch("aristotlebot.handlers.Project.prove_from_file", mock_prove),
            patch("aristotlebot.handlers.read_solution_file", return_value=error_output),
            patch("aristotlebot.handlers.make_temp_dir", return_value=Path("/tmp/aristotlebot_test")),
            patch("aristotlebot.handlers.shutil.rmtree"),
            patch("aristotlebot.handlers.upload_slack_file"),
            patch("aristotlebot.handlers.resolve_imports", AsyncMock(side_effect=RuntimeError("no repo"))),
            patch("aristotlebot.handlers.extract_github_repo_info", return_value=None),
            patch.object(Path, "read_text", return_value="-- no imports"),
        ):
            import asyncio
            asyncio.run(handle_message(slack_event, say, client, classified))

        # Should have posted an error message, not a success
        all_texts = [
            c.kwargs.get("text", c[1].get("text", ""))
            for c in say.call_args_list
        ]
        error_msgs = [t for t in all_texts if ":x:" in t]
        success_msgs = [t for t in all_texts if ":white_check_mark:" in t]
        assert len(error_msgs) >= 1, "Should post error when API returns error file"
        assert len(success_msgs) == 0, "Should NOT post success when API returns error file"


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
