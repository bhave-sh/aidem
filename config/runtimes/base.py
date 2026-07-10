from __future__ import annotations

import shutil
from pathlib import Path


class Runtime:
    """Base class for per-tool isolated execution runtimes.

    Each runtime owns how a single registered tool is installed into an
    aidem-owned, per-name isolated environment under ``~/.aidem/envs/<name>/``
    and how its binary is located and executed. aidem resolves and execs the
    binary from the env, never from the global PATH, so tools are sandboxed
    per-name and same-named CLIs never collide.

    Subclasses implement the lifecycle for one ecosystem:
      - ``install``: build/populate the env (venv, binary download, image pull, ...)
      - ``resolve_binary``: absolute path to the tool's CLI inside the env
      - ``run``: exec the tool with args (default: os.execvp on resolve_binary)
      - ``uninstall``: tear the env down
      - ``is_installed``: whether the env is populated and runnable

    The manifest entry is passed in as ``meta`` (the dict stored under the
    tool's name in manifest.json). Runtimes read runtime-specific keys
    (``extras``, ``asset``, ``image``, ``spec``, ``binary``) from it.
    """

    name = "base"

    def __init__(self, meta: dict, env_path: Path):
        self.meta = meta
        self.env_path = env_path

    @property
    def binary_name(self) -> str:
        """The CLI entry-point name (from manifest 'binary')."""
        return self.meta.get("binary", "")

    def install(self, source: str | None = None) -> str:
        """Populate the env. Returns a short human status message.

        ``source`` is the git URL the repo was cloned from (for runtimes that
        build from the clone); PyPI/binary/image runtimes may ignore it.
        """
        raise NotImplementedError

    def resolve_binary(self) -> Path | None:
        """Absolute path to the tool's CLI inside the env, or None if absent."""
        if not self.binary_name:
            return None
        candidate = self.env_path / "bin" / self.binary_name
        return candidate if candidate.exists() else None

    def run(self, args: list[str]) -> int:
        """Exec the tool, passing args through. Returns only on exec failure."""
        import os
        binary = self.resolve_binary()
        if binary is None:
            raise RuntimeError(
                f"{self.name}: binary '{self.binary_name}' not found in env "
                f"{self.env_path}. Run `aidem registry install <name>`."
            )
        os.execvp(str(binary), [str(binary)] + list(args))
        return 127

    def uninstall(self) -> str:
        """Tear the env down (remove the env dir)."""
        if self.env_path.exists():
            shutil.rmtree(self.env_path)
            return f"removed env {self.env_path}"
        return f"env {self.env_path} already absent"

    def is_installed(self) -> bool:
        """Whether the env is populated and the binary is runnable."""
        return self.resolve_binary() is not None
