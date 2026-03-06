"""Lean 4 playground link generation.

Generates URLs for the Lean 4 web playground at ``live.lean-lang.org``
so users can interactively verify solved Lean code in their browser.

Encoding scheme (reverse-engineered from lean4web source):
    1. Compress the Lean source code using LZ-String's ``compressToBase64``.
    2. Strip trailing ``=`` padding (not needed by LZ-String for decompression
       and interferes with URL fragment parsing).
    3. Place the result in the URL hash fragment: ``#codez=<compressed>``.

Reference:
    - lean4web source: ``client/src/editor/code-atoms.ts`` in
      https://github.com/leanprover-community/lean4web
    - LZ-String: https://github.com/pieroxy/lz-string

Invariants:
    - Generated URLs are always valid and decodable by the playground.
    - Empty or whitespace-only code produces None (no link).
    - The function is pure (no side effects, no network calls).
"""

from __future__ import annotations

import logging

import lzstring

logger = logging.getLogger(__name__)

#: Base URL for the Lean 4 playground.
_PLAYGROUND_BASE = "https://live.lean-lang.org/"


def lean_playground_url(code: str) -> str | None:
    """Generate a Lean 4 playground URL that opens with the given code.

    Preconditions:
        - *code* is a string of Lean 4 source code.

    Postconditions:
        - Returns a full URL string, or None if *code* is empty/whitespace.
        - The URL uses the ``#codez=`` fragment parameter with LZ-String
          base64 compression.
        - The returned URL, when opened in a browser, displays the code
          in the Lean 4 playground editor.

    >>> lean_playground_url("#check Nat.add_comm") is not None
    True
    >>> lean_playground_url("") is None
    True
    >>> lean_playground_url("   ") is None
    True
    """
    if not code or not code.strip():
        return None

    try:
        lz = lzstring.LZString()
        compressed = lz.compressToBase64(code)
        # Strip trailing '=' padding — the playground does this too.
        compressed = compressed.rstrip("=")
        return f"{_PLAYGROUND_BASE}#codez={compressed}"
    except Exception:
        logger.exception("Failed to generate playground URL")
        return None


def decode_playground_url(codez: str) -> str | None:
    """Decode a ``codez=`` parameter back to Lean source code.

    This is the inverse of :func:`lean_playground_url` and is primarily
    useful for testing round-trip correctness.

    Preconditions:
        - *codez* is the value of the ``codez`` URL fragment parameter
          (without the ``codez=`` prefix).

    Postconditions:
        - Returns the original Lean source code, or None on failure.
    """
    try:
        lz = lzstring.LZString()
        return lz.decompressFromBase64(codez)
    except Exception:
        logger.exception("Failed to decode playground URL")
        return None
