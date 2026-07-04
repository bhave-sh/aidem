from pathlib import Path

from .base import Generator


class WindsurfGenerator(Generator):
    """Bridge Windsurf's global skills dir to aidem's shared skills library.

    Skills doc: https://docs.windsurf.com/windsurf/cascade/skills.md
    Global path: ~/.codeium/windsurf/skills/<name>/SKILL.md
    """

    name = "windsurf"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".codeium" / "windsurf" / "skills"
