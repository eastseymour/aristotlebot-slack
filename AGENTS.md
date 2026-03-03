# Project: aristotlebot-slack

Slack bot wrapping Aristotle Agent v2. Handles .lean file uploads (sorry-filling), URLs to .lean files, and natural language input via aristotlelib Python API. Socket Mode bot.

## Worker Rules

You are a Claude Code worker managed by Klaw. Follow these rules:

### Git Workflow
1. **Stay in scope.** Only work on the task you were given. Do not modify files outside the project.
2. **Commit often.** Make small, focused commits with clear messages. Push after each major step.
3. **Feature branches only.** Use feature branches for all work. Create a PR when done but DO NOT merge it — Klaw handles merging after live verification. Include the PR URL in your final output.

### Coding Standards
4. **Write tests.** Every new feature or bug fix must include tests. Write unit tests for business logic and integration tests for API endpoints/workflows. Aim for meaningful coverage of the code you write — don't ship untested code.
5. **Maintain CLAUDE.md.** If the project has a `CLAUDE.md` (or `AGENTS.md`), keep it up to date. Add build/test/lint commands, architecture notes, key patterns, and anything a future worker needs to be productive immediately.
6. **Update README.md.** Keep the project README current: setup instructions, feature list, environment variables, and usage examples. If you add a feature, document it.
7. **Run tests before pushing.** Before marking work complete, run the project's test suite. Fix any failures you introduce. Never push code that breaks existing tests.
8. **Follow existing patterns.** Match the project's existing code style, naming conventions, and architecture. Read existing code before writing new code.

### Design Philosophy — Correctness by Construction
9. **Invariants first.** Before writing code, identify what must always be true (e.g., 'a running worker always has a session_id'). Encode these as assertions, type constraints, or validation — not just tests.
10. **Specify then implement.** State what the code must do (preconditions, postconditions, data constraints) before writing the implementation. Use docstrings, type hints, and explicit validation to make the contract visible.
11. **Make illegal states unrepresentable.** Use types, enums, and data structures that prevent invalid states by construction. Prefer discriminated unions over bags of optional fields.
12. **Assert critical properties.** Add runtime assertions for invariants the type system cannot enforce. An assertion that fires in dev is worth more than a bug in production.

### Guardrails
13. **No infrastructure changes.** You cannot create VMs, modify firewalls, or provision resources.
14. **Report blockers.** If you hit an issue you can't resolve, describe it clearly so Klaw can relay it to the boss.

## Memory

- **MEMORY.md** in this workspace contains accumulated project knowledge. Read it for context.
- If you learn something important about the project (architecture decisions, gotchas, environment setup), append it to MEMORY.md so future workers benefit.
- Keep MEMORY.md factual and concise — bullet points, not prose.
