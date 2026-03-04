# ARI-3: Mathlib/Init.lean Contents Report

**Worker:** `aristotlebot-slack-w13`
**Date:** 2026-03-04
**URL:** https://raw.githubusercontent.com/leanprover-community/mathlib4/master/Mathlib/Init.lean

## Summary

`Mathlib/Init.lean` is the **root import file** for Mathlib4 (the Lean 4 math library).
It is imported by virtually *all* Mathlib files, so its imports are carefully curated.

## What the file contains

### 1. Public imports (~35 lines)

The file consists primarily of `public import` statements that bring in:

- **Linter infrastructure:** `Lean.Linter.Sets`, `Mathlib.Lean.Linter`
- **Syntax linters (enabled by default):**
  - `DeprecatedSyntaxLinter`, `DirectoryDependency`, `DocPrime`, `DocString`
  - `EmptyLine`, `GlobalAttributeIn`, `HashCommandLinter`, `Header`
  - `FlexibleLinter`, `Multigoal`, `OldObtain`, `PrivateModule`
  - `TacticDocumentation`, `UnusedTacticExtension`, `UnusedTactic`
  - `UnusedInstancesInType`, `Style`, `Whitespace`
- **Utility commands:**
  - `Batteries.Tactic.HelpCmd` — makes `#help` available globally
  - `Batteries.Util.ProofWanted` — makes `proof_wanted` command available globally
  - `ImportGraph.Tools` — `#redundant_imports`, `#min_imports`, `#find_home`, `#import_diff`
  - `Mathlib.Tactic.MinImports` — `#min_imports in`
- **Library suggestions:** `Lean.LibrarySuggestions.Default`

### 2. Documentation block

A doc comment explains:
- This is the root file imported by virtually all Mathlib files
- Import guidelines: no bucket imports, every import needs a comment, preference for minimal transitive dependencies
- A linter verifies every Mathlib file imports `Mathlib.Init` (directly or indirectly)

### 3. Linter set registrations

Three linter sets are defined:

1. **`linter.mathlibStandardSet`** — All mathlib syntax linters enabled by default.
   Downstream projects can opt in via `set_option linter.mathlibStandardSet true`.
   Includes ~25 style/syntax linters (flexible, hashCommand, oldObtain, privateModule, various style linters, etc.)

2. **`linter.nightlyRegressionSet`** — Linters for the nightly-testing branch to catch regressions (linarithToGrind, omegaToLia, ringToGrind, tautoToGrind).

3. **`linter.weeklyLintSet`** — Linters that run weekly and post to Zulip (mergeWithGrind).

### 4. Validation `run_cmd`

A `run_cmd` block at the end validates that all linter options mentioned in the registered linter sets actually exist in the environment. This is a compile-time check that prevents referencing undefined linter options.

## File stats

- **Lines:** 144
- **Language:** Lean 4
- **Location:** `Mathlib/Init.lean` in the [mathlib4](https://github.com/leanprover-community/mathlib4) repository

## Command used

```bash
curl -fsSL https://raw.githubusercontent.com/leanprover-community/mathlib4/master/Mathlib/Init.lean
```
