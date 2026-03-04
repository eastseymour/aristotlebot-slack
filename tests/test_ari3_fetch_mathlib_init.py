"""Tests for ARI-3: Verify Mathlib/Init.lean is fetchable and contains expected content.

This test validates that the Mathlib4 Init.lean file can be fetched via HTTP
and contains the expected structure (public imports, linter set registrations).

Invariants:
    - The URL must return a 200 status code.
    - The content must contain 'public import' statements (it's a root import file).
    - The content must contain the mathlibStandardSet linter registration.
"""

from __future__ import annotations

import subprocess

import pytest

MATHLIB_INIT_URL = (
    "https://raw.githubusercontent.com/leanprover-community/mathlib4"
    "/master/Mathlib/Init.lean"
)


class TestFetchMathlibInit:
    """Tests that Mathlib/Init.lean is fetchable and well-formed."""

    @pytest.fixture
    def init_lean_content(self) -> str:
        """Fetch Mathlib/Init.lean content via curl."""
        result = subprocess.run(
            ["curl", "-fsSL", MATHLIB_INIT_URL],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"curl failed with return code {result.returncode}: {result.stderr}"
        )
        return result.stdout

    def test_curl_returns_content(self, init_lean_content: str) -> None:
        """The URL must return non-empty content."""
        assert len(init_lean_content) > 0, "Fetched content is empty"

    def test_contains_public_imports(self, init_lean_content: str) -> None:
        """Init.lean is the root import file; it must contain public import statements."""
        assert "public import" in init_lean_content, (
            "Expected 'public import' statements in Mathlib/Init.lean"
        )

    def test_contains_mathlib_standard_linter_set(self, init_lean_content: str) -> None:
        """Init.lean registers the mathlibStandardSet linter set."""
        assert "linter.mathlibStandardSet" in init_lean_content, (
            "Expected 'linter.mathlibStandardSet' registration in Init.lean"
        )

    def test_contains_root_file_docstring(self, init_lean_content: str) -> None:
        """Init.lean should document that it is the root file in Mathlib."""
        assert "root file in Mathlib" in init_lean_content, (
            "Expected documentation about being the root file"
        )

    def test_imports_linter_modules(self, init_lean_content: str) -> None:
        """Init.lean should import various Mathlib linter modules."""
        expected_imports = [
            "Mathlib.Tactic.Linter.DocPrime",
            "Mathlib.Tactic.Linter.Header",
            "Mathlib.Tactic.Linter.Style",
        ]
        for imp in expected_imports:
            assert imp in init_lean_content, (
                f"Expected import of {imp} in Init.lean"
            )

    def test_is_lean4_syntax(self, init_lean_content: str) -> None:
        """The file should use Lean 4 syntax (module keyword, public import, etc.)."""
        # Lean 4 uses 'public import' (not 'import' alone as in Lean 3)
        # and has register_linter_set commands
        assert "register_linter_set" in init_lean_content, (
            "Expected Lean 4 'register_linter_set' command"
        )
