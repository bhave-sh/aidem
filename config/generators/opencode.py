from pathlib import Path

from .base import Generator


class OpenCodeGenerator(Generator):
    """Bridge OpenCode's global skills dir to aidem's shared skills library.

    Skills doc: https://opencode.ai/docs/skills
    Global path: ~/.config/opencode/skills/<name>/SKILL.md
    """

    name = "opencode"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".config" / "opencode" / "skills"
