from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .base import Runtime


class DockerRuntime(Runtime):
    """Hardened tool env via Docker (ephemeral containers).

    For tools that ship a Dockerfile or a published image, aidem runs the tool
    in an ephemeral, network-disabled, non-privileged container. The current
    working directory is read-only by default; write access is explicit.

    Image source auto-heuristic: a manifest ``image`` takes precedence;
    otherwise, if the clone has a ``Dockerfile``, build ``aidem/<name>`` from
    it. The container execs the tool's binary name with the passed args.
    """

    name = "docker"

    def _image(self) -> str:
        image = self.meta.get("image") or f"aidem/{self.meta.get('name', 'tool')}"
        if not isinstance(image, str) or not image or image.startswith("-") or "\x00" in image:
            raise RuntimeError("docker runtime: invalid image reference")
        if self.meta.get("image") and "@sha256:" not in image:
            raise RuntimeError("docker runtime: published images must use a digest")
        return image

    def _dockerfile_present(self) -> bool:
        repo_path = self.meta.get("_repo_path")
        return bool(repo_path and (Path(repo_path) / "Dockerfile").exists())

    def _docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def install(self, source: str | None = None) -> str:
        if not self._docker_available():
            raise RuntimeError("docker runtime: 'docker' not found on PATH")
        image = self.meta.get("image")
        if image:
            subprocess.run(["docker", "pull", self._image()], check=True)
            return f"pulled {image}"
        if self._dockerfile_present():
            repo_path = self.meta.get("_repo_path")
            tag = self._image()
            subprocess.run(["docker", "build", "-t", tag, str(repo_path)], check=True)
            return f"built {tag} from Dockerfile"
        raise RuntimeError(
            "docker runtime: no --image given and no Dockerfile in the clone. "
            "Pass --image <ref> or add a Dockerfile."
        )

    def resolve_binary(self) -> Path | None:
        # Docker doesn't expose a host binary; 'installed' = image present.
        return None

    def run(self, args: list[str]) -> int:
        if not self._docker_available():
            raise RuntimeError("docker runtime: 'docker' not found on PATH")
        if not self.is_installed():
            raise RuntimeError(
                f"docker runtime: image '{self._image()}' not installed. "
                f"Run `aidem registry install {self.meta.get('name', '')}`."
            )
        image = self._image()
        cwd = os.getcwd()
        mode = "rw" if self.meta.get("write_worktree") else "ro"
        cmd = [
            "docker", "run", "--rm", "--read-only", "--network=none",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
        ]
        if hasattr(os, "getuid"):
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += ["-v", f"{cwd}:/work:{mode}", "-w", "/work"]
        # Pass through a TTY when aidem's own stdin is interactive.
        if sys.stdin.isatty():
            cmd.append("-it")
        if not self.binary_name or self.binary_name.startswith("-") or "\x00" in self.binary_name:
            raise RuntimeError("docker runtime: invalid executable name")
        cmd += [image, self.binary_name] + list(args)
        os.execvp("docker", cmd)
        return 127

    def uninstall(self) -> str:
        # Remove the locally-built image (aidem/<name>) so a later setup rebuilds
        # fresh. Leave user-specified --image pulls alone (they may be shared
        # and large; user can `docker rmi` those explicitly).
        image = self._image()
        if self.meta.get("image") is None and shutil.which("docker"):
            subprocess.run(["docker", "rmi", image],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
        if self.env_path.exists():
            shutil.rmtree(self.env_path)
            return f"removed image {image} + env {self.env_path}"
        return f"removed image {image}"

    def is_installed(self) -> bool:
        if not self._docker_available():
            return False
        res = subprocess.run(
            ["docker", "image", "inspect", self._image()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
