"""Lean 4 import parsing and dependency resolution.

This module handles:
    1. Parsing ``import`` statements from Lean 4 source code.
    2. Resolving import paths to file paths within a GitHub repository.
    3. Recursively fetching imported files with depth and count limits.
    4. Classifying imports as local (same repo) vs external (Mathlib, Std, etc.).

Invariants:
    - Recursive resolution always terminates (bounded by MAX_DEPTH and MAX_FILES).
    - External dependencies (Mathlib, Std, etc.) are never fetched; they are
      reported as unresolved rather than failing silently.
    - All network errors during import fetching are caught and logged; they
      degrade gracefully (the import is marked as unresolved).

Design:
    The module follows Correctness by Construction: ``ResolvedImports`` is a
    discriminated result type that separates resolved files from unresolved
    imports, making it impossible to silently lose information about
    dependencies we couldn't fetch.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import PurePosixPath

import aiohttp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum recursion depth for import resolution (0 = only direct imports).
MAX_DEPTH: int = 10

#: Maximum total files to fetch during import resolution.
MAX_FILES: int = 50

#: Known external Lean 4 packages that we cannot resolve from the source repo.
#: These are reported as unresolved rather than attempted.
#: Includes standard Lean/Mathlib packages and common Lake dependencies
#: that live in separate repositories (e.g. VCVio, CompPoly).
EXTERNAL_PACKAGES: frozenset[str] = frozenset({
    "Mathlib",
    "Std",
    "Init",
    "Lean",
    "Lake",
    "Batteries",
    "Qq",
    "Aesop",
    "ProofWidgets",
    "Cli",
    "VCVio",
    "CompPoly",
    "ImportGraph",
    "LeanSearchClient",
    "Plausible",
})


# ---------------------------------------------------------------------------
# Import parsing
# ---------------------------------------------------------------------------

#: Regex matching Lean 4 import statements.
#: Captures the module path (e.g. ``ArkLib.Data.Fin.Basic``).
#: Handles optional ``public`` keyword: ``import public Foo.Bar``
_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:public\s+)?([A-Za-z_][A-Za-z0-9_.]*)",
    re.MULTILINE,
)


class ImportKind(Enum):
    """Whether an import is local to the repo or from an external package."""
    LOCAL = auto()
    EXTERNAL = auto()


@dataclass(frozen=True)
class LeanImport:
    """A parsed Lean 4 import statement.

    Attributes:
        module_path: The dotted module path (e.g. ``ArkLib.Data.Fin.Basic``).
        kind: Whether the import is local or external.
        top_level_package: The first component of the module path
            (e.g. ``ArkLib`` for ``ArkLib.Data.Fin.Basic``).
    """
    module_path: str
    kind: ImportKind
    top_level_package: str

    def __post_init__(self) -> None:
        assert self.module_path, "module_path must be non-empty"
        assert self.top_level_package, "top_level_package must be non-empty"


def parse_lean_imports(source: str) -> list[LeanImport]:
    """Parse all ``import`` statements from Lean 4 source code.

    Preconditions:
        - *source* is valid Lean 4 source text (or at least contains
          ``import`` lines; we don't validate full syntax).

    Postconditions:
        - Returns a list of :class:`LeanImport` objects, one per import.
        - Each import is classified as LOCAL or EXTERNAL based on its
          top-level package.
        - Order matches the order of appearance in *source*.

    >>> imports = parse_lean_imports("import ArkLib.Data.Fin.Basic\\nimport Mathlib.Tactic")
    >>> [i.module_path for i in imports]
    ['ArkLib.Data.Fin.Basic', 'Mathlib.Tactic']
    >>> [i.kind for i in imports]
    [ImportKind.LOCAL, ImportKind.EXTERNAL]
    """
    results: list[LeanImport] = []
    seen: set[str] = set()

    for match in _IMPORT_RE.finditer(source):
        module_path = match.group(1)
        if module_path in seen:
            continue
        seen.add(module_path)

        top = module_path.split(".", 1)[0]
        kind = (
            ImportKind.EXTERNAL
            if top in EXTERNAL_PACKAGES
            else ImportKind.LOCAL
        )
        results.append(LeanImport(
            module_path=module_path,
            kind=kind,
            top_level_package=top,
        ))

    return results


def import_to_file_path(module_path: str) -> str:
    """Convert a Lean 4 module path to a relative file path.

    Lean 4 convention: dots become directory separators, with ``.lean`` suffix.

    Preconditions:
        - *module_path* is a valid dotted Lean module path.

    Postconditions:
        - Returns a POSIX-style relative path (forward slashes).
        - Path always ends with ``.lean``.

    >>> import_to_file_path("ArkLib.Data.Fin.Basic")
    'ArkLib/Data/Fin/Basic.lean'
    >>> import_to_file_path("Mathlib.Tactic")
    'Mathlib/Tactic.lean'
    """
    assert module_path, "module_path must be non-empty"
    parts = module_path.split(".")
    return str(PurePosixPath(*parts)) + ".lean"


# ---------------------------------------------------------------------------
# GitHub repo info extraction
# ---------------------------------------------------------------------------

#: Regex to extract owner, repo, and ref+path from a GitHub raw URL.
#: The ref can be a simple name (``main``, ``v1.0``, a commit hash) or a
#: multi-segment ref path (``refs/heads/main``, ``refs/tags/v1.0``).
_RAW_GITHUB_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
    r"(?P<ref>refs/(?:heads|tags)/[^/]+|[^/]+)/(?P<path>.+)$"
)

#: Regex to extract owner, repo, ref, and path from a GitHub blob URL.
#: Like the raw URL regex, the ref may be multi-segment (``refs/heads/main``).
_BLOB_GITHUB_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/"
    r"(?P<ref>refs/(?:heads|tags)/[^/]+|[^/]+)/(?P<path>.+?)(?:\?[^#]*)?(?:#.*)?$"
)


@dataclass(frozen=True)
class GitHubRepoInfo:
    """Extracted GitHub repository coordinates.

    Attributes:
        owner: GitHub user or org (e.g. ``Verified-zkEVM``).
        repo: Repository name (e.g. ``ArkLib``).
        ref: Branch, tag, or commit (e.g. ``main``).
    """
    owner: str
    repo: str
    ref: str

    def __post_init__(self) -> None:
        assert self.owner, "owner must be non-empty"
        assert self.repo, "repo must be non-empty"
        assert self.ref, "ref must be non-empty"

    def raw_url_for(self, file_path: str) -> str:
        """Build a raw.githubusercontent.com URL for a file in this repo.

        Preconditions:
            - *file_path* is a POSIX-style relative path within the repo.

        Postconditions:
            - Returns a fully-qualified URL to the raw file content.
        """
        return (
            f"https://raw.githubusercontent.com/"
            f"{self.owner}/{self.repo}/{self.ref}/{file_path}"
        )


def extract_github_repo_info(url: str) -> GitHubRepoInfo | None:
    """Extract GitHub owner, repo, and ref from a URL.

    Supports both ``raw.githubusercontent.com`` and ``github.com/blob`` URLs.
    Returns None for non-GitHub URLs.

    >>> info = extract_github_repo_info(
    ...     "https://raw.githubusercontent.com/Verified-zkEVM/ArkLib/main/ArkLib/Data/Fin/Sigma.lean"
    ... )
    >>> info.owner, info.repo, info.ref
    ('Verified-zkEVM', 'ArkLib', 'main')

    >>> extract_github_repo_info("https://example.com/foo.lean") is None
    True
    """
    # Try raw.githubusercontent.com first
    m = _RAW_GITHUB_RE.match(url)
    if m:
        return GitHubRepoInfo(
            owner=m.group("owner"),
            repo=m.group("repo"),
            ref=m.group("ref"),
        )

    # Try github.com/blob URL
    m = _BLOB_GITHUB_RE.match(url)
    if m:
        return GitHubRepoInfo(
            owner=m.group("owner"),
            repo=m.group("repo"),
            ref=m.group("ref"),
        )

    return None


# ---------------------------------------------------------------------------
# Resolved imports result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnresolvedImport:
    """An import that could not be resolved.

    Attributes:
        module_path: The dotted module path.
        reason: Why it couldn't be resolved (e.g. "external package: Mathlib",
                "fetch failed: 404", "depth limit exceeded").
    """
    module_path: str
    reason: str


@dataclass
class ResolvedImports:
    """The result of recursively resolving Lean 4 imports.

    Attributes:
        resolved_files: Dict mapping relative file paths to their content.
            Example: ``{"ArkLib/Data/Fin/Basic.lean": "import Init..."}``
        unresolved: List of imports that could not be resolved with reasons.
        depth_reached: The maximum recursion depth actually reached.
        total_fetched: Total number of files successfully fetched.
    """
    resolved_files: dict[str, str] = field(default_factory=dict)
    unresolved: list[UnresolvedImport] = field(default_factory=list)
    depth_reached: int = 0
    total_fetched: int = 0


# ---------------------------------------------------------------------------
# Recursive import resolution
# ---------------------------------------------------------------------------

async def _fetch_raw_url(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch text content from a raw URL. Returns None on failure."""
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.warning(
                    "Failed to fetch %s: HTTP %d", url, resp.status
                )
                return None
    except Exception:
        logger.warning("Error fetching %s", url, exc_info=True)
        return None


