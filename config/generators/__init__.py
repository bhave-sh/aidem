from pathlib import Path

from .claude import ClaudeGenerator
from .cursor import CursorGenerator
from .github import GitHubGenerator
from .kilo import KiloGenerator
from .opencode import OpenCodeGenerator
from .windsurf import WindsurfGenerator


def discover_generators(config_dir: Path, skills_dir: Path | None = None):
    """All generators, in tool order.

    skills_dir defaults to the user data dir's shared skill library.
    """
    from aidem_paths import skills_dir as _skills_dir
    sd = skills_dir if skills_dir is not None else _skills_dir()
    return [
        KiloGenerator(config_dir, sd),
        ClaudeGenerator(config_dir, sd),
        CursorGenerator(config_dir, sd),
        OpenCodeGenerator(config_dir, sd),
        WindsurfGenerator(config_dir, sd),
        GitHubGenerator(config_dir, sd),
    ]


def shared_skills_dir(config_dir: Path) -> Path:
    """The shared canonical skill library (user data dir / skills).

    Real files (created skills) and symlinks (registered repo skills) both live
    here. Passthrough tools (Kilo, Claude) dir-symlink their dot-folders to this.
    Transform tools (Cursor) regenerate derived mirrors from here.
    """
    from aidem_paths import skills_dir as _skills_dir
    return _skills_dir()


def bridgeable_generators(config_dir: Path):
    """Generators that actually have a global skills path (can be bridged)."""
    return [g for g in discover_generators(config_dir) if g.global_path is not None]


def transform_generators(config_dir: Path):
    """Generators that maintain a transformed mirror (need regeneration)."""
    return [g for g in discover_generators(config_dir) if not g.passthrough]


def ensure_all_bridges(config_dir: Path):
    """Create every tool's one-time dir symlink. Returns list of (status, msg)."""
    return [g.ensure_bridge() for g in discover_generators(config_dir)]


def regenerate_all_mirrors(config_dir: Path) -> int:
    """Regenerate all transform mirrors (Cursor). Returns total files written."""
    total = 0
    for g in transform_generators(config_dir):
        total += g.regenerate()
    return total