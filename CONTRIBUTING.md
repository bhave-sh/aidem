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
| Runtimes (`config/runtimes/*.py`) | package | yes |
| Overlays + canonical `AGENTS.md` | `config/overlays/`, `config/AGENTS.md` | yes |
| Tests | `tests/` | yes |
| **Shared skills library** | `~/.aidem/skills` | user-writable |
| **Shared rules library** | `~/.aidem/rules` | user-writable |
| **Isolated tool envs** | `~/.aidem/envs/<name>/` | user-writable (one dir per tool) |
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
   - For skills: set `global_path` to the tool's global skills directory (return
     `None` if the tool has no confirmed global skills path).
   - For rules: set `rules_global_path` to the tool's global rules directory if
     it is dir-shaped (Claude), or override `ensure_rules_bridge()` for a
     config-array tool (Kilo, OpenCode) / `regenerate_rules()` for a concat
     tool (Windsurf). Leave both as `None`/no-op if the tool has no file-based
     global rules path (Cursor, GitHub).
   - Set `passthrough = True` if the tool reads plain markdown verbatim, or
     `passthrough = False` + `extension` + a `format_skill()` override if it
     needs a transformed format (like Cursor's `.mdc` frontmatter).
   - Override `staging_dir` only if the tool needs its own transformed mirror
     directory under the data dir (see `cursor.py`).
2. Add the class to the `discover_generators` list in
   `config/generators/__init__.py`.
3. Add a test in `tests/test_skills.py` (skills bridge) and/or
   `tests/test_rules.py` (rules bridge) that verifies the bridge points to the
   right staging dir / config entry.

If a passthrough tool later adopts a proprietary format, switch that one file
from passthrough to a transformed mirror — no other code changes.

## Adding a new runtime (execution adapter)

Layer 2 execution uses a runtime-adapter model: each registered tool lives in
an aidem-owned isolated env at `~/.aidem/envs/<name>/` and aidem resolves+execs
its binary from there, never the global PATH. Each ecosystem has one adapter
file under `config/runtimes/`:

1. Create `config/runtimes/<name>.py` subclassing `Runtime` (from `base.py`):
   - Set `name`.
   - Implement `install()` to populate the env (`self.env_path`).
   - Implement `resolve_binary()` to return the absolute path to the CLI inside
     the env (default looks at `env_path/bin/<binary>`; override for layouts
     like `node_modules/.bin/`).
   - Override `run()` only if exec isn't a plain `os.execvp` (e.g. Docker wraps
     in a `docker run` invocation).
   - Implement `is_installed()` so `registry list`/`setup` report state.
   - Implement `uninstall()` (default removes the env dir).
2. Add a file-marker → runtime mapping in `config/runtimes/__init__.py`
   (`RUNTIME_MARKERS`) and add the kind to `SUPPORTED_RUNTIMES`.
3. Add the kind to `RUNTIME_KINDS` in `aidem_cli.py`.
4. Add tests in `tests/test_runtimes.py` with monkeypatched
   `subprocess.run` / `urllib` (never hit the real network or build tools).

aidem's core stays ecosystem-agnostic: it knows "install into envs/<n>, resolve
binary from envs/<n>/bin, exec it" — the per-ecosystem specifics live in one
adapter file. Auto-heuristics (PyPI-wheel preference, GitHub-Releases asset
matching, etc.) belong in the adapter, not the CLI.

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

## Committing, DCO, and CLA

### DCO (Developer Certificate of Origin)

Every commit must include a `Signed-off-by` line to certify that you have the
right to submit it under the project's license. This is the [Developer
Certificate of Origin](DCO.md) v1.1.

```bash
git commit -s -m "feat: add foo"
```

If you forget, amend with `git commit --amend -s`. A DCO bot will check every
pull request automatically.

### Contributor License Agreement

By submitting a pull request you also agree to the [Contributor License
Agreement](CLA.md). The CLA grants the project maintainers the right to
re-license contributions (including for commercial purposes) — this preserves
the project's monetization flexibility while keeping the core open.

Both the DCO and CLA apply to every contribution.

## Releasing (maintainers)

- Bump `version` in `pyproject.toml`.
- Tag the commit (`vX.Y.Z`).
- Publish per the chosen distribution channel (see the README's
  Installation section). The `~/.aidem` data dir survives upgrades by design.