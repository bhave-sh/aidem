from pathlib import Path

from .base import Generator


class WindsurfGenerator(Generator):
    """Bridge Windsurf's global skills + rules to aidem's shared libraries.

    Skills doc: https://docs.windsurf.com/windsurf/cascade/skills.md
    Skills global path: ~/.codeium/windsurf/skills/<name>/SKILL.md

    Rules doc: https://docs.windsurf.com/windsurf/cascade/memories
    Global rules live in a SINGLE file ~/.codeium/windsurf/memories/global_rules.md
    (always-on, no frontmatter). Windsurf enforces a 6,000-character cap on it.
    aidem concatenates all rules from its shared rules library into that file.
    """

    name = "windsurf"
    passthrough = True

    WINDSURF_RULES_CHAR_LIMIT = 6000

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".codeium" / "windsurf" / "skills"

    @property
    def rules_target(self) -> Path:
        return Path.home() / ".codeium" / "windsurf" / "memories" / "global_rules.md"

    def ensure_rules_bridge(self) -> tuple[str, str]:
        # No directory to symlink; the concat mirror is built in regenerate_rules.
        return ("skipped", f"{self.name}: rules bridged via concat mirror on setup")

    def regenerate_rules(self) -> int:
        """Concatenate all shared rules into Windsurf's single global_rules.md."""
        src = self.rules_dir
        if not src.exists():
            return 0
        files = sorted(src.glob("*.md"))
        if not files:
            return 0
        target = self.rules_target
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "\n\n".join(f.read_text().rstrip() for f in files) + "\n"
        target.write_text(body)
        return len(files)

    def rules_warnings(self) -> list[str]:
        target = self.rules_target
        if not target.exists():
            return []
        size = len(target.read_text())
        if size > self.WINDSURF_RULES_CHAR_LIMIT:
            return [f"windsurf: global_rules.md is {size} chars; Windsurf caps at "
                    f"{self.WINDSURF_RULES_CHAR_LIMIT}. Trim or remove rules."]
        return []
