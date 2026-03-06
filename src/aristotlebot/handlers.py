"""Slack message handlers for the three input modes.

Each handler follows the same contract:
    1. Acknowledge the message with an ⏳ reaction.
    2. Download/prepare input.
    3. Submit to Aristotle via aristotlelib.
    4. Wait for completion.
    5. Post the result summary in-thread and upload the solution as a .lean file.
    6. Clean up temporary files.

Invariants:
    - Temporary directories are always cleaned up, even on failure.
    - Every user-facing error is posted back in the thread (never silently swallowed).
    - Solution code is uploaded as .lean file attachments, never posted inline.

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

from .lean_imports import (
    ResolvedImports,
    extract_github_repo_info,
    format_import_context,
    resolve_imports,
)
from .playground import lean_playground_url
from .utils import (
    AristotleResult,
    ClassifiedMessage,
    MessageKind,
    _make_solution_filename,
    download_slack_file,
    download_url,
    format_result_summary,
    make_temp_dir,
    read_solution_file,
    upload_slack_file,
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
# Result posting helper
# ---------------------------------------------------------------------------

def _post_result(
    say,
    client,
    *,
    channel: str,
    thread_ts: str,
    result: AristotleResult,
) -> None:
    """Post an Aristotle result: summary message + .lean file attachment + playground link.

    For successful completions with solution text:
        - Posts a brief summary message (no inline code).
        - Uploads the solution as a .lean file attachment in the same thread.
        - Generates and appends a Lean 4 playground link for interactive verification.

    For errors or empty results:
        - Posts only the summary message (no file upload, no playground link).

    Preconditions:
        - *result* is a valid AristotleResult.
        - *say* and *client* are synchronous Slack bolt helpers.
    """
    summary = format_result_summary(
        status=result.status,
        solution_text=result.solution_text,
        error=result.error,
    )

    # Upload solution as .lean file if we have solution text
    if result.status == "COMPLETE" and result.solution_text:
        filename = _make_solution_filename(result.solution_text)
        try:
            upload_slack_file(
                client,
                content=result.solution_text,
                filename=filename,
                channel=channel,
                thread_ts=thread_ts,
                title=filename,
            )
        except Exception:
            logger.exception("Failed to upload solution file; falling back to summary only")
            summary += "\n_(File upload failed; solution not attached.)_"

        # Generate Lean 4 playground link for interactive verification
        playground_url = lean_playground_url(result.solution_text)
        if playground_url:
            summary += f"\n:link: <{playground_url}|Open in Lean 4 Playground>"

    say(text=summary, thread_ts=thread_ts)


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

        # For file uploads, we have no repo info so imports can't be resolved.
        # Still parse and report any imports found (ARI-6).
        source = input_path.read_text(encoding="utf-8", errors="replace")
        resolved = await _resolve_imports_safe(source, repo_info=None)
        context_paths = _write_context_files(resolved, tmp_dir)

        if resolved.unresolved:
            _report_import_status(say, thread_ts, resolved)

        result = await _run_aristotle_formal(
            input_path, tmp_dir, context_file_paths=context_paths,
        )
        _post_result(say, client, channel=channel, thread_ts=thread_ts, result=result)

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
    """Download .lean file from URL, resolve imports, submit to Aristotle, return the proof."""
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

        # Resolve Lean 4 imports from the downloaded file (ARI-6).
        source = input_path.read_text(encoding="utf-8", errors="replace")
        repo_info = extract_github_repo_info(url)
        resolved = await _resolve_imports_safe(source, repo_info)
        context_paths = _write_context_files(resolved, tmp_dir)

        # Report import resolution status in thread.
        _report_import_status(say, thread_ts, resolved)

        result = await _run_aristotle_formal(
            input_path, tmp_dir, context_file_paths=context_paths,
        )
        _post_result(say, client, channel=channel, thread_ts=thread_ts, result=result)

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

        result = await _run_aristotle_informal(text, tmp_dir)
        _post_result(say, client, channel=channel, thread_ts=thread_ts, result=result)

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

async def _run_aristotle_formal(
    input_path: Path,
    tmp_dir: Path,
    *,
    context_file_paths: list[Path] | None = None,
) -> AristotleResult:
    """Submit a .lean file to Aristotle (formal mode) and return the result.

    When context_file_paths are provided, uses the lower-level Project API
    to explicitly set project_root=tmp_dir, ensuring that relative file paths
    sent to the API match the Lean import paths (e.g. ``ArkLib/Data/Fin/Basic.lean``
    rather than just ``Basic.lean``).

    Args:
        input_path: Path to the main .lean file.
        tmp_dir: Temporary directory for output.  Also used as the project root
            when context files are provided.
        context_file_paths: Optional list of resolved dependency files to
            include as context (ARI-6).  These must be laid out in *tmp_dir*
            with their repo-relative paths preserved.

    Postconditions:
        - Always returns an AristotleResult (never raises to the caller).

    Invariant:
        - When context files are provided, project_root is always tmp_dir so
          that ``file.relative_to(project_root)`` yields the correct import path.
    """
    output_path = tmp_dir / "solution.lean"
    try:
        if context_file_paths:
            # Use lower-level API for precise project_root control.
            # The high-level prove_from_file() auto-computes project_root
            # as the common parent of context files, which gives wrong
            # relative paths when all context files share a deep prefix.
            project = await Project.create(
                project_input_type=ProjectInputType.FORMAL_LEAN,
                validate_lean_project_root=False,
            )

            await project.add_context(
                context_file_paths=context_file_paths,
                validate_lean_project_root=False,
                project_root=tmp_dir,
            )
            logger.info(
                "Added %d context files with project_root=%s",
                len(context_file_paths), tmp_dir,
            )

            await project.solve(input_file_path=input_path)

            result_path_str = await project.wait_for_completion(
                output_file_path=output_path,
            )
        else:
            # No context files — use the simpler high-level API.
            result_path_str = await Project.prove_from_file(
                input_file_path=input_path,
                validate_lean_project=False,
                auto_add_imports=False,
                wait_for_completion=True,
                output_file_path=output_path,
                project_input_type=ProjectInputType.FORMAL_LEAN,
            )

        result_path = Path(result_path_str)
        solution_text = read_solution_file(result_path)
        return AristotleResult(status="COMPLETE", solution_text=solution_text)
    except Exception as exc:
        logger.exception("Aristotle formal submission failed")
        return AristotleResult(status="FAILED", error=str(exc))


async def _run_aristotle_informal(prompt: str, tmp_dir: Path) -> AristotleResult:
    """Submit a natural language prompt to Aristotle (informal mode) and return the result.

    Postconditions:
        - Always returns an AristotleResult (never raises to the caller).
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
        return AristotleResult(status="COMPLETE", solution_text=solution_text)
    except Exception as exc:
        logger.exception("Aristotle informal submission failed")
        return AristotleResult(status="FAILED", error=str(exc))


