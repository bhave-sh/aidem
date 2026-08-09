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
2. **Tool isolation** — experimental AI agents each demand their own Python/Docker setup. You register a repo once, and `aidem run <tool>` executes it from an aidem-owned isolated env at `~/.aidem/envs/<tool>/` — never polluting your global PATH or system Python. Runtime adapters (uv / prebuilt binary / docker) handle each ecosystem.

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
│  aidem create <name> --skill        ← author a skill locally     │
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
    │                 │  │ B: ~/.aidem/skills│  │                  │
   │                 │  │   (shared lib)   │   │                  │
   │                 │  │                  │   │                  │
   │                 │  │ aidem setup:      │   │                  │
   │                 │  │  ~/.<tool>/...   │   │                  │
   │                 │  │   →→→ config/... │   │                  │
   └─────────────────┘  └──────────────────┘   └──────────────────┘
```

### Layer 0 — Registry

Skill/tool repos are `git clone`d into `~/.aidem/registry/<kind>/<name>/`. On add, the repo's `skill.md` (or `skills/**/SKILL.md`) is symlinked into `~/.aidem/skills`, along with any supporting subdirectories (e.g. `rules/`, `references/`). Repos that define a console script (`pyproject.toml [project.scripts]`) are also installed as `uv tool` in editable mode. Rule repos (`--kind rule`) link their `rule.md`/`rules/*.md` into `~/.aidem/rules` as flat files instead.

### Layer 1 — Central staging + repo conventions

aidem keeps **one** shared skill library — `~/.aidem/skills/`. `aidem setup` links each tool's global skills dir to it **once**:

| Tool       | IDE dot-folder          | Bridged to         |
|---|---|---|
| Kilo       | `~/.kilo/skills`       | `~/.aidem/skills`  |
| Claude     | `~/.claude/skills`     | `~/.aidem/skills`  |
| Cursor     | `~/.cursor/skills`     | `~/.aidem/skills`  |
| OpenCode   | `~/.config/opencode/skills` | `~/.aidem/skills`  |
| Windsurf   | `~/.codeium/windsurf/skills` | `~/.aidem/skills`  |

Add/remove a skill in `~/.aidem/skills` and every bridged tool sees it instantly.

The same model now extends to **rules** via `~/.aidem/rules/`. Rules have no cross-tool standard (unlike skills), so each tool is bridged in the shape it supports:

| Tool       | Global rules target | Bridge |
|---|---|---|
| Claude     | `~/.claude/rules`              | passthrough dir symlink → `~/.aidem/rules` |
| Kilo       | `instructions[]` in `~/.config/kilo/kilo.jsonc` | config-array glob entry |
| OpenCode   | `instructions[]` in `~/.config/opencode/opencode.json` | config-array glob entry |
| Windsurf   | `~/.codeium/windsurf/memories/global_rules.md` | concat mirror (6,000-char Windsurf cap; warns on overflow) |
| Cursor     | — | skipped (User Rules are UI-only; project rules are repo-level `.mdc`) |
| GitHub Copilot | — | skipped (repo-level only) |

Add/remove a rule in `~/.aidem/rules` and every bridged tool sees it on the next `aidem setup`.

For repo-level standards, `aidem init <path>` writes a single `AGENTS.md`. Tools that read it natively (Cursor, Copilot, Kilo) consume it directly — no per-tool files, no duplication. Commit it so every contributor gets the standard on clone with **no aidem install required**.

User data (`~/.aidem/`) is separated from the shipped package and survives upgrades. Override with `AIDEM_DATA_DIR`.

### Layer 2 — Execution

`aidem run <tool> <args>` reads `registry/manifest.json`, resolves the tool's binary from its **aidem-owned isolated env** at `~/.aidem/envs/<tool>/bin/<binary>`, and execs it with all args passed through via `os.execvp`. Exit codes and stdio are preserved; `--help` is passed through.

aidem never installs tool CLIs onto the global PATH (`~/.local/bin`). Each registered tool lives in its own isolated env, so same-named binaries never collide and the host system stays clean. The runtime kind (uv / binary / docker) is auto-detected from file markers and dispatches to a per-ecosystem adapter:

| Runtime | Mechanism | Host deps | Isolation | Example tools |
|---|---|---|---|---|
| **uv** (default for Python) | `uv venv` + `uv pip install` into `~/.aidem/envs/<n>/` | Python + uv | full app isolation, no global PATH | headroom, any pyproject tool |
| **binary** | fetch GitHub release asset → extract → `envs/<n>/bin/` | none | fully sandboxed (single binary) | rtk, any release-asset tool |
| **docker** | `docker run --rm -v $PWD:/work <image> <args>` | Docker only | OS-level sandbox, reproducible | any Dockerfile/image tool |

Auto-heuristics make `aidem registry add <url> <name>` one-command magic for the common cases (no flags required):
- **uv**: prefers a **published PyPI wheel** over an editable-from-clone build (avoids native-toolchain builds); defaults to the `[all]` extras group if the clone declares it. Override with `--spec "<pkg>[extras]"` / `--extras "..."`.
- **binary**: queries the repo's GitHub Releases and picks the asset matching `{os}-{arch}` (rtk ships `rtk-<arch>-<os>.tar.gz`). Override with `--asset "<glob>"`.
- **docker**: pulls a `--image <ref>` if given, else builds the clone's `Dockerfile` as `aidem/<name>`.

Detected-but-deferred ecosystems (npm, cargo, go) register as skills-only with a clear note until their runtimes ship.

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
    ├── generators/              # Per-tool bridge logic (one file per tool)
    │   ├── base.py              # Generator interface (skills + rules bridges)
    │   ├── cursor.py            # skills passthrough; rules skipped (no global path)
    │   ├── kilo.py             # skills passthrough/config fallback; rules → instructions[]
    │   ├── claude.py           # skills + rules passthrough → ~/.aidem/{skills,rules}
    │   ├── github.py            # no global path (repo-level only, both kinds)
    │   └── windsurf.py          # skills passthrough; rules → concat global_rules.md
    └── runtimes/               # Per-ecosystem execution adapters (one file per runtime)
        ├── base.py              # Runtime interface (install/resolve_binary/run/uninstall)
        ├── uv_venv.py          # Python: uv venv + pip install into envs/<n>/
        ├── binary.py           # Prebuilt: fetch release asset → envs/<n>/bin/
        └── docker.py           # Container: docker run --rm -v $PWD:/work
```

User data (writable, persistent, `~/.aidem/` — overridable via `AIDEM_DATA_DIR`):

```
~/.aidem/
├── skills/                 # Shared skill library (real files + source symlinks)
├── rules/                  # Shared rules library (flat *.md, one file per rule)
├── mcp/                    # MCP server configs (when registered with --kind mcp)
├── envs/                   # Layer 2: isolated tool envs (one dir per registered tool)
│   └── <name>/bin/<binary> # aidem resolves+execs from here, never global PATH
└── registry/               # Layer 0: git clones + manifest
    ├── manifest.json
    └── skill/             # repos registered with --kind skill (singular kind label)
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

- `~/.kilo/skills      -> ~/.aidem/skills`
- `~/.claude/skills     -> ~/.aidem/skills`
- `~/.cursor/skills     -> ~/.aidem/skills`

Safe to re-run (idempotent). If a tool's parent directory isn't found (tool not installed), aidem skips it gracefully. Kilo falls back to a `~/.config/kilo/kilo.jsonc` config entry when the symlink can't be created. `aidem setup` also bridges rules where a tool supports them (Claude dir symlink; Kilo/OpenCode config-array; Windsurf concat mirror; Cursor/GitHub skipped).

### 2. Create a skill or rule (author your own)

```bash
aidem create my-review-skill --skill
# opens $EDITOR with a skill template; save to commit it
# or non-interactively:
aidem create my-review-skill --skill --body "$(cat <<'MD'
# Skill: my-review-skill
## Purpose
Review code for correctness and style.
## When to Apply
During PR review.
MD
)"
```

Writes `~/.aidem/skills/my-review-skill/SKILL.md`. Kilo, Claude, and Cursor see it instantly through their dir bridges.

```bash
aidem create no-emoji --rule --body "# Rule: no-emoji\nDo not use emojis in code or docs."
```

Writes `~/.aidem/rules/no-emoji.md` (flat, one file per rule). Bridged tools see it on the next `aidem setup`. `--skill` is the default if neither flag is given.

### 3. Add a skill repo from the registry

```bash
aidem registry add https://github.com/example/my-scanner my-scanner --kind skill
```

Clones the repo into `~/.aidem/registry`, symlinks its `skill.md` into `~/.aidem/skills`. It now appears in Kilo, Claude, and Cursor — no per-tool wiring.

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

Tools install into an isolated, aidem-owned env — no global PATH pollution, no same-name clashes. The runtime is auto-detected:

```bash
# Python tool with a pyproject.toml -> uv runtime (PyPI wheel preferred, [all] extras if declared)
aidem registry add https://github.com/headroomlabs-ai/headroom headroom
aidem run headroom doctor

# Rust/Go single-binary tool -> binary runtime (GitHub release asset, platform-matched)
aidem registry add https://github.com/rtk-ai/rtk rtk
aidem run rtk git status

# Tool shipping a Dockerfile -> docker runtime (ephemeral container, cwd mounted)
aidem registry add https://github.com/example/container-tool ctool
aidem run ctool scan .
```

---

## Command Reference

### Registry commands (`aidem registry ...`)

```bash
aidem registry add <git-url> <name> [--kind skill|rule|mcp|memory|plan]
    [--runtime uv|binary|docker] [--spec "<pkg>[extras]"] [--extras "..."]
    [--asset "<glob>"] [--image "<ref>"] [--no-install]
    # Clone a repo, link its content into ~/.aidem/<kind>, and install its tool
    # binary into an aidem-owned isolated env (~/.aidem/envs/<name>/) — never the
    # global PATH. Runtime is auto-detected from file markers (pyproject->uv,
    # Cargo/go->binary, Dockerfile->docker); flags override the auto-heuristic.
    # Defaults to --kind skill. Deferred ecosystems (npm/cargo/go) register as
    # skills-only with a note until their runtimes ship.

aidem registry setup
    # Re-clone missing registered repos and install those that define binaries.

aidem registry update
    # Pull updates for all cloned repos (then run `aidem setup` to refresh links).

aidem registry install <name>
    # Install or reinstall a single registered tool into its isolated env.

aidem registry remove <name>
    # Unregister, remove clone, tear down the isolated env, and unlink content.

aidem registry list
    # Show registered skills/tools, runtime kind, and env install state.
```

### Skill & rule authoring & bridging

```bash
aidem create <name> --skill|--rule [--body <text>]
    # Author a skill (~/.aidem/skills/<name>/SKILL.md) or a rule
    # (~/.aidem/rules/<name>.md) in aidem's central library. Opens $EDITOR, or
    # use --body. --skill is the default when neither flag is given.

aidem setup
    # Build/regenerate the one-time dir bridges for skills and rules. Idempotent.
```

### Repo standard & execution

```bash
aidem init [project_path] [--template <name>] [--link/--copy] [--force]
    # Write one AGENTS.md into a repo. Prompts if writing to cwd; refuses inside the aidem package dir unless --force.

aidem run <tool> [args...]
    # Execute a registered tool from its isolated env (~/.aidem/envs/<tool>/bin),
    # passing all arguments through. Exit codes and stdio preserved; --help works.
    # A legacy global-PATH install still runs but prints a one-time migration nudge.
```

---

## How the Centralized Model Works

```
                        ┌──────────────────────────────────────┐
                        │ ~/.aidem/skills       (single lib)   │
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
     →→→ ~/.aidem/skills         →→→ ~/.aidem/skills         →→→ ~/.aidem/skills
   (passthrough dir symlink)  (passthrough dir symlink)  (passthrough dir symlink)
```

- **One maintenance point**: edit `~/.aidem/skills`; all tools update live (they dereference the dir symlink).
- **Adding a skill** = one write into `~/.aidem/skills` — not N writes across the home directory.
- **Removing a skill** = one unlink in `~/.aidem/skills` — not hunting through `~/.cursor`, `~/.claude`, `~/.kilo`.
- **Cleanup** = `rm -rf ~/.aidem/skills/*` and the few dir symlinks. No scattered stale files.

Rules work the same way from `~/.aidem/rules` — Claude gets a passthrough dir symlink, Kilo/OpenCode get an `instructions` config-array entry, Windsurf gets a concat mirror; edit once, re-run `aidem setup`.

To add support for a new AI tool, add a generator in `config/generators/<tool>.py` that sets `global_path` and `staging_dir` for skills, and `rules_global_path` (or an `ensure_rules_bridge` / `regenerate_rules` override) for rules. Override `format_skill`/`regenerate` only if its format differs, and is passthrough otherwise. Register it in `config/generators/__init__.py`.

---

## Why This Architecture?

| Concern | Without aidem | With aidem |
|---|---|---|
| Skill duplication | Copy each skill into every tool's dir, in each tool's format | One `~/.aidem/skills` library; tools dir-symlink into it once |
| Scattered home pollution | N×M files across `~/.cursor`, `~/.claude`, `~/.kilo` | A few dir symlinks; skills live under `~/.aidem/` |
| Adding a skill | Write into 3+ home dirs | One directory in `~/.aidem/skills` |
| Removing a skill | Hunt across home dirs for stale links/copies | One unlink in `~/.aidem/skills` |
| Live skill updates | Edit the file in every tool | Edit `~/.aidem/skills`; all tools update instantly |
| Repo config bloat | 5+ per-tool files per repo | One `AGENTS.md` per repo, no per-tool files |
| Tool isolation | Install every agent into system/project Python | Each tool has its own aidem-owned env (`~/.aidem/envs/<n>/`); runtime adapters (uv/binary/docker) keep the global PATH clean |
| New AI tool support | Re-wire every skill into its new format | Add one generator file; one-time bridge |
| Repo contribution friction | Contributors must install your config tool | They just clone — `AGENTS.md` is a static committed file |

---

## Future Work

aidem centralizes skills and rules. The same architecture extends naturally to other AI development artifacts:

### Rules (done)
A `~/.aidem/rules/` library with per-tool bridges now ships: Claude gets a passthrough dir symlink, Kilo/OpenCode get an `instructions` config-array entry, and Windsurf gets a concat mirror (capped at Windsurf's 6,000-char `global_rules.md` limit). Cursor and GitHub Copilot have no file-based global rules path and are skipped. Author rules with `aidem create <name> --rule`; register rule repos with `aidem registry add <url> <name> --kind rule`. Project-level rule init (writing per-tool `.cursor/rules/*.mdc`, `.devin/rules/*.md`, … into a repo) is deliberately out of scope — it would reintroduce the per-tool file bloat aidem's single `AGENTS.md` model avoids.

### MCP server configuration
Configuring MCP servers once and having them available in every tool is the top pain point for multi-tool users. Each tool uses a different config format, so this needs per-tool *transform generators* (like aidem originally had for Cursor `.mdc` mirrors) — not just passthrough symlinks. Higher complexity, higher user value.

### Plans and memory
These concepts are deeply vendor-specific with no emerging standard. Aidem would need a canonical format plus per-tool transformers. Lower priority until the ecosystem converges.

### Runtimes (done + deferred)
Layer 2 execution uses a runtime-adapter model: aidem owns an isolated env per tool (`~/.aidem/envs/<n>/`) and resolves+execs the binary from it, never the global PATH. **Shipped:** `uv` (Python, PyPI-wheel-preferred + `[all]`-extras heuristic), `binary` (prebuilt release asset, platform-matched), `docker` (ephemeral container, cwd mounted). **Detected but deferred:** `npm`, `cargo`, `go` (register as skills-only until their runtimes ship). To add a new runtime, create `config/runtimes/<name>.py` subclassing `Runtime` (implement `install`/`resolve_binary`/`run`/`uninstall`/`is_installed`), add a marker in `config/runtimes/__init__.py`, and add the kind to `RUNTIME_KINDS`.

### Kind-specific content formats
The `--kind` flag drives which plural container content is linked into (`~/.aidem/skills`, `~/.aidem/rules`, etc.). Content detection is now kind-aware (`_content_source_for` looks for `rule.md`/`rules/*.md` for rules, `SKILL.md`/`skills/**/SKILL.md` for skills). Remaining work: recognize richer per-kind conventions (e.g. `.mdc` frontmatter for project rules, JSON for MCP) when those kinds are built out.

To add support for a new artifact type, add a generator in `config/generators/<tool>.py` that implements the kind's bridge method (e.g. `ensure_rules_bridge`), register the kind in `REGISTRY_KINDS`, and extend `_content_source_for` / `_add_shared_content` for the new shape.

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