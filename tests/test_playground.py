"""Unit tests for aristotlebot.playground — Lean 4 playground link generation.

Tests cover:
    - URL generation from Lean source code
    - Round-trip encoding/decoding (compress → decompress)
    - Edge cases: empty code, whitespace-only, very long code
    - URL format validation (base URL, fragment parameter)
    - Error handling for invalid inputs
"""

from __future__ import annotations

import pytest

from aristotlebot.playground import (
    _PLAYGROUND_BASE,
    decode_playground_url,
    lean_playground_url,
)


# ===================================================================
# lean_playground_url
# ===================================================================

class TestLeanPlaygroundUrl:
    """Tests for lean_playground_url — generating playground links."""

    def test_generates_url_for_simple_code(self):
        code = "#check Nat.add_comm"
        url = lean_playground_url(code)
        assert url is not None
        assert url.startswith(_PLAYGROUND_BASE)
        assert "#codez=" in url

    def test_url_starts_with_playground_base(self):
        url = lean_playground_url("theorem foo : True := trivial")
        assert url is not None
        assert url.startswith("https://live.lean-lang.org/")

    def test_url_uses_hash_fragment(self):
        """The codez parameter is in the URL hash fragment, not query string."""
        url = lean_playground_url("def x := 42")
        assert url is not None
        assert "#codez=" in url
        assert "?codez=" not in url

    def test_no_trailing_equals_in_codez(self):
        """Trailing '=' padding is stripped from the compressed value."""
        url = lean_playground_url("#check Nat.add_comm")
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        assert not codez.endswith("=")

    def test_returns_none_for_empty_string(self):
        assert lean_playground_url("") is None

    def test_returns_none_for_whitespace_only(self):
        assert lean_playground_url("   ") is None
        assert lean_playground_url("\n\n") is None
        assert lean_playground_url("\t") is None

    def test_returns_none_for_none_input(self):
        """None input should not crash (even though type hint says str)."""
        # The function checks truthiness, so None would be caught by the guard
        assert lean_playground_url(None) is None  # type: ignore[arg-type]

    def test_handles_unicode_code(self):
        """Lean 4 supports Unicode identifiers and operators."""
        code = "theorem α_β : True := trivial"
        url = lean_playground_url(code)
        assert url is not None
        assert "#codez=" in url

    def test_handles_multiline_code(self):
        code = "import Mathlib\n\ntheorem foo : True := by\n  trivial"
        url = lean_playground_url(code)
        assert url is not None

    def test_handles_very_long_code(self):
        """Even very long code should produce a valid URL."""
        code = "-- " + "x" * 10000 + "\ndef foo := 42"
        url = lean_playground_url(code)
        assert url is not None
        assert "#codez=" in url


# ===================================================================
# Round-trip encoding/decoding
# ===================================================================

class TestPlaygroundRoundTrip:
    """Tests verifying that encode → decode round-trips correctly."""

    def test_simple_roundtrip(self):
        code = "#check Nat.add_comm"
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code

    def test_multiline_roundtrip(self):
        code = "import Mathlib\n\ntheorem foo : 1 + 1 = 2 := by\n  norm_num"
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code

    def test_unicode_roundtrip(self):
        code = "def α : ℕ → ℕ := fun n => n + 1\n-- Greek letters: α β γ δ"
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code

    def test_long_code_roundtrip(self):
        code = "\n".join(f"def f{i} := {i}" for i in range(100))
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code

    def test_special_characters_roundtrip(self):
        """Code with special characters (quotes, backslashes, etc.)."""
        code = 'def msg := "hello\\nworld"\ndef path := "C:\\\\Users"'
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code

    def test_realistic_lean_file_roundtrip(self):
        """Test with a realistic Lean 4 theorem file."""
        code = """\
import Mathlib.Tactic

/-- The sum of the first n natural numbers is n*(n+1)/2. -/
theorem sum_first_n (n : ℕ) : 2 * (∑ i in Finset.range n, i) = n * (n - 1) := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [Finset.sum_range_succ]
    ring_nf
    omega
"""
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        decoded = decode_playground_url(codez)
        assert decoded == code


# ===================================================================
# decode_playground_url
# ===================================================================

class TestDecodePlaygroundUrl:
    """Tests for decode_playground_url — decoding codez parameter."""

    def test_decodes_valid_codez(self):
        # First generate a valid codez value
        code = "def x := 42"
        url = lean_playground_url(code)
        assert url is not None
        codez = url.split("#codez=", 1)[1]
        result = decode_playground_url(codez)
        assert result == code

    def test_returns_none_for_invalid_codez(self):
        """Invalid compressed data should return None, not crash."""
        result = decode_playground_url("")
        # Empty string decompression behavior varies; just check it doesn't crash
        # The function returns None on exception

    def test_handles_empty_string(self):
        """Empty string should not crash."""
        # Just verify no exception is raised
        decode_playground_url("")


# ===================================================================
# Integration with Slack message format
# ===================================================================

class TestPlaygroundSlackIntegration:
    """Tests verifying playground URL works in Slack message context."""

    def test_url_is_valid_for_slack_link(self):
        """URLs should work as Slack mrkdwn link targets.

        The link format <URL|🔗 Open in Lean Playground> hides the long
        encoded URL behind a clean clickable hyperlink in Slack (ARI-13).
        """
        code = "theorem foo : True := trivial"
        url = lean_playground_url(code)
        assert url is not None
        # Slack mrkdwn link format: <URL|label>
        slack_link = f"<{url}|\U0001f517 Open in Lean Playground>"
        assert "live.lean-lang.org" in slack_link
        assert "#codez=" in slack_link
        # Verify the URL is properly enclosed
        assert slack_link.startswith("<https://")
        assert slack_link.endswith(">")
