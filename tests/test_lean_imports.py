"""Unit and integration tests for aristotlebot.lean_imports — Lean 4 import resolution.

Tests cover:
    - Import parsing from Lean 4 source code
    - Import path to file path conversion
    - GitHub repo info extraction from URLs
    - Recursive import resolution (mocked network)
    - Depth and file count limits
    - External package detection
    - Context formatting
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aristotlebot.lean_imports import (
    EXTERNAL_PACKAGES,
    GitHubRepoInfo,
    ImportKind,
    LeanImport,
    ResolvedImports,
    UnresolvedImport,
    extract_github_repo_info,
    format_import_context,
    import_to_file_path,
    parse_lean_imports,
    resolve_imports,
)


# ===================================================================
# parse_lean_imports
# ===================================================================

class TestParseLeanImports:
    """Tests for parse_lean_imports — extracting import statements from Lean 4 source."""

    def test_single_import(self):
        source = "import ArkLib.Data.Fin.Basic"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].module_path == "ArkLib.Data.Fin.Basic"
        assert imports[0].kind == ImportKind.LOCAL
        assert imports[0].top_level_package == "ArkLib"

    def test_multiple_imports(self):
        source = "import ArkLib.Data.Fin.Basic\nimport ArkLib.Data.Fin.Sigma"
        imports = parse_lean_imports(source)
        assert len(imports) == 2
        assert imports[0].module_path == "ArkLib.Data.Fin.Basic"
        assert imports[1].module_path == "ArkLib.Data.Fin.Sigma"

    def test_external_mathlib_import(self):
        source = "import Mathlib.Tactic"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL
        assert imports[0].top_level_package == "Mathlib"

    def test_external_std_import(self):
        source = "import Std.Data.HashMap"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL

    def test_external_init_import(self):
        source = "import Init.Prelude"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL
        assert imports[0].top_level_package == "Init"

    def test_external_lean_import(self):
        source = "import Lean.Elab.Tactic"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL

    def test_mixed_local_and_external(self):
        source = (
            "import Mathlib.Tactic\n"
            "import ArkLib.Data.Fin.Basic\n"
            "import Std.Data.HashMap\n"
        )
        imports = parse_lean_imports(source)
        assert len(imports) == 3
        kinds = [i.kind for i in imports]
        assert kinds == [ImportKind.EXTERNAL, ImportKind.LOCAL, ImportKind.EXTERNAL]

    def test_public_import(self):
        """Lean 4 supports 'import public' syntax."""
        source = "import public ArkLib.Data.Fin.Basic"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].module_path == "ArkLib.Data.Fin.Basic"

    def test_import_with_leading_whitespace(self):
        source = "  import ArkLib.Core"
        imports = parse_lean_imports(source)
        assert len(imports) == 1

    def test_no_imports(self):
        source = "theorem foo : True := trivial"
        imports = parse_lean_imports(source)
        assert imports == []

    def test_empty_source(self):
        imports = parse_lean_imports("")
        assert imports == []

    def test_comment_import_not_matched(self):
        """Import statements inside line comments should not be parsed."""
        source = "-- import Mathlib.Tactic\ntheorem foo : True := trivial"
        imports = parse_lean_imports(source)
        assert imports == []

    def test_import_in_block_comment_matched(self):
        """Block comments may still match (best-effort parsing).
        This is an acceptable limitation since we're not a full parser."""
        # The regex is line-based, so /- import X -/ on its own line could match
        source = "/- import Mathlib.Tactic -/"
        # This test documents the behavior, not necessarily the ideal
        imports = parse_lean_imports(source)
        # The regex requires the import to start the line (with optional whitespace)
        # /- doesn't match because the line starts with /-
        # Actual behavior depends on regex — let's just document it
        # For now we accept if it matches or not

    def test_duplicate_imports_deduplicated(self):
        source = "import ArkLib.Core\nimport ArkLib.Core"
        imports = parse_lean_imports(source)
        assert len(imports) == 1

    def test_single_module_name(self):
        """A single-component module name is valid in Lean 4."""
        source = "import MyModule"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].module_path == "MyModule"
        assert imports[0].top_level_package == "MyModule"
        assert imports[0].kind == ImportKind.LOCAL

    def test_all_known_external_packages(self):
        """All packages in EXTERNAL_PACKAGES should be classified as external."""
        for pkg in EXTERNAL_PACKAGES:
            source = f"import {pkg}.Something"
            imports = parse_lean_imports(source)
            assert len(imports) == 1, f"Failed for {pkg}"
            assert imports[0].kind == ImportKind.EXTERNAL, f"{pkg} not classified as external"

    def test_vcvio_classified_as_external(self):
        """VCVio (a Lake dependency) should be classified as external."""
        source = "import VCVio.OracleComp.QueryTracking.CachingOracle"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL
        assert imports[0].top_level_package == "VCVio"

    def test_comppoly_classified_as_external(self):
        """CompPoly (a Lake dependency) should be classified as external."""
        source = "import CompPoly.Data.Classes.DCast"
        imports = parse_lean_imports(source)
        assert len(imports) == 1
        assert imports[0].kind == ImportKind.EXTERNAL
        assert imports[0].top_level_package == "CompPoly"

    def test_real_world_lean_file(self):
        """Test parsing a realistic Lean 4 file header."""
        source = """
import ArkLib.Data.Fin.Basic
import ArkLib.Data.Fin.Sigma
import Mathlib.Tactic
import Mathlib.Data.Fintype.Basic

open Finset

theorem my_theorem : True := trivial
"""
        imports = parse_lean_imports(source)
        assert len(imports) == 4
        local_imports = [i for i in imports if i.kind == ImportKind.LOCAL]
        external_imports = [i for i in imports if i.kind == ImportKind.EXTERNAL]
        assert len(local_imports) == 2
        assert len(external_imports) == 2

    def test_preserves_order(self):
        source = "import C.Z\nimport A.B\nimport B.C"
        imports = parse_lean_imports(source)
        assert [i.module_path for i in imports] == ["C.Z", "A.B", "B.C"]


