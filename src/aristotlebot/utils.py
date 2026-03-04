"""Helpers for downloading files, extracting results, and formatting Slack messages.

Invariants:
- Downloaded files are always written to a temporary directory that the caller manages.
- All network helpers raise on non-200 responses (fail-fast).
- Formatted messages never exceed Slack's 40 000-char limit.
- Solution code is uploaded as .lean file attachments, never posted inline.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import urllib.request
from enum import Enum, auto
from pathlib import Path
from typing import NamedTuple

import aiohttp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------

class MessageKind(Enum):
    """Discriminated union for the three input modes the bot supports.

    Using an enum makes illegal states (e.g. "both a file upload AND a URL")
    unrepresentable at the type level.
    """
    LEAN_FILE_UPLOAD = auto()
    LEAN_URL = auto()
    NATURAL_LANGUAGE = auto()


class ClassifiedMessage(NamedTuple):
    """Result of classifying an incoming Slack message/event."""
    kind: MessageKind
    # For LEAN_FILE_UPLOAD: the Slack file dict; for LEAN_URL: the URL string;
    # for NATURAL_LANGUAGE: the raw message text.
    payload: dict | str


# Regex for URLs that end with .lean (possibly with query params)
_LEAN_URL_RE = re.compile(r"https?://\S+\.lean(?:\?\S*)?(?=#|\s|$)", re.IGNORECASE)

# Slack wraps URLs in angle brackets in event text, e.g. <https://example.com>.
# This regex matches the <URL> pattern so we can strip the brackets before
# running the URL classifier.
_SLACK_ANGLE_BRACKET_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>")


def _strip_slack_angle_brackets(text: str) -> str:
    """Replace Slack's ``<URL>`` and ``<URL|label>`` wrappers with the bare URL.

    Slack's event API wraps URLs in angle brackets, e.g.::

        <https://example.com/file.lean>
        <https://example.com/file.lean|example.com/file.lean>

    This function strips those wrappers so downstream regexes can match the
    raw URL.  Non-URL angle-bracket sequences (e.g. ``<@U12345>``) are left
    untouched because the inner regex requires ``https?://``.

    Postconditions:
        - Every ``<https://…>`` wrapper in the input is replaced by the bare URL.
        - The returned string contains no angle-bracket-wrapped HTTP(S) URLs.
    """
    return _SLACK_ANGLE_BRACKET_RE.sub(r"\1", text)


def classify_message(event: dict) -> ClassifiedMessage:
    """Classify a Slack message event into one of the three input modes.

    Preconditions:
        - *event* is a Slack message event dict (has at least a "text" key or "files" key).

    Postconditions:
        - Returns exactly one ClassifiedMessage whose kind and payload are consistent.
        - Angle-bracket-wrapped URLs (Slack formatting) are handled transparently:
          the returned payload URL never contains surrounding ``<>`` characters.
    """
    # Priority 1: file uploads
    files = event.get("files") or []
    for f in files:
        name = (f.get("name") or "").lower()
        if name.endswith(".lean"):
            return ClassifiedMessage(kind=MessageKind.LEAN_FILE_UPLOAD, payload=f)

    # Priority 2: URLs to .lean files in the message text
    # Strip Slack's angle-bracket URL wrappers before matching.
    text = event.get("text") or ""
    normalized_text = _strip_slack_angle_brackets(text)
    match = _LEAN_URL_RE.search(normalized_text)
    if match:
        return ClassifiedMessage(kind=MessageKind.LEAN_URL, payload=match.group(0))

    # Priority 3: natural language
    return ClassifiedMessage(kind=MessageKind.NATURAL_LANGUAGE, payload=text)


# ---------------------------------------------------------------------------
# File downloading
# ---------------------------------------------------------------------------

async def download_slack_file(
    url: str,
    token: str,
    dest_dir: Path,
    filename: str,
) -> Path:
    """Download a file from Slack's servers using the bot token.

    Preconditions:
        - *url* is a valid Slack file URL (``url_private_download``).
        - *token* is a valid Slack bot token with ``files:read`` scope.
        - *dest_dir* exists and is writable.

    Postconditions:
        - Returns the path to the downloaded file inside *dest_dir*.

    Raises:
        aiohttp.ClientResponseError on non-200 responses.
    """
    assert dest_dir.is_dir(), f"dest_dir must exist: {dest_dir}"
    dest = dest_dir / filename
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            dest.write_bytes(await resp.read())
    return dest


async def download_url(url: str, dest_dir: Path, filename: str | None = None) -> Path:
    """Download a file from an arbitrary URL.

    Preconditions:
        - *url* is a valid HTTP(S) URL.
        - *dest_dir* exists and is writable.

    Postconditions:
        - Returns the path to the downloaded file inside *dest_dir*.

    Raises:
        aiohttp.ClientResponseError on non-200 responses.
    """
    assert dest_dir.is_dir(), f"dest_dir must exist: {dest_dir}"
    if filename is None:
        # Derive filename from URL path
        filename = url.rsplit("/", 1)[-1].split("?")[0] or "input.lean"
    dest = dest_dir / filename
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            dest.write_bytes(await resp.read())
    return dest


# ---------------------------------------------------------------------------
# Temporary directory helper
# ---------------------------------------------------------------------------

def make_temp_dir(prefix: str = "aristotlebot_") -> Path:
    """Create and return a new temporary directory.

    The caller is responsible for cleanup (use ``shutil.rmtree`` or
    ``tempfile.TemporaryDirectory`` context manager externally).
    """
    return Path(tempfile.mkdtemp(prefix=prefix))


# ---------------------------------------------------------------------------
# Aristotle result type
# ---------------------------------------------------------------------------

class AristotleResult(NamedTuple):
    """Result of an Aristotle submission.

    Discriminated by *status*:
        - ``"COMPLETE"`` with ``solution_text`` → successful proof.
        - ``"COMPLETE"`` without ``solution_text`` → completed but no output.
        - ``"FAILED"`` with ``error`` → submission failed.
        - Other status values → in-progress or unknown.

    Invariants:
        - ``error`` is non-None only when the submission raised an exception.
        - ``solution_text`` and ``error`` are never both non-None.
    """
    status: str
    solution_text: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

_SLACK_MAX_TEXT = 39_000  # leave headroom below the 40 000 hard limit

# Regex to extract the first theorem/lemma/def name from Lean source.
# Lean identifiers start with a letter or underscore, followed by alphanumeric/underscore/dot/'.
# We exclude bare colons and other punctuation that appear in anonymous declarations.
_LEAN_DECL_RE = re.compile(
    r"(?:theorem|lemma|def|example)\s+([A-Za-z_][A-Za-z0-9_.']*)"
)


def _extract_theorem_name(solution_text: str) -> str | None:
    """Extract the first theorem/lemma/def name from Lean code.

    Returns None if no named declaration is found. Anonymous declarations
    like ``theorem : True := trivial`` (no name before the colon) return None.

    >>> _extract_theorem_name("theorem foo : True := trivial")
    'foo'
    >>> _extract_theorem_name("-- just a comment") is None
    True
    >>> _extract_theorem_name("theorem : True := trivial") is None
    True
    """
    match = _LEAN_DECL_RE.search(solution_text)
    return match.group(1) if match else None


def _make_solution_filename(solution_text: str | None) -> str:
    """Generate a descriptive filename for a solution .lean file.

    Uses the theorem name if one can be extracted, otherwise falls back
    to ``"solution.lean"``.

    Postconditions:
        - Returned filename always ends with ``.lean``.
        - Filename contains only safe characters (alphanumeric, underscore, dot, hyphen).
    """
    if solution_text:
        name = _extract_theorem_name(solution_text)
        if name:
            # Sanitize: keep only word chars, dots, hyphens
            safe = re.sub(r"[^\w.\-]", "_", name)
            filename = f"{safe}.lean"
            assert filename.endswith(".lean")
            return filename
    return "solution.lean"


def format_result_message(
    *,
    status: str,
    solution_text: str | None = None,
    error: str | None = None,
) -> str:
    """Format an Aristotle result for posting back to Slack (legacy, inline code).

    .. deprecated::
        Use :func:`format_result_summary` for new code. This function embeds
        the full solution inline, which is replaced by file uploads in LEA-24.

    Postconditions:
        - Returned string length ≤ _SLACK_MAX_TEXT.
    """
    if error:
        msg = f":x: *Aristotle failed*\n```\n{error}\n```"
    elif status == "COMPLETE" and solution_text:
        body = solution_text
        if len(body) > _SLACK_MAX_TEXT - 200:
            body = body[: _SLACK_MAX_TEXT - 200] + "\n… (truncated)"
        msg = f":white_check_mark: *Aristotle completed*\n```lean\n{body}\n```"
    elif status == "COMPLETE":
        msg = ":white_check_mark: *Aristotle completed* (no solution text returned)"
    else:
        msg = f":hourglass_flowing_sand: *Status:* `{status}`"

    assert len(msg) <= _SLACK_MAX_TEXT + 1000, "message exceeds safe Slack limit"
    return msg


def format_result_summary(
    *,
    status: str,
    solution_text: str | None = None,
    error: str | None = None,
) -> str:
    """Format a brief summary of an Aristotle result for Slack (no inline code).

    Unlike :func:`format_result_message`, this does NOT embed the full proof.
    The solution code should be uploaded as a ``.lean`` file attachment via
    :func:`upload_slack_file`.

    Postconditions:
        - Returned string is a short summary (< 500 chars).
        - Keeps the ✅/❌ emoji prefix convention.
    """
    if error:
        # Truncate error to first line for the summary
        first_line = error.strip().split("\n", 1)[0]
        if len(first_line) > 200:
            first_line = first_line[:200] + "…"
        msg = f":x: *Aristotle failed* — {first_line}"
    elif status == "COMPLETE" and solution_text:
        theorem_name = _extract_theorem_name(solution_text)
        name_part = f" `{theorem_name}`" if theorem_name else ""
        msg = (
            f":white_check_mark: *Aristotle completed*{name_part}"
            " — proof generated successfully. See attached `.lean` file."
        )
    elif status == "COMPLETE":
        msg = ":white_check_mark: *Aristotle completed* (no solution text returned)"
    else:
        msg = f":hourglass_flowing_sand: *Status:* `{status}`"

    assert len(msg) < 500, f"summary too long ({len(msg)} chars)"
    return msg


# ---------------------------------------------------------------------------
# Slack file upload (two-step external upload API)
# ---------------------------------------------------------------------------

def upload_slack_file(
    client,
    *,
    content: str,
    filename: str,
    channel: str,
    thread_ts: str,
    title: str | None = None,
) -> None:
    """Upload text content to Slack as a file attachment using the external upload API.

    Uses the two-step Slack file upload flow:
        1. ``files.getUploadURLExternal`` — obtain a presigned upload URL and file ID.
        2. HTTP POST the file content to the presigned URL.
        3. ``files.completeUploadExternal`` — finalize and share the file in the
           specified channel and thread.

    Preconditions:
        - *client* is a synchronous Slack WebClient with ``files:write`` scope.
        - *content* is non-empty UTF-8 text.
        - *channel* and *thread_ts* identify a valid Slack thread.

    Postconditions:
        - The file appears as an attachment in the specified Slack thread.

    Raises:
        ``slack_sdk.errors.SlackApiError`` if any Slack API call fails.
        ``urllib.error.URLError`` if the presigned URL upload fails.
    """
    assert content, "cannot upload empty file content"
    assert filename, "filename must be non-empty"

    content_bytes = content.encode("utf-8")

    # Step 1: Get a presigned upload URL from Slack
    upload_response = client.files_getUploadURLExternal(
        filename=filename,
        length=len(content_bytes),
    )
    upload_url = upload_response["upload_url"]
    file_id = upload_response["file_id"]

    logger.debug(
        "Got upload URL for file_id=%s, filename=%s, length=%d",
        file_id, filename, len(content_bytes),
    )

    # Step 2: POST the file content to the presigned URL
    req = urllib.request.Request(
        upload_url,
        data=content_bytes,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200, (
            f"Presigned URL upload failed with status {resp.status}"
        )

    # Step 3: Finalize and share the file in the channel/thread
    client.files_completeUploadExternal(
        files=[{"id": file_id, "title": title or filename}],
        channel_id=channel,
        thread_ts=thread_ts,
    )

    logger.info(
        "Uploaded solution file %s (file_id=%s) to channel=%s thread=%s",
        filename, file_id, channel, thread_ts,
    )


def read_solution_file(path: Path) -> str | None:
    """Read a solution file, returning its text content or None if missing.

    Handles both plain .lean files and .tar.gz archives (reads the first .lean
    file found inside the archive).
    """
    if not path.exists():
        return None

    if path.suffix == ".lean":
        return path.read_text(encoding="utf-8", errors="replace")

    # For tar.gz, extract and read the first .lean file
    if path.name.endswith(".tar.gz") or path.name.endswith(".tgz"):
        import tarfile
        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".lean") and member.isfile():
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        return extracted.read().decode("utf-8", errors="replace")
        return None

    # Fallback: try reading as text
    return path.read_text(encoding="utf-8", errors="replace")
