from pathlib import Path

import json
import re


def _strip_jsonc_comments(text: str) -> str:
    """Strip // and /* */ comments from JSONC text so json.loads can parse it.

    Preserves comments inside string literals. JSONC configs (Kilo, OpenCode)
    allow comments that stdlib json rejects; without stripping, a parse failure
    would silently clobber the user's whole config.
    """
    def repl(match):
        s = match.group(0)
        return s if s.startswith('"') else ""
    pattern = re.compile(
        r'"(?:\\.|[^"\\])*"'
        r'|//[^\n]*'
        r'|/\*.*?\*/',
        re.DOTALL,
    )
    return pattern.sub(repl, text)


class Generator:
    """Base class for per-tool content bridges.

    Each generator owns how a single AI tool consumes aidem's shared libraries,
    for each content kind aidem supports:

      - skills: ``global_path`` -> tool's skills dot-folder; ``staging_dir`` ->
        aidem's shared skills library. Passthrough tools dir-symlink directly;
        transform tools (legacy Cursor .mdc) override ``regenerate``.
      - rules: ``rules_global_path`` -> tool's rules location (dir or None);
        ``rules_staging_dir`` -> aidem's shared rules library. Passthrough tools
        dir-symlink; config-array tools (Kilo, OpenCode) override
        ``ensure_rules_bridge``; concat tools (Windsurf) override
        ``regenerate_rules``.

    Keeping each tool in its own file means switching a tool from passthrough to
    a proprietary format later only touches that one file.
    """

    name = "base"
    passthrough = True
    extension = "md"

    def __init__(self, config_dir: Path, skills_dir: Path | None = None,
                 rules_dir: Path | None = None):
        self.config_dir = config_dir
        # The shared canonical libraries live in the user data dir (writable,
        # persistent); config_dir holds shipped read-only assets.
        if skills_dir is None:
            from aidem_paths import skills_dir as _skills_dir
            skills_dir = _skills_dir()
        if rules_dir is None:
            from aidem_paths import rules_dir as _rules_dir
            rules_dir = _rules_dir()
        self.skills_dir = skills_dir
        self.rules_dir = rules_dir

    @property
    def global_path(self) -> Path | None:
        """The tool's own skills dot-folder. None if unsupported."""
        return None

    @property
    def staging_dir(self) -> Path:
        """aidem-internal dir the tool's skills global_path symlinks to."""
        return self.skills_dir

    @property
    def rules_global_path(self) -> Path | None:
        """The tool's own global rules location. None if unsupported.

        For dir-shaped tools (Claude) this is a directory passthrough tools
        symlink into. Config-array tools (Kilo, OpenCode) and concat tools
        (Windsurf) leave this None and override ``ensure_rules_bridge`` /
        ``regenerate_rules`` instead.
        """
        return None

    @property
    def rules_staging_dir(self) -> Path:
        """aidem-internal dir the tool's rules_global_path symlinks to."""
        return self.rules_dir

    def skill_filename(self, name: str) -> str:
        return f"{name}.{self.extension}"

    def format_skill(self, content: str, name: str) -> str:
        """Transform a skill's content into this tool's format (passthrough = identity)."""
        return content

    def _ensure_dir_symlink(self, gp: Path | None, staging: Path,
                            label: str) -> tuple[str, str]:
        """Create a one-time dir symlink: gp -> staging.

        Idempotent. Refuses to clobber an existing real directory. Skips if the
        tool's parent directory is missing (tool not installed). Returns
        (status, message) where status in {"ok", "skipped", "error"}.
        """
        if gp is None:
            return ("skipped", f"{self.name}: no global {label} path")
        if not gp.parent.exists():
            return ("skipped", f"{self.name}: {gp.parent} not found (tool not installed)")
        staging.mkdir(parents=True, exist_ok=True)
        if gp.is_symlink():
            try:
                if gp.resolve() == staging.resolve():
                    return ("skipped", f"{self.name}: {label} bridge already in place")
            except Exception:
                pass
            gp.unlink()
        elif gp.exists():
            return ("error", f"{self.name}: {gp} exists and is not a symlink. "
                              "Move/backup its contents and remove the directory, "
                              "then re-run `aidem setup`.")
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.symlink_to(staging)
        return ("ok", f"{self.name}: {gp} -> {staging}")

    def ensure_bridge(self) -> tuple[str, str]:
        """Create the one-time skills dir symlink: global_path -> staging_dir."""
        return self._ensure_dir_symlink(self.global_path, self.staging_dir, "skills")

    def ensure_rules_bridge(self) -> tuple[str, str]:
        """Create the one-time rules dir symlink (passthrough default).

        Config-array / concat tools override this.
        """
        return self._ensure_dir_symlink(
            self.rules_global_path, self.rules_staging_dir, "rules")

    def regenerate(self) -> int:
        """Rebuild this tool's transformed skills mirror (passthrough = no-op)."""
        return 0

    def regenerate_rules(self) -> int:
        """Rebuild this tool's transformed rules mirror (passthrough = no-op).

        Concat tools (Windsurf) override to merge all rules into one file.
        """
        return 0

    def rules_warnings(self) -> list[str]:
        """Tool-specific warnings about the last rules bridge/mirror (empty by default)."""
        return []

    def _inject_into_config_array(self, config_path: Path, key: str,
                                  entry: str) -> tuple[str, str] | None:
        """Append ``entry`` to a JSON/JSONC config's top-level ``key`` array.

        Shared by config-array bridges (Kilo, OpenCode). Parses JSONC
        tolerantly (strips comments); on parse failure warns and returns None
        instead of clobbering the user's config. Idempotent: a no-op if the
        entry is already present. Returns (status, message) on success, or
        None to signal the caller should report a skip.
        """
        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(_strip_jsonc_comments(config_path.read_text()))
                if not isinstance(config, dict):
                    raise ValueError("config root is not an object")
            except (json.JSONDecodeError, ValueError, OSError):
                return None
        arr: list = config.setdefault(key, [])
        if entry in arr:
            return ("skipped", f"{self.name}: {key} entry already in {config_path}")
        arr.append(entry)
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        return ("ok", f"{self.name}: added {key} entry to {config_path}")