async def resolve_imports(
    source: str,
    repo_info: GitHubRepoInfo | None,
    *,
    max_depth: int = MAX_DEPTH,
    max_files: int = MAX_FILES,
) -> ResolvedImports:
    """Recursively resolve Lean 4 imports from a source file.

    This function:
        1. Parses ``import`` statements from *source*.
        2. For local imports, constructs the file path and fetches from GitHub.
        3. Recursively resolves imports from fetched files.
        4. Stops at *max_depth* levels or *max_files* total files.
        5. Reports external dependencies (Mathlib, etc.) as unresolved.

    Preconditions:
        - *source* is valid Lean 4 source text.
        - *repo_info* is a valid GitHubRepoInfo or None (for non-GitHub sources).

    Postconditions:
        - Returns a ResolvedImports with all resolved files and unresolved imports.
        - total_fetched <= max_files.
        - depth_reached <= max_depth.
        - No import is both resolved and unresolved.

    When *repo_info* is None, all imports are reported as unresolved (we don't
    know where to fetch them from).
    """
    result = ResolvedImports()

    if repo_info is None:
        # Can't resolve imports without repo info; mark all as unresolved.
        imports = parse_lean_imports(source)
        for imp in imports:
            result.unresolved.append(UnresolvedImport(
                module_path=imp.module_path,
                reason="no GitHub repo info available (non-GitHub URL or file upload)",
            ))
        return result

    # Track already-visited module paths to avoid cycles.
    visited: set[str] = set()

    async with aiohttp.ClientSession() as session:
        await _resolve_recursive(
            session=session,
            source=source,
            repo_info=repo_info,
            result=result,
            visited=visited,
            current_depth=0,
            max_depth=max_depth,
            max_files=max_files,
        )

    return result


