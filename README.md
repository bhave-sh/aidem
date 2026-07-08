<div align="center">

# `aidem`

**AI development environment manager.**

_One content library, one-time tool bridges, one repo standard._

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.md)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Made with uv](https://img.shields.io/badge/uv-powered-blueviolet.svg)](https://github.com/astral-sh/uv)

</div>

---

aidem solves two fragmentation problems caused by the wave of AI coding assistants:

1. **Content fragmentation** — every AI tool wants skills, rules, and MCP configs in a different directory format. You maintain content in **one** aidem location (`~/.aidem/<kind>/`) organized by kind; `aidem setup` links each tool's global directory to it **once**, so every skill you add afterwards surfaces as a native skill in whatever IDE the developer uses — with no per-skill-per-tool wiring.
2. **Tool isolation** — experimental AI agents each demand their own Python/Docker setup. You register a repo once, and `aidem run <tool>` executes it in an isolated `uv tool` environment without polluting your system.

aidem **does not ship skills** — it ships the *tools* to manage them. It also does **not** pollute each repo with per-tool config files: repo-level standards collapse to a single committed `AGENTS.md` (read natively by Cursor, Copilot, and Kilo).

---

## The Problem

Every major AI coding tool wants its own skills directory:

```
~/.cursor/skills/                  # Cursor
~/.claude/skills/                 # Claude Code
~/.kilo/skills/                   # Kilo
```

A developer with 5 skills and 3 tools manually maintains **15 files** across their home directory. Adding/removing a skill means touching 3+ places and cleaning up scattered stale files later.

---

## Architecture: Centralized Bridging

```
┌──────────────────────────────────────────────────────────────────┐
│                            aidem CLI                              │
│                                                                   │
│  aidem registry add <url> <name>   ← clone a skill repo + link it │
│  aidem skill create <name>         ← author a skill locally     │
│  aidem setup                       ← one-time dir bridges        │
│  aidem init [path]                 ← write AGENTS.md into a repo│
│  aidem run <tool> <args>           ← execute a registered tool  │
└──────────────────────────────────────────────────────────────────┘
            │                  │                        │
            ▼                  ▼                        ▼
   ┌─────────────────┐  ┌──────────────────┐   ┌──────────────────┐
   │  Layer 0        │  │  Layer 1         │   │  Layer 2         │
   │  Registry       │  │  Central staging│   │  Execution       │
   │                 │  │  + repo init     │   │                  │
   │ git clone       │  │                  │   │ read manifest    │
   │ clone +         │  │ A: aidem init    │   │ find binary      │
   │ uv tool install │  │  → repo/AGENTS.md│   │ os.execvp        │
   │                 │  │                  │   │ pass-through     │
   │                 │  │ B: ~/.aidem/skill│   │                  │
   │                 │  │   (shared lib)   │   │                  │
   │                 │  │                  │   │                  │
   │                 │  │ aidem setup:      │   │                  │
   │                 │  │  ~/.<tool>/...   │   │                  │
   │                 │  │   →→→ config/... │   │                  │
   └─────────────────┘  └──────────────────┘   └──────────────────┘
```

### Layer 0 — Registry

Skill/tool repos are `git clone`d into `~/.aidem/registry/<kind>/<name>/`. On add, the repo's `skill.md` (or `skills/**/SKILL.md`) is symlinked into `~/.aidem/skill`, along with any supporting subdirectories (e.g. `rules/`, `references/`). Repos that define a console script (`pyproject.toml [project.scripts]`) are also installed as `uv tool` in editable mode.

### Layer 1 — Central staging + repo conventions

aidem keeps **one** shared skill library — `~/.aidem/skill/`. `aidem setup` links each tool's global skills dir to it **once**:

| Tool       | IDE dot-folder          | Bridged to         |
|---|---|---|
| Kilo       | `~/.kilo/skills`       | `~/.aidem/skill`  |
| Claude     | `~/.claude/skills`     | `~/.aidem/skill`  |
| Cursor     | `~/.cursor/skills`     | `~/.aidem/skill`  |
| OpenCode   | `~/.config/opencode/skills` | `~/.aidem/skill`  |
| Windsurf   | `~/.codeium/windsurf/skills` | `~/.aidem/skill`  |

Add/remove a skill in `~/.aidem/skill` and every bridged tool sees it instantly.

For repo-level standards, `aidem init <path>` writes a single `AGENTS.md`. Tools that read it natively (Cursor, Copilot, Kilo) consume it directly — no per-tool files, no duplication. Commit it so every contributor gets the standard on clone with **no aidem install required**.

User data (`~/.aidem/`) is separated from the shipped package and survives upgrades. Override with `AIDEM_DATA_DIR`.

### Layer 2 — Execution

`aidem run <tool> <args>` reads `registry/manifest.json`, finds the globally installed binary, and passes through all arguments via `os.execvp`. Exit codes and stdio are preserved; `--help` is passed through.

---

## Repository Structure

Shipped package (read-only, travels with the install):

```
aidem/                            # the package
├── aidem_cli.py                  # CLI entry point
├── aidem_paths.py                # Centralized path resolution (data dir vs package)
├── pyproject.toml                # Package definition
├── README.md
├── LICENSE.md / CLA.md / NOTICE.md / CONTRIBUTING.md
├── tests/                        # pytest suite (fake HOME/data dir via fixtures)
└── config/                       # shipped assets (read-only)
    ├── AGENTS.md                 # Canonical agent context (repo template source)
    ├── overlays/                 # Per-project-type AGENTS.md templates
    │   ├── web-app/AGENTS.md
    │   └── python-cli/AGENTS.md
    └── generators/              # Per-tool bridge logic (one file per tool)
        ├── base.py              # Generator interface (global_path, staging_dir, ensure_bridge, regenerate)
        ├── cursor.py            # passthrough: → ~/.aidem/skill
        ├── kilo.py             # passthrough: → ~/.aidem/skill
        ├── claude.py           # passthrough: → ~/.aidem/skill
        ├── github.py            # no global path (repo-level only)
        └── windsurf.py          # no confirmed global path yet
```

User data (writable, persistent, `~/.aidem/` — overridable via `AIDEM_DATA_DIR`):

```
~/.aidem/
├── skill/                  # Shared skill library (real files + source symlinks)
├── rule/                   # Shared rules library (when registered with --kind rule)
├── mcp/                    # MCP server configs (when registered with --kind mcp)
├── registry/               # Layer 0: git clones + manifest
│   ├── manifest.json
│   └── skill/             # repos registered with --kind skill
```

---

## Installation

Requires Python 3.11+ and `uv`.

```bash
git clone https://github.com/bhave-sh/aidem.git ~/aidem
cd ~/aidem
uv tool install --editable .
aidem --help
```

Add aidem's bin to PATH if prompted (once, in your shell rc):
```bash
export PATH="$HOME/.local/bin:$PATH"
```

aidem stores user data (skills, registry, manifest) in `~/.aidem/` by default.
Override with the `AIDEM_DATA_DIR` env var (e.g. for testing or a non-default
profile). Shipped package assets (generators, overlays, canonical `AGENTS.md`)
travel with the install and are read-only.

---

## Quick Start

### 1. Build the one-time tool bridges (once per machine)

```bash
aidem setup
```

This symlinks each tool's global skills dir into aidem's staging:

- `~/.kilo/skills      -> ~/.aidem/skill`
- `~/.claude/skills     -> ~/.aidem/skill`
- `~/.cursor/skills     -> ~/.aidem/skill`

Safe to re-run (idempotent). If a tool's parent directory isn't found (tool not installed), aidem skips it gracefully. Kilo falls back to a `~/.config/kilo/kilo.jsonc` config entry when the symlink can't be created.

### 2. Create a skill (author your own)

```bash
aidem skill create my-review-skill
# opens $EDITOR with a skill template; save to commit it
# or non-interactively:
aidem skill create my-review-skill --body "$(cat <<'MD'
# Skill: my-review-skill
## Purpose
Review code for correctness and style.
## When to Apply
During PR review.
MD
)"
```

Writes `~/.aidem/skill/my-review-skill/SKILL.md`. Kilo, Claude, and Cursor see it instantly through their dir bridges.

### 3. Add a skill repo from the registry

```bash
aidem registry add https://github.com/example/my-scanner my-scanner --kind skill
```

Clones the repo into `~/.aidem/registry`, symlinks its `skill.md` into `~/.aidem/skill`. It now appears in Kilo, Claude, and Cursor — no per-tool wiring.

### 4. Update the standard in a repo

```bash
cd ~/projects/my-web-app
aidem init --template web-app
```

Writes a single `AGENTS.md` (no per-tool files). Commit it so teammates inherit the standard on clone.

### 5. Run a registered tool

```bash
aidem run my-scanner .
aidem run my-scanner --format json --output report.json
```

---

## Command Reference

### Registry commands (`aidem registry ...`)

```bash
aidem registry add <git-url> <name> [--kind skill|rule|mcp|memory|plan]
    # Clone a skill/tool repo, link its content into ~/.aidem/<kind>, install binary if defined.
    # Defaults to --kind skill. Non-uv runtimes / marker-less repos prompt to register as skills-only.

aidem registry setup
    # Re-clone missing registered repos and install those that define binaries.

aidem registry update
    # Pull updates for all cloned repos (then run `aidem setup` to refresh links).

aidem registry install <name>
    # Install or reinstall a single registered tool's binary.

aidem registry remove <name>
    # Unregister, remove clone, and unlink its content from ~/.aidem/<kind>.

aidem registry list
    # Show registered skills/tools and skill availability.
```

### Skill authoring & bridging

```bash
aidem skill create <name> [--body <text>]
    # Author a skill in ~/.aidem/skill (opens $EDITOR, or use --body).

aidem setup
    # Build/regenerate the one-time dir-level bridges. Idempotent.
```

### Repo standard & execution

```bash
aidem init [project_path] [--template <name>] [--link/--copy] [--force]
    # Write one AGENTS.md into a repo. Prompts if writing to cwd; refuses inside the aidem package dir unless --force.

aidem run <tool> [args...]
    # Execute a registered tool, passing all arguments through.
```

---

## How the Centralized Model Works

```
                       ┌──────────────────────────────────────┐
                       │ ~/.aidem/skill        (single lib)   │
                       │  ├── my-review-skill/                 │
                       │  │   └── SKILL.md       (created)    │
                       │  ├── my-scanner/                      │
                       │  │   └── SKILL.md   → symlink         │
                       │  └── ...                             │
                       └───────────────┬───────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
  ~/.kilo/skills               ~/.claude/skills              ~/.cursor/skills
    →→→ ~/.aidem/skill          →→→ ~/.aidem/skill          →→→ ~/.aidem/skill
  (passthrough dir symlink)  (passthrough dir symlink)  (passthrough dir symlink)
```

- **One maintenance point**: edit `~/.aidem/skill`; all tools update live (they dereference the dir symlink).
- **Adding a skill** = one write into `~/.aidem/skill` — not N writes across the home directory.
- **Removing a skill** = one unlink in `~/.aidem/skill` — not hunting through `~/.cursor`, `~/.claude`, `~/.kilo`.
- **Cleanup** = `rm -rf ~/.aidem/skill/*` and the few dir symlinks. No scattered stale files.

To add support for a new AI tool, add a generator in `config/generators/<tool>.py` that sets `global_path` and `staging_dir`, overrides `format_skill`/`regenerate` if its format differs, and is passthrough otherwise. Register it in `config/generators/__init__.py`.

---

## Why This Architecture?

| Concern | Without aidem | With aidem |
|---|---|---|
| Skill duplication | Copy each skill into every tool's dir, in each tool's format | One `~/.aidem/skill` library; tools dir-symlink into it once |
| Scattered home pollution | N×M files across `~/.cursor`, `~/.claude`, `~/.kilo` | A few dir symlinks; skills live under `~/.aidem/` |
| Adding a skill | Write into 3+ home dirs | One directory in `~/.aidem/skill` |
| Removing a skill | Hunt across home dirs for stale links/copies | One unlink in `~/.aidem/skill` |
| Live skill updates | Edit the file in every tool | Edit `~/.aidem/skill`; all tools update instantly |
| Repo config bloat | 5+ per-tool files per repo | One `AGENTS.md` per repo, no per-tool files |
| Tool isolation | Install every agent into system/project Python | Each tool has its own `uv tool` environment |
| New AI tool support | Re-wire every skill into its new format | Add one generator file; one-time bridge |
| Repo contribution friction | Contributors must install your config tool | They just clone — `AGENTS.md` is a static committed file |

---

## Future Work

aidem currently centralizes skills. The same architecture extends naturally to other AI development artifacts:

### Rules (next priority)
Cursor, Windsurf, and others support project-level or global rules (`.cursor/rules/`, `.windsurfrules`). A `~/.aidem/rules/` library with per-tool passthrough bridges would eliminate the same duplication problem skills already solve.

### MCP server configuration
Configuring MCP servers once and having them available in every tool is the top pain point for multi-tool users. Each tool uses a different config format, so this needs per-tool *transform generators* (like aidem originally had for Cursor `.mdc` mirrors) — not just passthrough symlinks. Higher complexity, higher user value.

### Plans and memory
These concepts are deeply vendor-specific with no emerging standard. Aidem would need a canonical format plus per-tool transformers. Lower priority until the ecosystem converges.

### Kind-specific content formats
The `--kind` flag now drives which directory content is linked into (`~/.aidem/skill`, `~/.aidem/rule`, etc.), but the content format detection is still skill-centric (looking for `SKILL.md`). Future work should adapt `_skill_source_for` to recognize content format conventions per kind (e.g., `.mdc` files for rules, JSON for MCP).

To add support for a new artifact type, add a generator in `config/generators/<tool>.py` that implements `ensure_bridge()` for that type's staging directory, register the kind in `REGISTRY_KINDS`, and extend the registry content detection.

---

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
development setup, how to run the test suite, how to add a generator for a new
AI tool, and code style.

By contributing, you agree to the [Contributor License Agreement](CLA.md).

---

## License

aidem is licensed under the [Apache License 2.0](LICENSE.md).

See [NOTICE.md](NOTICE.md) for third-party dependency licenses and registry tool licensing details.