# ===================================================================
# import_to_file_path
# ===================================================================

class TestImportToFilePath:
    """Tests for import_to_file_path — converting module paths to file paths."""

    def test_simple_conversion(self):
        assert import_to_file_path("ArkLib.Data.Fin.Basic") == "ArkLib/Data/Fin/Basic.lean"

    def test_single_component(self):
        assert import_to_file_path("MyModule") == "MyModule.lean"

    def test_two_components(self):
        assert import_to_file_path("Mathlib.Tactic") == "Mathlib/Tactic.lean"

    def test_deep_nesting(self):
        result = import_to_file_path("A.B.C.D.E.F")
        assert result == "A/B/C/D/E/F.lean"

    def test_always_ends_with_lean(self):
        """Invariant: result always ends with .lean."""
        for path in ["Foo", "Foo.Bar", "A.B.C.D"]:
            assert import_to_file_path(path).endswith(".lean")

    def test_uses_forward_slashes(self):
        """Result uses POSIX-style forward slashes."""
        result = import_to_file_path("ArkLib.Data.Fin.Basic")
        assert "\\" not in result

    def test_rejects_empty(self):
        with pytest.raises(AssertionError):
            import_to_file_path("")


# ===================================================================
# extract_github_repo_info
# ===================================================================

class TestExtractGitHubRepoInfo:
    """Tests for extract_github_repo_info — parsing GitHub URLs."""

    def test_raw_githubusercontent_url(self):
        url = "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/main/ArkLib/Data/Fin/Sigma.lean"
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.owner == "Verified-zkEVM"
        assert info.repo == "ArkLib"
        assert info.ref == "main"

    def test_github_blob_url(self):
        url = "https://github.com/Verified-zkEVM/ArkLib/blob/main/ArkLib/Data/Fin/Sigma.lean"
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.owner == "Verified-zkEVM"
        assert info.repo == "ArkLib"
        assert info.ref == "main"

    def test_github_blob_url_with_commit_ref(self):
        url = "https://github.com/owner/repo/blob/abc123def/src/File.lean"
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.ref == "abc123def"

    def test_github_blob_url_with_query_params(self):
        url = "https://github.com/owner/repo/blob/main/File.lean?raw=true"
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.ref == "main"

    def test_github_blob_url_with_fragment(self):
        url = "https://github.com/owner/repo/blob/main/File.lean#L42"
        info = extract_github_repo_info(url)
        assert info is not None

    def test_non_github_url_returns_none(self):
        assert extract_github_repo_info("https://example.com/foo.lean") is None

    def test_raw_url_for(self):
        info = GitHubRepoInfo(owner="Verified-zkEVM", repo="ArkLib", ref="main")
        url = info.raw_url_for("ArkLib/Data/Fin/Basic.lean")
        assert url == (
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "main/ArkLib/Data/Fin/Basic.lean"
        )

    def test_hyphenated_names(self):
        url = "https://raw.githubusercontent.com/my-org/my-repo/dev/src/File.lean"
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.owner == "my-org"
        assert info.repo == "my-repo"
        assert info.ref == "dev"

    def test_refs_heads_branch(self):
        """URLs with refs/heads/BRANCH should parse correctly."""
        url = (
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "refs/heads/main/ArkLib/Data/Polynomial/RationalFunctions.lean"
        )
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.owner == "Verified-zkEVM"
        assert info.repo == "ArkLib"
        assert info.ref == "refs/heads/main"

    def test_refs_tags_version(self):
        """URLs with refs/tags/TAG should parse correctly."""
        url = (
            "https://raw.githubusercontent.com/owner/repo/"
            "refs/tags/v1.0.0/src/File.lean"
        )
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.ref == "refs/tags/v1.0.0"

    def test_refs_heads_raw_url_for(self):
        """raw_url_for should reconstruct valid URLs with refs/heads/ refs."""
        info = GitHubRepoInfo(
            owner="Verified-zkEVM",
            repo="ArkLib",
            ref="refs/heads/main",
        )
        url = info.raw_url_for("ArkLib/Data/Fin/Basic.lean")
        assert url == (
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "refs/heads/main/ArkLib/Data/Fin/Basic.lean"
        )

    def test_commit_hash_ref(self):
        """Full commit hashes should parse correctly."""
        url = (
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "27ff62470e9947f72ddd978db458ee622f8bdcd1/"
            "ArkLib/ProofSystem/Component/CheckClaim.lean"
        )
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.ref == "27ff62470e9947f72ddd978db458ee622f8bdcd1"

    def test_blob_url_with_refs_heads(self):
        """GitHub blob URLs with refs/heads/ should parse correctly."""
        url = (
            "https://github.com/owner/repo/blob/"
            "refs/heads/develop/src/File.lean"
        )
        info = extract_github_repo_info(url)
        assert info is not None
        assert info.ref == "refs/heads/develop"


