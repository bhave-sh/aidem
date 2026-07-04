from pathlib import Path

from .base import Generator


class ClaudeGenerator(Generator):
    """Bridge Claude Code's global skills dir to aidem's shared skills library.

    Skills doc: https://docs.anthropic.com/en/docs/claude-code/skills
    Global path: ~/.claude/skills/<name>/SKILL.md
    """

    name = "claude"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".claude" / "skills"
