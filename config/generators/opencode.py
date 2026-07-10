from pathlib import Path

from .base import Generator


class OpenCodeGenerator(Generator):
    """Bridge OpenCode's global skills + rules to aidem's shared libraries.

    Skills doc: https://opencode.ai/docs/skills
    Skills global path: ~/.config/opencode/skills/<name>/SKILL.md

    Rules doc: https://opencode.ai/docs/rules
    Rules are loaded via the ``instructions`` array in opencode.json (paths,
    globs, or URLs). aidem injects a glob pointing at its shared rules library.
    """

    name = "opencode"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".config" / "opencode" / "skills"

    def ensure_rules_bridge(self) -> tuple[str, str]:
        """Inject a rules glob into the ``instructions`` array of opencode.json."""
        config_dir = Path.home() / ".config" / "opencode"
        config_dir.mkdir(parents=True, exist_ok=True)
        result = self._inject_into_config_array(
            config_dir / "opencode.json", "instructions",
            str(self.rules_dir) + "/*.md",
        )
        if result is None:
            return ("error", f"{self.name}: ~/.config/opencode/opencode.json is unreadable "
                              "(invalid JSON/JSONC); refusing to clobber it. "
                              "Fix it manually, then re-run `aidem setup`.")
        return result
