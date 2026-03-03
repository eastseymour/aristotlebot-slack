"""Slack message handlers for the three input modes.

Each handler follows the same contract:
    1. Acknowledge the message with an ⏳ reaction.
    2. Download/prepare input.
    3. Submit to Aristotle via aristotlelib.
    4. Wait for completion.
    5. Post the result back in-thread.
    6. Clean up temporary files.

Invariants:
    - Temporary directories are always cleaned up, even on failure.
    - Every user-facing error is posted back in the thread (never silently swallowed).

Note:
    Slack Bolt's ``say`` and ``client`` are synchronous even when called from
    async handlers (via ``asyncio.run``). All say/client calls are therefore
    synchronous; only aristotlelib and download calls are awaited.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from aristotlelib.project import Project, ProjectInputType, ProjectStatus

from .utils import (
    ClassifiedMessage,
    MessageKind,
    download_slack_file,
    download_url,
    format_result_message,
    make_temp_dir,
    read_solution_file,
)

logger = logging.getLogger(__name__)


async def handle_message(event: dict, say, client, classified: ClassifiedMessage) -> None:
    """Dispatch to the appropriate handler based on message classification.

    Preconditions:
        - *classified* is a valid ClassifiedMessage.
        - *say* and *client* are Slack bolt helpers (synchronous).

    This is the single entry point called from app.py.
    """
    dispatch = {
        MessageKind.LEAN_FILE_UPLOAD: _handle_lean_file_upload,
        MessageKind.LEAN_URL: _handle_lean_url,
        MessageKind.NATURAL_LANGUAGE: _handle_natural_language,
    }

    handler = dispatch[classified.kind]
    await handler(event, say, client, classified)


# ---------------------------------------------------------------------------
# Handler: .lean file upload
# ---------------------------------------------------------------------------

async def _handle_lean_file_upload(
    event: dict,
    say,
    client,
    classified: ClassifiedMessage,
) -> None:
    """Download uploaded .lean file, submit to Aristotle, return the proof."""
    assert classified.kind == MessageKind.LEAN_FILE_UPLOAD
    file_info: dict = classified.payload  # type: ignore[assignment]
    channel = event["channel"]
    thread_ts = event.get("ts", "")
    token = os.environ["SLACK_BOT_TOKEN"]

    # React to acknowledge
    _add_reaction(client, channel, thread_ts, "hourglass_flowing_sand")

    tmp_dir = make_temp_dir()
    try:
        filename = file_info.get("name", "input.lean")
        download_url_str = file_info.get("url_private_download", "")
        if not download_url_str:
            say(
                text=":x: Could not get download URL for the uploaded file.",
                thread_ts=thread_ts,
            )
            return

        say(
            text=f":hourglass_flowing_sand: Downloading `{filename}` and submitting to Aristotle…",
            thread_ts=thread_ts,
        )

        input_path = await download_slack_file(
            url=download_url_str,
            token=token,
            dest_dir=tmp_dir,
            filename=filename,
        )

        result_text = await _run_aristotle_formal(input_path, tmp_dir)
        say(text=result_text, thread_ts=thread_ts)

    except Exception:
        logger.exception("Error handling .lean file upload")
        say(
            text=":x: An error occurred while processing the uploaded file.",
            thread_ts=thread_ts,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _remove_reaction(client, channel, thread_ts, "hourglass_flowing_sand")


# ---------------------------------------------------------------------------
# Handler: URL to .lean file
# ---------------------------------------------------------------------------

async def _handle_lean_url(
    event: dict,
    say,
    client,
    classified: ClassifiedMessage,
) -> None:
    """Download .lean file from URL, submit to Aristotle, return the proof."""
    assert classified.kind == MessageKind.LEAN_URL
    url: str = classified.payload  # type: ignore[assignment]
    channel = event["channel"]
    thread_ts = event.get("ts", "")

    _add_reaction(client, channel, thread_ts, "hourglass_flowing_sand")

    tmp_dir = make_temp_dir()
    try:
        say(
            text=f":hourglass_flowing_sand: Downloading `{url}` and submitting to Aristotle…",
            thread_ts=thread_ts,
        )

        input_path = await download_url(url=url, dest_dir=tmp_dir)

        result_text = await _run_aristotle_formal(input_path, tmp_dir)
        say(text=result_text, thread_ts=thread_ts)

    except Exception:
        logger.exception("Error handling .lean URL")
        say(
            text=f":x: An error occurred while processing the URL: `{url}`",
            thread_ts=thread_ts,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _remove_reaction(client, channel, thread_ts, "hourglass_flowing_sand")


# ---------------------------------------------------------------------------
# Handler: natural language
# ---------------------------------------------------------------------------

async def _handle_natural_language(
    event: dict,
    say,
    client,
    classified: ClassifiedMessage,
) -> None:
    """Submit natural language prompt to Aristotle in informal mode."""
    assert classified.kind == MessageKind.NATURAL_LANGUAGE
    text: str = classified.payload  # type: ignore[assignment]
    channel = event["channel"]
    thread_ts = event.get("ts", "")

    if not text.strip():
        return  # Ignore empty messages

    _add_reaction(client, channel, thread_ts, "hourglass_flowing_sand")

    tmp_dir = make_temp_dir()
    try:
        say(
            text=":hourglass_flowing_sand: Submitting to Aristotle…",
            thread_ts=thread_ts,
        )

        result_text = await _run_aristotle_informal(text, tmp_dir)
        say(text=result_text, thread_ts=thread_ts)

    except Exception:
        logger.exception("Error handling natural language message")
        say(
            text=":x: An error occurred while processing your message.",
            thread_ts=thread_ts,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _remove_reaction(client, channel, thread_ts, "hourglass_flowing_sand")


# ---------------------------------------------------------------------------
# Aristotle submission helpers
# ---------------------------------------------------------------------------

async def _run_aristotle_formal(input_path: Path, tmp_dir: Path) -> str:
    """Submit a .lean file to Aristotle (formal mode) and return a formatted result message.

    Postconditions:
        - Always returns a user-friendly string (never raises to the caller).
    """
    output_path = tmp_dir / "solution.lean"
    try:
        result_path_str = await Project.prove_from_file(
            input_file_path=input_path,
            validate_lean_project=False,
            wait_for_completion=True,
            output_file_path=output_path,
            project_input_type=ProjectInputType.FORMAL_LEAN,
        )
        result_path = Path(result_path_str)
        solution_text = read_solution_file(result_path)
        return format_result_message(status="COMPLETE", solution_text=solution_text)
    except Exception as exc:
        logger.exception("Aristotle formal submission failed")
        return format_result_message(status="FAILED", error=str(exc))


async def _run_aristotle_informal(prompt: str, tmp_dir: Path) -> str:
    """Submit a natural language prompt to Aristotle (informal mode) and return a formatted result.

    Postconditions:
        - Always returns a user-friendly string (never raises to the caller).
    """
    output_path = tmp_dir / "solution.lean"
    try:
        result_path_str = await Project.prove_from_file(
            input_content=prompt,
            wait_for_completion=True,
            output_file_path=output_path,
            project_input_type=ProjectInputType.INFORMAL,
        )
        result_path = Path(result_path_str)
        solution_text = read_solution_file(result_path)
        return format_result_message(status="COMPLETE", solution_text=solution_text)
    except Exception as exc:
        logger.exception("Aristotle informal submission failed")
        return format_result_message(status="FAILED", error=str(exc))


# ---------------------------------------------------------------------------
# Slack reaction helpers (best-effort, never raise)
# ---------------------------------------------------------------------------

def _add_reaction(client, channel: str, timestamp: str, name: str) -> None:
    """Add an emoji reaction to a message. Silently ignores errors."""
    try:
        client.reactions_add(
            channel=channel,
            timestamp=timestamp,
            name=name,
        )
    except Exception:
        logger.debug("Failed to add reaction %s", name, exc_info=True)


def _remove_reaction(client, channel: str, timestamp: str, name: str) -> None:
    """Remove an emoji reaction from a message. Silently ignores errors."""
    try:
        client.reactions_remove(
            channel=channel,
            timestamp=timestamp,
            name=name,
        )
    except Exception:
        logger.debug("Failed to remove reaction %s", name, exc_info=True)