async def _resolve_recursive(
    *,
    session: aiohttp.ClientSession,
    source: str,
    repo_info: GitHubRepoInfo,
    result: ResolvedImports,
    visited: set[str],
    current_depth: int,
    max_depth: int,
    max_files: int,
) -> None:
    """Internal recursive resolver. Mutates *result* and *visited* in-place."""
    imports = parse_lean_imports(source)

    for imp in imports:
        # Skip already-visited modules (cycle prevention).
        if imp.module_path in visited:
            continue
        visited.add(imp.module_path)

        # External packages: report as unresolved.
        if imp.kind == ImportKind.EXTERNAL:
            result.unresolved.append(UnresolvedImport(
                module_path=imp.module_path,
                reason=f"external package: {imp.top_level_package}",
            ))
            continue

        # Check file count limit.
        if result.total_fetched >= max_files:
            result.unresolved.append(UnresolvedImport(
                module_path=imp.module_path,
                reason=f"file count limit reached ({max_files})",
            ))
            continue

        # Resolve local import.
        file_path = import_to_file_path(imp.module_path)
        url = repo_info.raw_url_for(file_path)

        content = await _fetch_raw_url(session, url)
        if content is None:
            result.unresolved.append(UnresolvedImport(
                module_path=imp.module_path,
                reason=f"fetch failed: {url}",
            ))
            continue

        # Success: record the resolved file.
        result.resolved_files[file_path] = content
        result.total_fetched += 1
        result.depth_reached = max(result.depth_reached, current_depth + 1)

        # Recurse into the fetched file's imports (if within depth limit).
        if current_depth + 1 < max_depth:
            await _resolve_recursive(
                session=session,
                source=content,
                repo_info=repo_info,
                result=result,
                visited=visited,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                max_files=max_files,
            )
        else:
            # Check if there are deeper imports we can't follow.
            deeper_imports = parse_lean_imports(content)
            for deeper in deeper_imports:
                if deeper.module_path not in visited and deeper.kind == ImportKind.LOCAL:
                    result.unresolved.append(UnresolvedImport(
                        module_path=deeper.module_path,
                        reason=f"depth limit reached ({max_depth})",
                    ))
                    visited.add(deeper.module_path)


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def format_import_context(resolved: ResolvedImports) -> str:
    """Format resolved imports into a context string for the LLM.

    The context string is designed to be prepended to the input file's
    content so the LLM has visibility into the types, theorems, and
    definitions from imported files.

    Postconditions:
        - Returns a string (possibly empty if no imports were resolved).
        - Each resolved file is wrapped with a clear header showing its path.
        - Unresolved imports are noted as comments.
    """
    if not resolved.resolved_files and not resolved.unresolved:
        return ""

    parts: list[str] = []
    parts.append("/- === Resolved import context (auto-fetched) === -/")
    parts.append("")

    # Add resolved files.
    for path, content in resolved.resolved_files.items():
        parts.append(f"/- === {path} === -/")
        parts.append(content.rstrip())
        parts.append("")

    # Note unresolved imports.
    if resolved.unresolved:
        parts.append("/- === Unresolved imports === -/")
        for u in resolved.unresolved:
            parts.append(f"/- {u.module_path}: {u.reason} -/")
        parts.append("")

    parts.append(f"/- === End import context ({resolved.total_fetched} files resolved, "
                  f"{len(resolved.unresolved)} unresolved) === -/")
    parts.append("")

    return "\n".join(parts)
