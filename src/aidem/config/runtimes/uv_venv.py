from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from .base import Runtime


class UvVenvRuntime(Runtime):
    """Isolated Python tool env via ``uv venv`` + ``uv pip install``.

    aidem owns the env at ``~/.aidem/envs/<name>/`` and installs the tool's
    package into it. Without an explicit ``spec``, installation always targets
    the checked-out repository so repository identity cannot silently switch to
    an unrelated PyPI package.
    """

    name = "uv"

    def _detect_extras(self) -> str:
        """Default extras string: explicit 'all' if declared, else empty.

        Honors a manifest 'extras' override; otherwise inspects the clone's
        pyproject optional-dependencies for an 'all' group.
        """
        extras = self.meta.get("extras")
        if extras is not None:
            return extras
        repo_path = self.meta.get("_repo_path")
        if repo_path:
            pyproject = Path(repo_path) / "pyproject.toml"
            if pyproject.exists():
                try:
                    data = tomllib.loads(pyproject.read_text())
                    opt = data.get("project", {}).get("optional-dependencies", {})
                    if "all" in opt:
                        return "all"
                except Exception:
                    pass
        return ""

    def _install_command(self, source: str | None) -> list[str]:
        spec = self.meta.get("spec")
        if spec:
            if (not isinstance(spec, str) or not spec or spec.startswith("-")
                    or any(ord(char) < 32 for char in spec)):
                raise RuntimeError("uv runtime: invalid explicit package spec")
            # An explicit spec carries its own extras; honor it verbatim.
            target = spec
            kind = "pypi"
        else:
            extras = self._detect_extras()
            if not isinstance(extras, str) or any(ord(char) < 32 for char in extras):
                raise RuntimeError("uv runtime: invalid extras value")
            repo_path = self.meta.get("_repo_path")
            if not repo_path:
                raise RuntimeError("uv runtime: no spec and no clone to install from")
            target = str(repo_path)
            if extras:
                target = f"{target}[{extras}]"
            kind = "editable"
        self._last_kind = kind
        python = str(self.env_path / "bin" / "python")
        if kind == "pypi":
            return ["uv", "pip", "install", "--python", python, target]
        return ["uv", "pip", "install", "-e", "--python", python, target]

    def install(self, source: str | None = None) -> str:
        self.env_path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uv", "venv", str(self.env_path)], check=True)
        cmd = self._install_command(source)
        subprocess.run(cmd, check=True)
        return f"uv venv + pip install ({getattr(self, '_last_kind', 'pypi')})"

    def resolve_binary(self) -> Path | None:
        if not self.binary_name:
            return None
        candidate = self.env_path / "bin" / self.binary_name
        return candidate if candidate.exists() else None

    def is_installed(self) -> bool:
        return (self.env_path / "pyvenv.cfg").exists() and self.resolve_binary() is not None
