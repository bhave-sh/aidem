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

# Kinds of content aidem manages.
REGISTRY_KINDS = ("skill", "rule", "mcp", "memory", "plan")


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
    """Content library directory for a given kind (e.g. 'skill' -> ~/.aidem/skill)."""
    return data_dir() / kind


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


def manifest_path() -> Path:
    return registry_dir() / "manifest.json"


def overlays_dir() -> Path:
    """Shipped package overlays (read-only project-type AGENTS.md templates)."""
    return config_dir() / "overlays"


def canonical_agents() -> Path:
    """Shipped canonical AGENTS.md (read-only template source)."""
    return config_dir() / "AGENTS.md"


def ensure_data_dirs() -> None:
    """Create the data dir and key subdirs if missing."""
    for p in (data_dir(), skills_dir(), registry_dir()):
        p.mkdir(parents=True, exist_ok=True)