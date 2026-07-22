# aidem — Global Agent Context

This file contains conventions and instructions shared across all AI coding tools in this project. It is maintained as the canonical source in the `aidem` configuration repository and generated into individual projects.

## Project Identity

- This is a **Python CLI / library** project.
- Stack: Python 3.11+, `uv` for dependency and environment management.
- Tool-specific configuration files are generated from this source. Do not edit generated files directly.

## Coding Conventions

### General

- Write code that is readable, maintainable, and well-tested.
- Prefer explicit over implicit. Avoid clever one-liners that obscure intent.
- Keep functions and modules focused on a single responsibility.
- Document public APIs and non-obvious behavior.

### Python

- Use type hints on public functions and classes.
- Prefer `pathlib.Path` over `os.path`.
- Use f-strings for formatting.
- Follow PEP 8 style, enforced by `ruff`.
- Use `pydantic` or dataclasses for structured data.

### Project Structure

- Source code lives under a package directory matching the project name.
- CLI entry points are defined in `pyproject.toml` under `[project.scripts]`.
- Tests live in a top-level `tests/` directory.

### Error Handling

- Fail fast and fail loudly. Do not swallow exceptions silently.
- Validate inputs at boundaries.
- Raise specific exceptions with clear messages.
- Use `subprocess.run(check=True)` when calling external tools.

### Security

- Never commit secrets, API keys, or credentials to source control.
- Validate and sanitize all external inputs.
- Use parameterized queries. Never concatenate user input into SQL or shell commands.
- Prefer least-privilege access patterns.

### Testing

- Write tests with `pytest`.
- Use `tmp_path` for filesystem fixtures.
- Mock external network calls and subprocesses.
- Keep tests deterministic and fast.

## Communication Style

- Be concise but complete.
- When proposing changes, explain the rationale.
- When reviewing code, focus on correctness, maintainability, and security.
- Avoid unnecessary conversational filler.
