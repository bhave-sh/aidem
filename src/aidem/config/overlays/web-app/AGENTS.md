# aidem — Global Agent Context

This file contains conventions and instructions shared across all AI coding tools in this project. It is maintained as the canonical source in the `aidem` configuration repository and generated into individual projects.

## Project Identity

- This is a **web application** project.
- Stack: TypeScript, React, modern frontend tooling.
- Tool-specific configuration files are generated from this source. Do not edit generated files directly.

## Coding Conventions

### General

- Write code that is readable, maintainable, and well-tested.
- Prefer explicit over implicit. Avoid clever one-liners that obscure intent.
- Keep functions and modules focused on a single responsibility.
- Document public APIs and non-obvious behavior.

### TypeScript

- Use strict TypeScript. Avoid `any` unless absolutely necessary.
- Prefer interfaces over types for object shapes.
- Use explicit return types on public functions.
- Use `const` and `let`; avoid `var`.

### React

- Prefer functional components with hooks.
- Keep components small and focused.
- Co-locate styles, tests, and sub-components when it improves discoverability.
- Avoid prop drilling; use context or state management when appropriate.

### Error Handling

- Fail fast and fail loudly. Do not swallow exceptions silently.
- Validate inputs at boundaries.
- Use error boundaries to isolate UI failures.
- Return meaningful error messages that help the caller diagnose the problem.

### Security

- Never commit secrets, API keys, or credentials to source control.
- Validate and sanitize all external inputs.
- Escape rendered content to prevent XSS.
- Use parameterized queries or ORM methods. Never concatenate user input into SQL.
- Prefer least-privilege access patterns.

### Testing

- Write unit tests for utilities and hooks.
- Write integration tests for user flows.
- Use realistic but anonymized test data.
- Keep tests deterministic and fast.

## Communication Style

- Be concise but complete.
- When proposing changes, explain the rationale.
- When reviewing code, focus on correctness, maintainability, and security.
- Avoid unnecessary conversational filler.
