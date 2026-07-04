from pathlib import Path

from .base import Generator


class KiloGenerator(Generator):
    """Bridge Kilo's global skills dir to aidem's shared skills library."""

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
        import json

        config_dir = Path.home() / ".config" / "kilo"
        config_dir.mkdir(parents=True, exist_ok=True)

        config_path = config_dir / "kilo.jsonc"
        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        skills = config.setdefault("skills", {})
        paths: list = skills.get("paths", [])
        aidem_path = str(self.skills_dir)
        if aidem_path not in paths:
            paths.append(aidem_path)
            skills["paths"] = paths
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            return ("ok", f"{self.name}: added skills.paths fallback in {config_path}")

        return ("ok", f"{self.name}: skills.paths already contains {aidem_path}")