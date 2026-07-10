"""Centralized path resolution for aidem.

aidem distinguishes two kinds of paths:

- **Package paths** (shipped, read-only, live next to the installed CLI):
  generators, overlays, the canonical AGENTS.md template.
- **Data paths** (user-writable, persistent, survive upgrades): the shared
  content libraries (skills, rules, mcp, memory, plans), the registry
  manifest and its git submodules.

By default the data dir is `~/.aidem`. Override with the `AIDEM_DATA_DIR`
environment variable (used by tests to keep the home directory pristine).

Content kind directories follow the pattern `~/.aidem/<kind>/` (e.g.
`~/.aidem/skills/`, `~/.aidem/rules/`).
"""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

# Kinds of content aidem manages (singular kind *labels*; the --kind values).
REGISTRY_KINDS = ("skill", "rule", "mcp", "memory", "plan")

# Map a singular kind label to its plural *container* directory name.
# Containers hold many entries of a kind, so they use the plural spelling that
# every bridged AI tool also uses (~/.claude/skills, ~/.claude/rules, ...).
# Kinds not yet built fall back to the label itself.
KIND_CONTAINERS = {"skill": "skills", "rule": "rules"}


def kind_container(kind: str) -> str:
    """Plural container name for a kind (e.g. 'skill' -> 'skills')."""
    return KIND_CONTAINERS.get(kind, kind)


def data_dir() -> Path:
    """The writable, persistent aidem data directory.

    Resolution order:
      1. `AIDEM_DATA_DIR` env var (absolute path)
      2. `~/.aidem`
    """
    env = os.environ.get("AIDEM_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".aidem"


def config_dir() -> Path:
    """The shipped package config dir (read-only overlays + canonical AGENTS.md)."""
    return PACKAGE_ROOT / "config"


def kind_dir(kind: str) -> Path:
    """Content library directory for a given kind (e.g. 'skill' -> ~/.aidem/skills)."""
    return data_dir() / kind_container(kind)


def skills_dir() -> Path:
    """User-writable shared skill library."""
    return kind_dir("skill")


def rules_dir() -> Path:
    return kind_dir("rule")


def mcp_dir() -> Path:
    return kind_dir("mcp")


def memory_dir() -> Path:
    return kind_dir("memory")


def plans_dir() -> Path:
    return kind_dir("plan")


def registry_dir() -> Path:
    """User-writable registry root holding manifest + git submodules."""
    return data_dir() / "registry"


def envs_dir() -> Path:
    """User-writable isolated tool environments (one dir per registered tool).

    Each tool's isolated env lives at envs_dir()/<name>/ with its binaries under
    envs_dir()/<name>/bin/. aidem resolves and execs binaries from here, never
    from the global PATH (~/.local/bin), so tools are sandboxed per-name and
    same-named CLIs never collide.
    """
    return data_dir() / "envs"


def env_dir(name: str) -> Path:
    """Isolated environment directory for a registered tool by name."""
    return envs_dir() / name


def manifest_path() -> Path:
    return registry_dir() / "manifest.json"


def overlays_dir() -> Path:
    """Shipped package overlays (read-only project-type AGENTS.md templates)."""
    return config_dir() / "overlays"


def canonical_agents() -> Path:
    """Shipped canonical AGENTS.md (read-only template source)."""
    return config_dir() / "AGENTS.md"


def ensure_data_dirs() -> None:
    """Create the data dir and key subdirs if missing.

    Migrates legacy singular content containers (~/.aidem/skill, ~/.aidem/rule)
    to plural names (~/.aidem/skills, ~/.aidem/rules) on first run. The registry
    kind subdirs keep the singular kind label (internal clone storage; keeps
    existing manifest paths stable).
    """
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    for singular, plural in (("skill", "skills"), ("rule", "rules")):
        old = data / singular
        new = data / plural
        if old.exists() and not new.exists():
            old.rename(new)
        elif old.exists() and new.exists():
            # Partial manual migration: merge old contents into new so entries
            # in the singular dir don't silently vanish. Non-conflicting entries
            # move over; conflicts are left in place with a warning printed.
            for entry in list(old.iterdir()):
                dest = new / entry.name
                if dest.exists():
                    print(f"aidem: migration conflict — {entry} exists in both "
                          f"{old} and {new}; leaving the singular copy in place.")
                else:
                    entry.rename(dest)
    for p in (skills_dir(), rules_dir(), registry_dir(), envs_dir()):
        p.mkdir(parents=True, exist_ok=True)