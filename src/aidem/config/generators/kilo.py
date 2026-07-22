from pathlib import Path

from .base import Generator


class KiloGenerator(Generator):
    """Bridge Kilo's global skills + rules to aidem's shared libraries.

    Skills doc: https://kilo.ai/docs/customize/skills
    Skills global path: ~/.kilo/skills/<name>/SKILL.md (symlink; falls back to a
    skills.paths entry in ~/.config/kilo/kilo.jsonc when the symlink can't be
    created).

    Rules doc: https://kilo.ai/docs/customize/custom-rules
    Rules are loaded via the ``instructions`` array in kilo.jsonc (paths, globs,
    or URLs). aidem injects a glob pointing at its shared rules library.
    """

    name = "kilo"
    passthrough = True

    @property
    def global_path(self) -> Path | None:
        return Path.home() / ".kilo" / "skills"

    def ensure_bridge(self) -> tuple[str, str]:
        status, msg = super().ensure_bridge()
        if status == "ok":
            return status, msg
        if "bridge already in place" in msg:
            return status, msg
        return self._fallback_config_bridge()

    def _fallback_config_bridge(self) -> tuple[str, str]:
        config_dir = Path.home() / ".config" / "kilo"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "kilo.jsonc"
        aidem_path = str(self.skills_dir)

        config: dict = {}
        if config_path.exists():
            import json
            from .base import _strip_jsonc_comments
            try:
                config = json.loads(_strip_jsonc_comments(config_path.read_text()))
                if not isinstance(config, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError, OSError):
                return ("error", f"{self.name}: {config_path} is unreadable "
                                  "(invalid JSON/JSONC); refusing to clobber it. "
                                  "Fix it manually, then re-run `aidem setup`.")
        skills = config.setdefault("skills", {})
        paths: list = skills.get("paths", [])
        if aidem_path not in paths:
            paths.append(aidem_path)
            skills["paths"] = paths
            import json
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            return ("ok", f"{self.name}: added skills.paths fallback in {config_path}")
        return ("ok", f"{self.name}: skills.paths already contains {aidem_path}")

    def ensure_rules_bridge(self) -> tuple[str, str]:
        """Inject a rules glob into the ``instructions`` array of kilo.jsonc."""
        config_dir = Path.home() / ".config" / "kilo"
        config_dir.mkdir(parents=True, exist_ok=True)
        result = self._inject_into_config_array(
            config_dir / "kilo.jsonc", "instructions",
            str(self.rules_dir) + "/*.md",
        )
        if result is None:
            return ("error", f"{self.name}: ~/.config/kilo/kilo.jsonc is unreadable "
                              "(invalid JSON/JSONC); refusing to clobber it. "
                              "Fix it manually, then re-run `aidem setup`.")
        return result
