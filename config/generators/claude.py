from pathlib import Path

from .base import Generator


class ClaudeGenerator(Generator):
    """Bridge Claude Code's global skills + rules dirs to aidem's shared libraries.

    Skills doc: https://docs.anthropic.com/en/docs/claude-code/skills
    Skills global path: ~/.claude/skills/<name>/SKILL.md

    Rules doc: https://docs.anthropic.com/en/docs/claude-code/memory
    Rules global path: ~/.claude/rules/*.md (multi-file, supports symlinks).
    """

    name = "claude"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".claude" / "skills"

    @property
    def rules_global_path(self) -> Path | None:
        return Path.home() / ".claude" / "rules"