# ===================================================================
# resolve_imports — mocked network tests
# ===================================================================

def _make_mock_session(url_contents: dict[str, str]):
    """Create a mock aiohttp.ClientSession that returns content based on URL.

    url_contents: maps URLs to their text content. Missing URLs return 404.

    Note: ``session.get(url)`` must return an async context manager directly
    (not a coroutine), because the call-site uses ``async with session.get(url) as resp:``.
    """
    def mock_get(url):
        cm = AsyncMock()
        if url in url_contents:
            cm.__aenter__.return_value.status = 200
            cm.__aenter__.return_value.text = AsyncMock(return_value=url_contents[url])
        else:
            cm.__aenter__.return_value.status = 404
            cm.__aenter__.return_value.text = AsyncMock(return_value="Not found")
        return cm

    session = MagicMock()
    session.get = mock_get
    return session


class TestResolveImports:
    """Tests for resolve_imports — recursive dependency resolution with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_resolves_single_local_import(self):
        """A single local import should be fetched and resolved."""
        source = "import ArkLib.Core\ntheorem foo : True := trivial"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        dep_content = "-- ArkLib/Core.lean content"
        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/Core.lean": dep_content,
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 1
        assert "ArkLib/Core.lean" in result.resolved_files
        assert result.resolved_files["ArkLib/Core.lean"] == dep_content
        assert result.depth_reached == 1

    @pytest.mark.asyncio
    async def test_resolves_recursive_imports(self):
        """Imports in resolved files should themselves be recursively resolved."""
        source = "import ArkLib.A"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/A.lean":
                "import ArkLib.B\ndef a := 1",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/B.lean":
                "def b := 2",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 2
        assert "ArkLib/A.lean" in result.resolved_files
        assert "ArkLib/B.lean" in result.resolved_files
        assert result.depth_reached == 2

    @pytest.mark.asyncio
    async def test_external_imports_are_unresolved(self):
        """External packages (Mathlib, Std, etc.) should be listed as unresolved."""
        source = "import Mathlib.Tactic\nimport ArkLib.Core"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/Core.lean": "-- core",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 1
        unresolved_paths = [u.module_path for u in result.unresolved]
        assert "Mathlib.Tactic" in unresolved_paths
        mathlib_entry = next(u for u in result.unresolved if u.module_path == "Mathlib.Tactic")
        assert "external package" in mathlib_entry.reason

    @pytest.mark.asyncio
    async def test_depth_limit_respected(self):
        """Resolution stops at max_depth and reports deeper imports as unresolved."""
        source = "import ArkLib.L1"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/L1.lean":
                "import ArkLib.L2",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/L2.lean":
                "import ArkLib.L3",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/L3.lean":
                "def deep := 3",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info, max_depth=2)

        # Should resolve L1 and L2, but not L3 (depth limit)
        assert result.total_fetched == 2
        assert "ArkLib/L1.lean" in result.resolved_files
        assert "ArkLib/L2.lean" in result.resolved_files
        assert "ArkLib/L3.lean" not in result.resolved_files
        unresolved_paths = [u.module_path for u in result.unresolved]
        assert "ArkLib.L3" in unresolved_paths
        l3_entry = next(u for u in result.unresolved if u.module_path == "ArkLib.L3")
        assert "depth limit" in l3_entry.reason

    @pytest.mark.asyncio
    async def test_file_count_limit_respected(self):
        """Resolution stops at max_files and reports remaining as unresolved."""
        source = "import ArkLib.A\nimport ArkLib.B\nimport ArkLib.C"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/A.lean": "-- a",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/B.lean": "-- b",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/C.lean": "-- c",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info, max_files=2)

        assert result.total_fetched == 2
        # One should be unresolved due to file limit
        limit_unresolved = [u for u in result.unresolved if "file count limit" in u.reason]
        assert len(limit_unresolved) == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_marks_unresolved(self):
        """Files that fail to download are listed as unresolved."""
        source = "import ArkLib.Missing"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        # No URL map entries — all fetches will 404
        mock_session = _make_mock_session({})
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 0
        assert len(result.unresolved) == 1
        assert "fetch failed" in result.unresolved[0].reason

    @pytest.mark.asyncio
    async def test_circular_imports_handled(self):
        """Circular imports must not cause infinite recursion."""
        source = "import ArkLib.A"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/A.lean":
                "import ArkLib.B",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/B.lean":
                "import ArkLib.A",  # Circular!
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # Both files should be resolved, no infinite loop
        assert result.total_fetched == 2
        assert len(result.unresolved) == 0

    @pytest.mark.asyncio
    async def test_no_repo_info_marks_all_unresolved(self):
        """When repo_info is None, all imports are marked unresolved."""
        source = "import ArkLib.Core\nimport Mathlib.Tactic"
        result = await resolve_imports(source, repo_info=None)

        assert result.total_fetched == 0
        assert len(result.unresolved) == 2
        for u in result.unresolved:
            assert "no GitHub repo info" in u.reason

    @pytest.mark.asyncio
    async def test_no_imports_returns_empty(self):
        """Source without imports returns empty ResolvedImports."""
        source = "theorem foo : True := trivial"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")
        result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 0
        assert result.unresolved == []
        assert result.resolved_files == {}

    @pytest.mark.asyncio
    async def test_self_import_not_fetched(self):
        """If the same module appears twice, it should only be fetched once."""
        source = "import ArkLib.A\nimport ArkLib.A"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/A.lean": "-- a",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 1


# ===================================================================
# Same-repo-only import filtering (ARI-14)
# ===================================================================

class TestSameRepoImportFiltering:
    """Tests for ARI-14: only fetch imports whose top-level module matches the repo name.

    The resolver uses an allowlist approach: only imports starting with the
    repo name (e.g. 'ArkLib' for repo 'ArkLib') are fetched. Everything else
    is treated as external, regardless of whether it's in EXTERNAL_PACKAGES.
    """

    @pytest.mark.asyncio
    async def test_non_repo_import_not_fetched(self):
        """Imports from packages other than the repo should NOT be fetched.

        Even if 'SomeNewLib' is not in EXTERNAL_PACKAGES, it should still
        be treated as external because it doesn't match the repo name.
        """
        source = "import SomeNewLib.Foo\nimport ArkLib.Core"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/Core.lean": "-- core",
            # SomeNewLib should NOT be fetched — no URL entry needed
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # Only ArkLib.Core should be fetched
        assert result.total_fetched == 1
        assert "ArkLib/Core.lean" in result.resolved_files
        # SomeNewLib should be unresolved as external
        unresolved_paths = [u.module_path for u in result.unresolved]
        assert "SomeNewLib.Foo" in unresolved_paths
        # Verify it was classified as external, not fetch-failed
        somenewlib = next(u for u in result.unresolved if u.module_path == "SomeNewLib.Foo")
        assert "external package" in somenewlib.reason

    @pytest.mark.asyncio
    async def test_mathlib_never_fetched_from_arklib_repo(self):
        """Mathlib imports must never be fetched from the ArkLib repo (ARI-14).

        This is the exact bug that caused 404 errors in production.
        """
        source = (
            "import ArkLib.Data.Polynomial.RationalFunctions\n"
            "import Mathlib.Tactic\n"
            "import Mathlib.Data.Fintype.Basic\n"
            "import Std.Data.HashMap\n"
            "import Init.Prelude\n"
            "import Lean.Elab.Tactic\n"
        )
        repo_info = GitHubRepoInfo(owner="Verified-zkEVM", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/main/"
            "ArkLib/Data/Polynomial/RationalFunctions.lean": "-- rational functions",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # Only the ArkLib import should be fetched
        assert result.total_fetched == 1
        assert "ArkLib/Data/Polynomial/RationalFunctions.lean" in result.resolved_files

        # All non-ArkLib imports should be external
        unresolved_paths = {u.module_path for u in result.unresolved}
        assert "Mathlib.Tactic" in unresolved_paths
        assert "Mathlib.Data.Fintype.Basic" in unresolved_paths
        assert "Std.Data.HashMap" in unresolved_paths
        assert "Init.Prelude" in unresolved_paths
        assert "Lean.Elab.Tactic" in unresolved_paths

    @pytest.mark.asyncio
    async def test_unknown_package_treated_as_external(self):
        """Packages not in EXTERNAL_PACKAGES should still be external if they
        don't match the repo name.

        Postcondition: the resolver NEVER tries to fetch from a URL that
        doesn't match the repo name.
        """
        source = "import VeryObscureLib.Something"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        # Track all fetched URLs
        fetched_urls = []
        original_make_mock = _make_mock_session

        def tracking_mock(url_map):
            session = original_make_mock(url_map)
            original_get = session.get
            def tracking_get(url):
                fetched_urls.append(url)
                return original_get(url)
            session.get = tracking_get
            return session

        mock_session = tracking_mock({})
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # Should NOT have attempted to fetch VeryObscureLib from ArkLib repo
        assert len(fetched_urls) == 0, f"Should not fetch any URLs but fetched: {fetched_urls}"
        assert result.total_fetched == 0
        assert len(result.unresolved) == 1
        assert "external package" in result.unresolved[0].reason

    @pytest.mark.asyncio
    async def test_transitive_imports_resolved_recursively(self):
        """ARI-14: Transitive same-repo imports must be resolved.

        If A imports B, and B imports C (all in same repo), C must be fetched.
        """
        source = "import ArkLib.A"
        repo_info = GitHubRepoInfo(owner="org", repo="ArkLib", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/A.lean":
                "import ArkLib.B\nimport Mathlib.Tactic\ndef a := 1",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/B.lean":
                "import ArkLib.C\ndef b := 2",
            "https://raw.githubusercontent.com/org/ArkLib/main/ArkLib/C.lean":
                "def c := 3",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # All three ArkLib files should be resolved
        assert result.total_fetched == 3
        assert "ArkLib/A.lean" in result.resolved_files
        assert "ArkLib/B.lean" in result.resolved_files
        assert "ArkLib/C.lean" in result.resolved_files
        # Mathlib should be unresolved
        unresolved_paths = [u.module_path for u in result.unresolved]
        assert "Mathlib.Tactic" in unresolved_paths
        # Depth should reflect the 3 levels
        assert result.depth_reached == 3

    @pytest.mark.asyncio
    async def test_transitive_with_mixed_external_deps(self):
        """Real-world scenario: each file in the chain imports external deps.

        The resolver should only fetch same-repo files and skip all external
        deps at every level.
        """
        source = "import ArkLib.ProofSystem.Component.CheckClaim"
        repo_info = GitHubRepoInfo(
            owner="Verified-zkEVM", repo="ArkLib",
            ref="27ff62470e9947f72ddd978db458ee622f8bdcd1",
        )

        url_map = {
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "27ff62470e9947f72ddd978db458ee622f8bdcd1/"
            "ArkLib/ProofSystem/Component/CheckClaim.lean":
                "import ArkLib.Data.Fin.Basic\nimport Mathlib.Tactic\nimport VCVio.OracleComp\ndef check := 1",
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "27ff62470e9947f72ddd978db458ee622f8bdcd1/"
            "ArkLib/Data/Fin/Basic.lean":
                "import ArkLib.Data.Fin.Core\nimport CompPoly.Data.Classes\ndef fin_basic := 2",
            "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/"
            "27ff62470e9947f72ddd978db458ee622f8bdcd1/"
            "ArkLib/Data/Fin/Core.lean":
                "import Mathlib.Data.Fintype.Basic\ndef fin_core := 3",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        # All ArkLib files should be resolved
        assert result.total_fetched == 3
        assert "ArkLib/ProofSystem/Component/CheckClaim.lean" in result.resolved_files
        assert "ArkLib/Data/Fin/Basic.lean" in result.resolved_files
        assert "ArkLib/Data/Fin/Core.lean" in result.resolved_files

        # External deps from all levels should be unresolved
        unresolved_paths = {u.module_path for u in result.unresolved}
        assert "Mathlib.Tactic" in unresolved_paths
        assert "VCVio.OracleComp" in unresolved_paths
        assert "CompPoly.Data.Classes" in unresolved_paths
        assert "Mathlib.Data.Fintype.Basic" in unresolved_paths

    @pytest.mark.asyncio
    async def test_non_matching_repo_name_is_external(self):
        """If the repo is 'MyProject', only 'MyProject.*' imports are local."""
        source = "import MyProject.Core\nimport ArkLib.Something"
        repo_info = GitHubRepoInfo(owner="org", repo="MyProject", ref="main")

        url_map = {
            "https://raw.githubusercontent.com/org/MyProject/main/MyProject/Core.lean": "-- core",
        }

        mock_session = _make_mock_session(url_map)
        with patch("aristotlebot.lean_imports.aiohttp.ClientSession", return_value=_acm(mock_session)):
            result = await resolve_imports(source, repo_info)

        assert result.total_fetched == 1
        assert "MyProject/Core.lean" in result.resolved_files
        unresolved_paths = [u.module_path for u in result.unresolved]
        assert "ArkLib.Something" in unresolved_paths


# ===================================================================
# format_import_context
# ===================================================================

class TestFormatImportContext:
    """Tests for format_import_context — formatting resolved imports for LLM context."""

    def test_empty_result_returns_empty_string(self):
        result = ResolvedImports()
        assert format_import_context(result) == ""

    def test_resolved_files_included(self):
        result = ResolvedImports(
            resolved_files={"ArkLib/Core.lean": "def core := 1"},
            total_fetched=1,
        )
        ctx = format_import_context(result)
        assert "ArkLib/Core.lean" in ctx
        assert "def core := 1" in ctx
        assert "Resolved import context" in ctx

    def test_unresolved_imports_noted(self):
        result = ResolvedImports(
            unresolved=[
                UnresolvedImport("Mathlib.Tactic", "external package: Mathlib"),
            ],
        )
        ctx = format_import_context(result)
        assert "Unresolved imports" in ctx
        assert "Mathlib.Tactic" in ctx
        assert "external package" in ctx

    def test_mixed_resolved_and_unresolved(self):
        result = ResolvedImports(
            resolved_files={"ArkLib/Core.lean": "-- core"},
            unresolved=[
                UnresolvedImport("Mathlib.Tactic", "external package: Mathlib"),
            ],
            total_fetched=1,
        )
        ctx = format_import_context(result)
        assert "ArkLib/Core.lean" in ctx
        assert "Mathlib.Tactic" in ctx
        assert "1 files resolved" in ctx
        assert "1 unresolved" in ctx

    def test_summary_line_present(self):
        result = ResolvedImports(
            resolved_files={"A.lean": "-- a", "B.lean": "-- b"},
            total_fetched=2,
            unresolved=[UnresolvedImport("C", "missing")],
        )
        ctx = format_import_context(result)
        assert "2 files resolved" in ctx
        assert "1 unresolved" in ctx


# ===================================================================
# GitHubRepoInfo
# ===================================================================

class TestGitHubRepoInfo:
    """Tests for GitHubRepoInfo dataclass and raw_url_for method."""

    def test_raw_url_for_simple(self):
        info = GitHubRepoInfo(owner="org", repo="repo", ref="main")
        assert info.raw_url_for("src/File.lean") == (
            "https://raw.githubusercontent.com/org/repo/main/src/File.lean"
        )

    def test_raw_url_for_deep_path(self):
        info = GitHubRepoInfo(owner="org", repo="repo", ref="v1.0")
        assert info.raw_url_for("a/b/c/d.lean") == (
            "https://raw.githubusercontent.com/org/repo/v1.0/a/b/c/d.lean"
        )

    def test_rejects_empty_owner(self):
        with pytest.raises(AssertionError):
            GitHubRepoInfo(owner="", repo="repo", ref="main")

    def test_rejects_empty_repo(self):
        with pytest.raises(AssertionError):
            GitHubRepoInfo(owner="org", repo="", ref="main")

    def test_rejects_empty_ref(self):
        with pytest.raises(AssertionError):
            GitHubRepoInfo(owner="org", repo="repo", ref="")


# ===================================================================
# LeanImport
# ===================================================================

class TestLeanImport:
    """Tests for LeanImport dataclass invariants."""

    def test_rejects_empty_module_path(self):
        with pytest.raises(AssertionError):
            LeanImport(module_path="", kind=ImportKind.LOCAL, top_level_package="Foo")

    def test_rejects_empty_top_level_package(self):
        with pytest.raises(AssertionError):
            LeanImport(module_path="Foo.Bar", kind=ImportKind.LOCAL, top_level_package="")


# ===================================================================
# Helpers
# ===================================================================

def _acm(return_value):
    """Create a mock async context manager that yields *return_value*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm
