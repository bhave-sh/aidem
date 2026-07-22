from pathlib import Path

from .base import Generator


class CursorGenerator(Generator):
    """Bridge Cursor's global skills dir to aidem's shared skills library.

    Skills doc: https://cursor.com/docs/skills
    Skills global path: ~/.cursor/skills/<name>/SKILL.md

    Rules: Cursor has no file-based *global* rules location (User Rules are
    UI-managed; project rules are .cursor/rules/*.mdc with frontmatter and are
    repo-level only). So rules_global_path stays None -> skipped.
    """

    name = "cursor"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".cursor" / "skills"
