from pathlib import Path


class Generator:
    """Base class for per-tool skill bridges.

    Each generator owns how a single AI tool consumes skills:
      - global_path: the tool's dot-folder where it reads skills (None if unsupported)
      - staging_dir: the aidem-internal directory the tool's dot-folder points to
      - passthrough: True if the tool reads plain markdown verbatim (single shared
        config/skills dir); False if it needs a transformed mirror (e.g., Cursor .mdc)
      - format_skill: content transform (only override for transform tools)

    Keeping each tool in its own file means switching a tool from passthrough to a
    proprietary format later only touches that one file.
    """

    name = "base"
    passthrough = True
    extension = "md"

    def __init__(self, config_dir: Path, skills_dir: Path | None = None):
        self.config_dir = config_dir
        # The shared canonical skill library lives in the user data dir
        # (writable, persistent); config_dir holds shipped read-only assets.
        if skills_dir is None:
            from aidem_paths import skills_dir as _skills_dir
            skills_dir = _skills_dir()
        self.skills_dir = skills_dir

    @property
    def global_path(self) -> Path | None:
        """The tool's own dot-folder. None if this tool has no global skills path."""
        return None

    @property
    def staging_dir(self) -> Path:
        """aidem-internal dir the tool's global_path symlinks to.

        Passthrough tools read directly from the shared skills_dir.
        Transform tools (Cursor) override to a separate mirror dir.
        """
        return self.skills_dir

    def skill_filename(self, name: str) -> str:
        return f"{name}.{self.extension}"

    def format_skill(self, content: str, name: str) -> str:
        """Transform a skill's content into this tool's format (passthrough = identity)."""
        return content

    def ensure_bridge(self) -> tuple[str, str]:
        """Create the one-time dir symlink: global_path -> staging_dir.

        Idempotent. Refuses to clobber an existing real directory.
        Skips if the tool's parent directory is missing (tool not installed).
        Returns (status, message) where status in {"ok","skipped","error"}.
        """
        gp = self.global_path
        if gp is None:
            return ("skipped", f"{self.name}: no global skills path")

        if not gp.parent.exists():
            return ("skipped", f"{self.name}: {gp.parent} not found (tool not installed)")

        self.staging_dir.mkdir(parents=True, exist_ok=True)

        if gp.is_symlink():
            try:
                if gp.resolve() == self.staging_dir.resolve():
                    return ("skipped", f"{self.name}: bridge already in place")
            except Exception:
                pass
            gp.unlink()
        elif gp.exists():
            return ("error", f"{self.name}: {gp} exists and is not a symlink. "
                              "Move/backup its contents and remove the directory, "
                              "then re-run `aidem setup`.")

        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.symlink_to(self.staging_dir)
        return ("ok", f"{self.name}: {gp} -> {self.staging_dir}")

    def regenerate(self) -> int:
        """Rebuild this tool's transformed mirror from the shared skills_dir.

        Passthrough tools read skills_dir directly, so this is a no-op (0).
        Transform tools (Cursor) override to write formatted copies.
        """
        return 0