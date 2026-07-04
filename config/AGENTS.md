# aidem — Global Agent Context

This file is the canonical, portable standard for how AI coding tools should behave on **aidem-managed repos**. It is written into a repo's root as `AGENTS.md` via `aidem init` and committed so every contributor gets the same standard on clone (no aidem install required to read it).

aidem does **not** generate per-tool config files into repos. Tools that read `AGENTS.md` natively (Cursor, GitHub Copilot, Kilo) consume it directly. Your personal skills are bridged into each tool's global path by `aidem setup`, not committed per-repo.

## Coding Conventions

### General

- Write code that is readable, maintainable, and well-tested.
- Prefer explicit over implicit. Avoid clever one-liners that obscure intent.
- Keep functions and modules focused on a single responsibility.
- Document public APIs and non-obvious behavior.

### Error Handling

- Fail fast and fail loudly. Do not swallow exceptions silently.
- Validate inputs at boundaries.
- Return meaningful error messages that help the caller diagnose the problem.

### Security

- Never commit secrets, API keys, or credentials to source control.
- Validate and sanitize all external inputs.
- Use parameterized queries. Never concatenate user input into SQL or shell commands.
- Prefer least-privilege access patterns.

### Testing

- Write tests for business-critical logic.
- Prefer deterministic unit tests over slow integration tests where possible.
- Keep test data realistic but anonymized.

## Communication Style

- Be concise but complete.
- When proposing changes, explain the rationale.
- When reviewing code, focus on correctness, maintainability, and security.
- Avoid unnecessary conversational filler.