<div align="center">

# `aidem`

**AI development environment manager.**

_One skill library, one-time tool bridges, one repo standard._

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.md)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Made with uv](https://img.shields.io/badge/uv-powered-blueviolet.svg)](https://github.com/astral-sh/uv)

</div>

---

aidem solves two fragmentation problems caused by the wave of AI coding assistants:

1. **Skill fragmentation** — every AI tool wants skills/rules in a different directory format. You maintain skills in **one** aidem location (`~/.aidem/skills/`); `aidem setup` links each tool's global skills directory to it **once**, so every skill you add afterwards surfaces as a native skill in whatever IDE the developer uses — with no per-skill-per-tool wiring.
2. **Tool isolation** — experimental AI agents each demand their own Python/Docker setup. You register a repo once, and `aidem run <tool>` executes it in an isolated `uv tool` environment without polluting your system.

aidem **does not ship skills** — it ships the *tools* to manage them. It also does **not** pollute each repo with per-tool config files: repo-level standards collapse to a single committed `AGENTS.md` (read natively by Cursor, Copilot, and Kilo).

---

## The Problem

Every major AI coding tool wants its own skills/rules directory:

```
~/.cursor/rules/                  # Cursor
~/.claude/commands/               # Claude Code
~/.config/kilo/skills/            # Kilo
```

A developer with 5 skills and 3 tools manually maintains **15 files** across their home directory expressing the same capability in different formats. Adding/removing a skill means touching 3+ places and cleaning up scattered stale files later.

---

## Architecture: Centralized Bridging

```
┌──────────────────────────────────────────────────────────────────┐
│                            aidem CLI                              │
│                                                                   │
│  aidem registry add <url> <name>   ← clone a skill repo + link it │
│  aidem skill create <name>         ← author a skill locally     │
│  aidem setup                       ← one-time dir bridges + regen│
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
   │                 │  │ B: ~/.aidem/skills│   │                  │
   │                 │  │   (shared lib)   │   │                  │
   │                 │  │   + ~/.aidem/    │   │                  │
   │                 │  │     cursor_skills│   │                  │
   │                 │  │   (transformed) │   │                  │
   │                 │  │                  │   │                  │
   │                 │  │ aidem setup:      │   │                  │
   │                 │  │  ~/.<tool>/...   │   │                  │
   │                 │  │   →→→ config/... │   │                  │
   └─────────────────┘  └──────────────────┘   └──────────────────┘
```

### Half A — Repo conventions (minimal)

`aidem init <path>` writes a **single** `AGENTS.md` into a repo. Tools that read `AGENTS.md` natively (Cursor, GitHub Copilot, Kilo) consume it directly — no per-tool files generated, no duplication, no bloated prompts. The file is committed so every contributor gets the standard on clone with **no aidem install required to read it**.

Overlays under `config/overlays/<template>/AGENTS.md` supply project-type-specific conventions (e.g., `web-app`, `python-cli`).

### Half B — Centralized skill staging + one-time bridging (the moat)

aidem keeps **one** shared skill library — `~/.aidem/skills/` — holding every skill you own (real files from `aidem skill create` + symlinks to registered repos' `skill.md`). `aidem setup` links each tool's global skills directory to the appropriate staging **once**, so you never touch the IDE dot-folders again:

| Tool | IDE dot-folder | Symlinked to | Format |
|---|---|---|---|
| Kilo | `~/.config/kilo/skills` | `~/.aidem/skills` | passthrough (`.md`) |
| Claude Code | `~/.claude/commands` | `~/.aidem/skills` | passthrough (`.md`) |
| Cursor | `~/.cursor/rules` | `~/.aidem/cursor_skills` | transformed (`.mdc`, regenerated) |

- **Passthrough tools** (Kilo, Claude) dir-symlink directly into the shared `~/.aidem/skills`. Add/remove a skill there and every bridged passthrough tool sees it instantly.
- **Cursor** needs `.mdc` files with frontmatter — aidem maintains a separate transformed mirror at `~/.aidem/cursor_skills/`, regenerated from `~/.aidem/skills` on each `aidem setup` / `aidem skill create` / `aidem registry add`. Cursor's dot-folder symlinks to that mirror.
- Each tool's bridge logic lives in its own generator file (`config/generators/<tool>.py`, shipped read-only with the package), so if Kilo or Claude later adopt a proprietary format, only that one file changes — switching from passthrough to a transformed mirror.

User data (`~/.aidem/`) is separated from shipped package code: it survives `pip`/`brew`/`uv` upgrades. Override the data dir with the `AIDEM_DATA_DIR` env var.

This is the part that is **more than an AGENTS.md wrapper**: aidem makes authored/registered skills appear as native, searchable skills/commands in whatever tool each developer uses, managed from a single place with no scattered home-directory pollution.

### Layer 0 — Registry Management

Skill/tool repos are `git clone`d into `~/.aidem/registry/<category>/<name>/` (plain clones, not submodules — no parent repo required). On add, the repo's `skill.md` (or `skills/*.md`) is symlinked into `~/.aidem/skills` so it joins the shared library. Those that define a console script (`pyproject.toml [project.scripts]`) are also installed as `uv tool` in editable mode for isolated execution. Non-uv runtimes (npm/cargo/go) and marker-less repos register as skills-only (with a prompt) instead of failing silently.

### Layer 2 — Execution

`aidem run <tool> <args>` reads `registry/manifest.json`, finds the globally installed binary, and passes through all arguments via `os.execvp`. Exit codes and stdio are preserved; `--help` is passed through, not intercepted.

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
        ├── cursor.py            # transform: → ~/.aidem/cursor_skills/*.mdc
        ├── kilo.py             # passthrough: → ~/.aidem/skills
        ├── claude.py           # passthrough: → ~/.aidem/skills
        ├── github.py            # no global path (repo-level only)
        └── windsurf.py          # no confirmed global path yet
```

User data (writable, persistent, `~/.aidem/` — overridable via `AIDEM_DATA_DIR`):

```
~/.aidem/
├── skills/                 # Shared skill library (real files + source symlinks)
├── cursor_skills/          # Cursor transformed .mdc mirror (regenerated)
└── registry/               # Layer 0: git clones + manifest
    ├── manifest.json
    └── skills/             # registered skill repos land here by default
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

- `~/.config/kilo/skills  -> ~/.aidem/skills`
- `~/.claude/commands      -> ~/.aidem/skills`
- `~/.cursor/rules         -> ~/.aidem/cursor_skills`

Safe to re-run (idempotent). If a dir already exists as a real folder (e.g., you have existing rules), aidem refuses to clobber and tells you how to proceed.

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

Writes `~/.aidem/skills/my-review-skill.md` and regenerates the Cursor mirror. Kilo and Claude see it instantly through their dir bridge; Cursor sees it in `.cursor/rules/my-review-skill.mdc` after the mirror regen.

### 3. Add a skill repo from the registry

```bash
aidem registry add https://github.com/example/my-scanner my-scanner --category security
```

Clones the repo into `~/.aidem/registry`, symlinks its `skill.md` into `~/.aidem/skills`, and regenerates the Cursor mirror. It now appears in Kilo, Claude, and Cursor — no per-tool wiring.

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
aidem registry add <git-url> <name> [--category <category>]
    # Clone a skill/tool repo, link its skill into ~/.aidem/skills, install binary if defined.
    # Non-uv runtimes / marker-less repos prompt to register as skills-only.

aidem registry setup
    # Re-clone missing registered repos and install those that define binaries.

aidem registry update
    # Pull updates for all cloned repos (then run `aidem setup` to refresh links/mirrors).

aidem registry install <name>
    # Install or reinstall a single registered tool's binary.

aidem registry remove <name>
    # Unregister, remove clone, and unlink its skill from ~/.aidem/skills (+ regen mirror).

aidem registry list
    # Show registered skills/tools and skill availability.
```

### Skill authoring & bridging

```bash
aidem skill create <name> [--body <text>]
    # Author a skill in ~/.aidem/skills (opens $EDITOR, or use --body) and refresh mirrors.

aidem setup
    # Build/regenerate the one-time dir-level bridges and refresh transformed mirrors. Idempotent.
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
                       ┌─────────────────────────────────────┐
                       │ ~/.aidem/skills        (single lib)  │
                       │  ├── my-skill.md        (created)   │
                       │  ├── my-scanner.md      → symlink → ~/.aidem/registry/.../skill.md
                       │  └── ...                            │
                       └───────────────┬─────────────────────┘
                                       │ (regenerate on changes)
                                       ▼
                       ┌─────────────────────────────────────┐
                       │ ~/.aidem/cursor_skills              │
                       │  └── my-skill.mdc  (frontmatter .mdc)│
                       └───────────────┬─────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────┐
        ▼                              ▼                          ▼
 ~/.config/kilo/skills         ~/.claude/commands          ~/.cursor/rules
   →→→ ~/.aidem/skills            →→→ ~/.aidem/skills        →→→ ~/.aidem/cursor_skills
 (passthrough dir symlink)    (passthrough dir symlink)    (transform dir symlink)
```

- **One maintenance point**: edit `~/.aidem/skills`; passthrough tools update live (they dereference the dir symlink). Cursor picks up changes on the next `aidem setup`/`skill create`/`registry add` regen.
- **Adding a skill** = one write into `~/.aidem/skills` (+ one regen for Cursor) — not N writes across the home directory.
- **Removing a skill** = one unlink in `~/.aidem/skills` (+ regen) — not hunting through `~/.cursor`, `~/.claude`, `~/.kilo`.
- **Cleanup** = `rm -rf ~/.aidem/skills/*` and the few dir symlinks. No scattered stale files.

To add support for a new AI tool, add a generator in `config/generators/<tool>.py` that sets `global_path` and `staging_dir`, overrides `format_skill`/`regenerate` if its format differs, and is passthrough otherwise. Register it in `config/generators/__init__.py`.

---

## Why This Architecture?

| Concern | Without aidem | With aidem |
|---|---|---|
| Skill duplication | Copy each skill into every tool's dir, in each tool's format | One `~/.aidem/skills` library; tools dir-symlink into it once |
| Scattered home pollution | N×M files across `~/.cursor`, `~/.claude`, `~/.kilo` | A few dir symlinks; skills live under `~/.aidem/` |
| Adding a skill | Write into 3+ home dirs | One file in `~/.aidem/skills` (+ Cursor mirror regen) |
| Removing a skill | Hunt across home dirs for stale links/copies | One unlink in `~/.aidem/skills` (+ regen) |
| Live skill updates | Edit the file in every tool | Edit `~/.aidem/skills`; passthrough tools update instantly |
| Repo config bloat | 5+ per-tool files per repo | One `AGENTS.md` per repo, no per-tool files |
| Tool isolation | Install every agent into system/project Python | Each tool has its own `uv tool` environment |
| New AI tool support | Re-wire every skill into its new format | Add one generator file; one-time bridge |
| Repo contribution friction | Contributors must install your config tool | They just clone — `AGENTS.md` is a static committed file |

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