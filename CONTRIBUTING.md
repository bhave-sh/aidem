# Contributing to aidem

Thanks for your interest in improving aidem. This guide covers how to set up
the project, run the tests, and add common kinds of contributions.

## Project layout

aidem separates **shipped package assets** (read-only, travel with the install)
from **user data** (writable, persistent, survive upgrades):

| Concern | Location | Read-only? |
|---|---|---|
| CLI code (`aidem_cli.py`, `aidem_paths.py`) | package root | yes |
| Generators (`config/generators/*.py`) | package | yes |
| Overlays + canonical `AGENTS.md` | `config/overlays/`, `config/AGENTS.md` | yes |
| Tests | `tests/` | yes |
| **Shared skills library** | `~/.aidem/skills` | user-writable |
| **Cursor transformed mirror** | `~/.aidem/cursor_skills` | user-writable (regenerated) |
| **Registry + manifest** | `~/.aidem/registry`, `~/.aidem/registry/manifest.json` | user-writable |

`~/.aidem` is overridable via the `AIDEM_DATA_DIR` env var (how the tests keep
your real home pristine).

## Development setup

Requires Python 3.11+ and `uv`.

```bash
git clone <aidem-repo-url> ~/aidem
cd ~/aidem
uv tool install --editable .            # install the CLI globally (editable)
uv run --with pytest python -m pytest   # run the test suite
```

Add aidem's bin to PATH if prompted, once in your shell rc.

## Running tests

```bash
uv run --with pytest --with click --with jinja2 python -m pytest -q
```

Tests never touch the real `~/.aidem` or the real IDE dot-folders — the
`fake_dirs` and `invoke` fixtures in `tests/conftest.py` redirect
`AIDEM_DATA_DIR` and `HOME` to a throwaway `tmp_path`. Always use these
fixtures; do not write tests that mutate the real home.

## Adding a new AI tool (a generator)

Each tool's bridge logic lives in its own file under `config/generators/`, so
adding support for a new tool touches only one file plus the registry in
`config/generators/__init__.py`.

1. Create `config/generators/<tool>.py` subclassing `Generator`:
   - Set `name`.
   - Set `global_path` to the tool's global skills/rules directory (return
     `None` if the tool has no confirmed global path).
   - Set `passthrough = True` if the tool reads plain markdown verbatim, or
     `passthrough = False` + `extension` + a `format_skill()` override if it
     needs a transformed format (like Cursor's `.mdc` frontmatter).
   - Override `staging_dir` only if the tool needs its own transformed mirror
     directory under the data dir (see `cursor.py`).
2. Add the class to the `discover_generators` list in
   `config/generators/__init__.py`.
3. Add a test in `tests/test_skills.py` that verifies the bridge points to the
   right staging dir.

If a passthrough tool later adopts a proprietary format, switch that one file
from passthrough to a transformed mirror — no other code changes.

## Adding an overlay template

Create `config/overlays/<name>/AGENTS.md` with project-type-specific
conventions. `aidem init --template <name>` then picks it up automatically.
(For now overlays fully replace the canonical `AGENTS.md`; a merge layer is on
the roadmap.)

## Code style

- Keep functions small and focused.
- Type hints on all public functions.
- Follow the existing `pathlib.Path` style rather than `os.path`.
- No code comments unless the "why" is non-obvious; tests document intent.
- Run `python3 -m py_compile <file>` before committing if you don't run the
  full suite.

## Committing and CLA

By submitting a pull request you agree to the [Contributor License
Agreement](CLA.md). The CLA grants the project maintainers the right to
re-license contributions (including for commercial purposes) — this preserves
the project's monetization flexibility while keeping the core open.

## Releasing (maintainers)

- Bump `version` in `pyproject.toml`.
- Tag the commit (`vX.Y.Z`).
- Publish per the chosen distribution channel (see the README's
  Installation section). The `~/.aidem` data dir survives upgrades by design.