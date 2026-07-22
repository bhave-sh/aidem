from __future__ import annotations

from pathlib import Path

from .base import Runtime
from .binary import BinaryRuntime
from .docker import DockerRuntime
from .uv_venv import UvVenvRuntime

# File-marker -> default runtime kind. Detection = file presence, not README parsing.
RUNTIME_MARKERS = {
    "pyproject.toml": "uv",
    "Dockerfile": "docker",
    "Cargo.toml": "binary",   # prefer prebuilt release asset over a host Rust build
    "package.json": "npm",    # detected but deferred (NpxRuntime not yet implemented)
    "go.mod": "binary",       # go release assets are prebuilt binaries
}

# Runtime kinds aidem can fully dispatch to (install/run/uninstall).
SUPPORTED_RUNTIMES = ("uv", "binary", "docker")

# Ecosystems detected by markers but not yet implemented. These register as
# skills-only with a clear note until their runtimes ship. Derived from the
# marker set so the two can't drift: any marker value not in SUPPORTED_RUNTIMES.
DEFERRED_RUNTIMES = tuple(
    sorted({v for v in RUNTIME_MARKERS.values() if v not in SUPPORTED_RUNTIMES})
)


def detect_runtime(repo_path: Path) -> str | None:
    """Pick a default runtime kind from file markers in the cloned repo."""
    for marker, runtime in RUNTIME_MARKERS.items():
        if (repo_path / marker).exists():
            return runtime
    return None


def runtime_for(meta: dict) -> Runtime:
    """Construct the runtime adapter for a manifest entry.

    Reads 'runtime' from meta and dispatches. The env path is resolved from
    aidem_paths.env_dir(name) so runtimes never touch the global PATH.
    """
    from aidem.paths import env_dir
    name = meta.get("name") or meta.get("binary") or ""
    env_path = env_dir(name)
    kind = meta.get("runtime", "uv")
    if kind == "binary":
        return BinaryRuntime(meta, env_path)
    if kind == "docker":
        return DockerRuntime(meta, env_path)
    return UvVenvRuntime(meta, env_path)