# ---------------------------------------------------------------------------
# Import resolution helpers (ARI-6)
# ---------------------------------------------------------------------------

async def _resolve_imports_safe(
    source: str,
    repo_info: "GitHubRepoInfo | None",
) -> ResolvedImports:
    """Resolve imports with error handling. Never raises.

    If import resolution fails entirely, returns an empty ResolvedImports
    so the bot can still submit the file without context.
    """
    try:
        return await resolve_imports(source, repo_info)
    except Exception:
        logger.exception("Import resolution failed; proceeding without context")
        return ResolvedImports()


def _write_context_files(
    resolved: ResolvedImports,
    tmp_dir: Path,
) -> list[Path]:
    """Write resolved import files to disk and return their paths.

    Each resolved file is written to a subdirectory matching its relative
    path within the repository (e.g. ``ArkLib/Data/Fin/Basic.lean``).

    Returns:
        List of Paths to the written files (empty if none resolved).
    """
    paths: list[Path] = []
    for rel_path, content in resolved.resolved_files.items():
        dest = tmp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        paths.append(dest)
    return paths


def _report_import_status(
    say,
    thread_ts: str,
    resolved: ResolvedImports,
) -> None:
    """Post a brief status message about import resolution in the thread."""
    if not resolved.resolved_files and not resolved.unresolved:
        return  # No imports found; nothing to report.

    parts: list[str] = []

    if resolved.resolved_files:
        parts.append(
            f":package: Resolved {resolved.total_fetched} import(s) "
            f"as context (depth {resolved.depth_reached})"
        )

    # Summarize unresolved imports by category.
    external = [u for u in resolved.unresolved if "external package" in u.reason]
    other_unresolved = [u for u in resolved.unresolved if "external package" not in u.reason]

    if external:
        pkg_names = sorted({u.module_path.split(".")[0] for u in external})
        parts.append(
            f":memo: External dependencies (not fetched): {', '.join(pkg_names)}"
        )

    if other_unresolved:
        parts.append(
            f":warning: {len(other_unresolved)} import(s) could not be resolved"
        )

    if parts:
        say(text="\n".join(parts), thread_ts=thread_ts)


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
