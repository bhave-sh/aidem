from __future__ import annotations

import json
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from .base import Runtime


class UvVenvRuntime(Runtime):
    """Isolated Python tool env via ``uv venv`` + ``uv pip install``.

    aidem owns the env at ``~/.aidem/envs/<name>/`` and installs the tool's
    package into it. The install *source* is decoupled from the git clone so a
    prebuilt PyPI wheel is preferred over an editable-from-clone build (which
    forces a local toolchain build for native-extension packages like headroom).

    Two auto-heuristics (overridable via manifest keys / flags):
      - **PyPI-wheel preference**: if the package is published on PyPI under a
        name matching the repo, install ``<spec>[<extras>]`` from PyPI rather
        than building the clone. Avoids Rust/C builds.
      - **``[all]`` extras default**: if the clone's pyproject declares an
        ``all`` optional-dependency group, default to installing ``[all]`` so
        feature-rich tools work out of the box.
    """

    name = "uv"

    def _package_name(self) -> str | None:
        """PyPI package name for the tool, from spec or the clone's pyproject."""
        spec = self.meta.get("spec")
        if spec:
            return spec.split("[")[0].strip(" '\"")
        repo_path = self.meta.get("_repo_path")
        if repo_path:
            pyproject = Path(repo_path) / "pyproject.toml"
            if pyproject.exists():
                try:
                    data = tomllib.loads(pyproject.read_text())
                    return data.get("project", {}).get("name")
                except Exception:
                    pass
        return None

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

    def _on_pypi(self, package: str) -> bool:
        """Whether <package> is published on PyPI (JSON API, 5s timeout)."""
        url = f"https://pypi.org/pypi/{package}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aidem"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return False

    def _install_command(self, source: str | None) -> list[str]:
        spec = self.meta.get("spec")
        if spec:
            # An explicit spec carries its own extras; honor it verbatim.
            target = spec
            kind = "pypi"
        else:
            pkg = self._package_name()
            extras = self._detect_extras()
            extras_suffix = f"[{extras}]" if extras else ""
            if pkg and self._on_pypi(pkg):
                target = f"{pkg}{extras_suffix}"
                kind = "pypi"
            else:
                repo_path = self.meta.get("_repo_path")
                if not repo_path:
                    raise RuntimeError("uv runtime: no spec and no clone to install from")
                target = str(repo_path)
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
