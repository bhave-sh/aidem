from pathlib import Path

from .base import Generator


class CursorGenerator(Generator):
    """Bridge Cursor's global skills dir to aidem's shared skills library.

    Skills doc: https://cursor.com/docs/skills
    Global path: ~/.cursor/skills/<name>/SKILL.md
    """

    name = "cursor"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".cursor" / "skills"
