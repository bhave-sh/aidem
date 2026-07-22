"""Tests for runtime adapters and env-owned isolation (no global PATH)."""

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from aidem import paths as aidem_paths
from aidem import cli as aidem_cli


@pytest.fixture()
def skill_repo(tmp_path):
    """A throwaway local git repo exposing a skill.md at the repo root."""
    repo = tmp_path / "src-repo"
    repo.mkdir()
    (repo / "skill.md").write_text("# Skill: external-demo\n## Purpose\nDemo.\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture(autouse=True)
def _allow_file_transport(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1", prepend=False)
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow", prepend=False)
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always", prepend=False)


def _reload(fake_dirs):
    import importlib
    importlib.reload(aidem_paths)
    importlib.reload(aidem_cli)


def _run_cmd_mock(real_run):
    """Capture subprocess.run calls into a list while still executing git/uv when allowed."""
    calls: list[list[str]] = []

    def _mock(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    return _mock, calls


def test_envs_dir_created_and_per_tool(fake_dirs):
    _reload(fake_dirs)
    aidem_paths.ensure_data_dirs()
    assert aidem_paths.envs_dir().is_dir()
    assert aidem_paths.env_dir("rtk") == fake_dirs["data"] / "envs" / "rtk"


def test_detect_runtime_markers(fake_dirs, tmp_path):
    _reload(fake_dirs)
    from aidem.config.runtimes import detect_runtime
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_runtime(repo) == "uv"
    (repo / "pyproject.toml").unlink()
    (repo / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert detect_runtime(repo) == "binary"
    (repo / "Cargo.toml").unlink()
    (repo / "Dockerfile").write_text("FROM alpine\n")
    assert detect_runtime(repo) == "docker"


def test_runtime_for_dispatches_by_kind(fake_dirs):
    _reload(fake_dirs)
    from aidem.config.runtimes import runtime_for, UvVenvRuntime, BinaryRuntime, DockerRuntime
    assert isinstance(runtime_for({"runtime": "uv", "name": "t"}), UvVenvRuntime)
    assert isinstance(runtime_for({"runtime": "binary", "name": "t"}), BinaryRuntime)
    assert isinstance(runtime_for({"runtime": "docker", "name": "t"}), DockerRuntime)


def test_uv_venv_install_runs_uv_venv_then_pip(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(
        "aidem.config.runtimes.uv_venv.UvVenvRuntime._on_pypi",
        lambda self, pkg: True,
    )
    from aidem.config.runtimes import UvVenvRuntime
    meta = {"runtime": "uv", "name": "demo", "binary": "demo", "spec": "demo-ai[all]"}
    rt = UvVenvRuntime(meta, aidem_paths.env_dir("demo"))
    msg = rt.install("https://github.com/o/demo")
    assert any(cmd[:2] == ["uv", "venv"] for cmd in calls)
    assert any(cmd[:3] == ["uv", "pip", "install"] and "demo-ai[all]" in cmd for cmd in calls)
    assert rt.is_installed() is False  # pyvenv.cfg not actually created by mock


def test_uv_venv_all_extras_heuristic(fake_dirs, tmp_path, monkeypatch):
    _reload(fake_dirs)
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='headroom-ai'\n[project.optional-dependencies]\nall=['x']\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(
        "aidem.config.runtimes.uv_venv.UvVenvRuntime._on_pypi", lambda self, pkg: True,
    )
    from aidem.config.runtimes import UvVenvRuntime
    meta = {"runtime": "uv", "name": "headroom", "binary": "headroom", "_repo_path": str(repo)}
    rt = UvVenvRuntime(meta, aidem_paths.env_dir("headroom"))
    assert rt._detect_extras() == "all"
    assert rt._package_name() == "headroom-ai"


def test_uv_venv_pypi_wheel_preference(fake_dirs, tmp_path, monkeypatch):
    _reload(fake_dirs)
    calls = []
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='pkg'\n[project.scripts]\npkg='pkg:main'\n"
    )
    monkeypatch.setattr(
        "aidem.config.runtimes.uv_venv.UvVenvRuntime._on_pypi", lambda self, pkg: True,
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0))
    from aidem.config.runtimes import UvVenvRuntime
    meta = {"runtime": "uv", "name": "pkg", "binary": "pkg", "_repo_path": str(repo)}
    rt = UvVenvRuntime(meta, aidem_paths.env_dir("pkg"))
    rt.install("https://github.com/o/pkg")
    # pip install should target the PyPI name, not the editable clone path.
    pip_calls = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert pip_calls and "pkg" in pip_calls[0] and "-e" not in pip_calls[0]


def test_uv_venv_falls_back_to_editable_when_not_on_pypi(fake_dirs, tmp_path, monkeypatch):
    _reload(fake_dirs)
    calls = []
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='localonly'\n")
    monkeypatch.setattr(
        "aidem.config.runtimes.uv_venv.UvVenvRuntime._on_pypi", lambda self, pkg: False,
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0))
    from aidem.config.runtimes import UvVenvRuntime
    meta = {"runtime": "uv", "name": "lo", "binary": "lo", "_repo_path": str(repo)}
    rt = UvVenvRuntime(meta, aidem_paths.env_dir("lo"))
    rt.install("https://github.com/o/lo")
    pip_calls = [c for c in calls if c[:3] == ["uv", "pip", "install"]]
    assert pip_calls and "-e" in pip_calls[0] and str(repo) in pip_calls[0]


def test_binary_runtime_picks_platform_asset(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    assets = [
        {"name": "rtk-x86_64-unknown-linux-musl.tar.gz",
         "browser_download_url": "https://example/rtk-linux.tar.gz"},
        {"name": "rtk-aarch64-apple-darwin.tar.gz",
         "browser_download_url": "https://example/rtk-mac.tar.gz"},
        {"name": "rtk-x86_64-pc-windows-msvc.zip",
         "browser_download_url": "https://example/rtk-win.zip"},
    ]
    monkeypatch.setattr(
        "aidem.config.runtimes.binary.BinaryRuntime._latest_release_assets",
        lambda self, source: assets,
    )
    from aidem.config.runtimes import BinaryRuntime
    meta = {"runtime": "binary", "name": "rtk", "binary": "rtk", "source": "https://github.com/rtk-ai/rtk"}
    rt = BinaryRuntime(meta, aidem_paths.env_dir("rtk"))

    # Fake download: write a tarball containing an executable named 'rtk'.
    def fake_download(self, url, dest):
        import io, tarfile
        with tarfile.open(dest, "w:gz") as tf:
            data = b"#!/bin/sh\necho rtk\n"
            info = tarfile.TarInfo("rtk")
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(BinaryRuntime, "_download", fake_download)

    msg = rt.install("https://github.com/rtk-ai/rtk")
    assert "downloaded" in msg
    resolved = rt.resolve_binary()
    assert resolved is not None
    assert resolved.parent == aidem_paths.env_dir("rtk") / "bin"
    assert rt.is_installed()


def test_binary_runtime_asset_override(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    assets = [
        {"name": "weird-name.tar.gz", "browser_download_url": "https://example/weird.tar.gz"},
        {"name": "rtk-aarch64-apple-darwin.tar.gz", "browser_download_url": "https://example/rtk.tar.gz"},
    ]
    monkeypatch.setattr(
        "aidem.config.runtimes.binary.BinaryRuntime._latest_release_assets",
        lambda self, source: assets,
    )
    from aidem.config.runtimes import BinaryRuntime
    meta = {"runtime": "binary", "name": "rtk", "binary": "rtk",
            "source": "https://github.com/rtk-ai/rtk", "asset": "weird"}
    rt = BinaryRuntime(meta, aidem_paths.env_dir("rtk"))
    picked = rt._pick_asset(assets)
    assert picked["name"] == "weird-name.tar.gz"


def test_binary_runtime_owner_repo_parsing(fake_dirs):
    _reload(fake_dirs)
    from aidem.config.runtimes.binary import BinaryRuntime
    assert BinaryRuntime._owner_repo("https://github.com/rtk-ai/rtk") == "rtk-ai/rtk"
    assert BinaryRuntime._owner_repo("https://github.com/rtk-ai/rtk.git") == "rtk-ai/rtk"
    assert BinaryRuntime._owner_repo("git@github.com:rtk-ai/rtk.git") == "rtk-ai/rtk"
    assert BinaryRuntime._owner_repo("https://gitlab.com/o/r") is None


def test_docker_runtime_run_mounts_cwd(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    monkeypatch.setattr("aidem.config.runtimes.docker.shutil.which", lambda x: "/usr/local/bin/docker")
    monkeypatch.setattr("aidem.config.runtimes.docker.sys.stdin.isatty", lambda: False)
    monkeypatch.chdir(fake_dirs["home"])
    execved = {}
    def fake_execvp(cmd, args):
        execved["cmd"] = cmd
        execved["args"] = args
        raise SystemExit(0)
    monkeypatch.setattr("aidem.config.runtimes.docker.os.execvp", fake_execvp)
    from aidem.config.runtimes import DockerRuntime
    meta = {"runtime": "docker", "name": "headroom", "binary": "headroom", "image": "ghcr.io/o/h:latest"}
    rt = DockerRuntime(meta, aidem_paths.env_dir("headroom"))
    monkeypatch.setattr(rt, "is_installed", lambda: True)
    try:
        rt.run(["doctor"])
    except SystemExit:
        pass
    assert execved["cmd"] == "docker"
    assert execved["args"][0] == "docker"
    assert "run" in execved["args"]
    assert "--rm" in execved["args"]
    assert "ghcr.io/o/h:latest" in execved["args"]
    assert "doctor" in execved["args"]
    # cwd mounted at /work
    assert any("/work" in a for a in execved["args"])


def test_docker_runtime_run_before_install_errors_clearly(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    monkeypatch.setattr("aidem.config.runtimes.docker.shutil.which", lambda x: "/usr/local/bin/docker")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 1))
    from aidem.config.runtimes import DockerRuntime
    meta = {"runtime": "docker", "name": "t", "binary": "t", "image": "img:latest"}
    rt = DockerRuntime(meta, aidem_paths.env_dir("t"))
    with pytest.raises(RuntimeError, match="not installed"):
        rt.run(["scan"])


def test_docker_runtime_install_builds_from_dockerfile(fake_dirs, tmp_path, monkeypatch):
    _reload(fake_dirs)
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM alpine\n")
    monkeypatch.setattr("aidem.config.runtimes.docker.shutil.which", lambda x: "/usr/local/bin/docker")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0))
    from aidem.config.runtimes import DockerRuntime
    meta = {"runtime": "docker", "name": "t", "binary": "t", "_repo_path": str(repo)}
    rt = DockerRuntime(meta, aidem_paths.env_dir("t"))
    msg = rt.install("https://github.com/o/t")
    assert "built" in msg
    assert any("docker" in c and "build" in c for c in calls)


def test_binary_runtime_rejects_traversal_in_tarball(fake_dirs, monkeypatch):
    """A tarball member with ../ traversal must not escape the extraction dir."""
    _reload(fake_dirs)
    assets = [{"name": "bad.tar.gz", "browser_download_url": "https://github.com/o/r/releases/download/v1/bad.tar.gz"}]
    monkeypatch.setattr(
        "aidem.config.runtimes.binary.BinaryRuntime._latest_release_assets",
        lambda self, source: assets,
    )
    monkeypatch.setattr(
        "aidem.config.runtimes.binary.BinaryRuntime._verify_checksum_if_available",
        lambda self, a, asset, archive: None,
    )
    import io, tarfile
    def fake_download(self, url, dest):
        with tarfile.open(dest, "w:gz") as tf:
            # A legitimate binary inside the archive.
            data = b"#!/bin/sh\necho rtk\n"
            info = tarfile.TarInfo("rtk")
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
            # A traversal member that tries to escape.
            evil_data = b"evil!"
            evil = tarfile.TarInfo("../../escaped.txt")
            evil.size = len(evil_data)
            tf.addfile(evil, io.BytesIO(evil_data))
    monkeypatch.setattr("aidem.config.runtimes.binary.BinaryRuntime._download", fake_download)
    from aidem.config.runtimes import BinaryRuntime
    meta = {"runtime": "binary", "name": "rtk", "binary": "rtk", "source": "https://github.com/rtk-ai/rtk"}
    rt = BinaryRuntime(meta, aidem_paths.env_dir("rtk"))
    rt.install("https://github.com/rtk-ai/rtk")
    env = aidem_paths.env_dir("rtk")
    # The traversal file must NOT have escaped outside the env dir.
    assert not (env.parent.parent / "escaped.txt").exists()
    # The legitimate binary must be installed.
    assert (env / "bin" / "rtk").exists()


def test_binary_runtime_rejects_non_github_download_url(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    from aidem.config.runtimes.binary import BinaryRuntime
    meta = {"runtime": "binary", "name": "t", "binary": "t", "source": "https://github.com/o/t"}
    rt = BinaryRuntime(meta, aidem_paths.env_dir("t"))
    with pytest.raises(RuntimeError, match="non-GitHub"):
        rt._download("http://evil.example.com/binary", aidem_paths.env_dir("t") / "x")
    with pytest.raises(RuntimeError, match="non-GitHub"):
        rt._download("https://evil.example.com/binary", aidem_paths.env_dir("t") / "x")


def test_binary_runtime_checksum_mismatch_rejected(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    asset = {"name": "rtk.tar.gz", "browser_download_url": "https://github.com/o/r/releases/download/v1/rtk.tar.gz"}
    sha_asset = {"name": "checksums.txt", "browser_download_url": "https://github.com/o/r/releases/download/v1/checksums.txt"}
    assets = [asset, sha_asset]
    monkeypatch.setattr(
        "aidem.config.runtimes.binary.BinaryRuntime._latest_release_assets",
        lambda self, source: assets,
    )
    import io, tarfile
    def fake_download(self, url, dest):
        if "checksums" in url:
            Path(dest).write_text("0000000000000000000000000000000000000000000000000000000000000000  rtk.tar.gz\n")
        else:
            with tarfile.open(dest, "w:gz") as tf:
                data = b"#!/bin/sh\necho rtk\n"
                info = tarfile.TarInfo("rtk")
                info.size = len(data)
                info.mode = 0o755
                tf.addfile(info, io.BytesIO(data))
    monkeypatch.setattr("aidem.config.runtimes.binary.BinaryRuntime._download", fake_download)
    from aidem.config.runtimes import BinaryRuntime
    meta = {"runtime": "binary", "name": "rtk", "binary": "rtk", "source": "https://github.com/rtk-ai/rtk"}
    rt = BinaryRuntime(meta, aidem_paths.env_dir("rtk"))
    with pytest.raises(RuntimeError, match="checksum verification failed"):
        rt.install("https://github.com/rtk-ai/rtk")


def test_resolve_run_binary_prefers_env(fake_dirs):
    _reload(fake_dirs)
    aidem_paths.ensure_data_dirs()
    env_bin = aidem_paths.env_dir("rtk") / "bin" / "rtk"
    env_bin.parent.mkdir(parents=True, exist_ok=True)
    env_bin.write_text("#!/bin/sh\necho hi\n")
    resolved, migrated = aidem_cli._resolve_run_binary({"binary": "rtk"}, "rtk")
    assert resolved == str(env_bin)
    assert migrated is False


def test_resolve_run_binary_legacy_fallback(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    aidem_paths.ensure_data_dirs()
    # No env binary; simulate a legacy global PATH install.
    monkeypatch.setattr("aidem.cli.shutil.which", lambda b: f"/usr/local/bin/{b}")
    resolved, migrated = aidem_cli._resolve_run_binary({"binary": "rtk"}, "rtk")
    assert resolved == "/usr/local/bin/rtk"
    assert migrated is True


def test_resolve_run_binary_missing(fake_dirs, monkeypatch):
    _reload(fake_dirs)
    aidem_paths.ensure_data_dirs()
    monkeypatch.setattr("aidem.cli.shutil.which", lambda b: None)
    resolved, migrated = aidem_cli._resolve_run_binary({"binary": "ghost"}, "ghost")
    assert resolved is None
    assert migrated is False


def test_registry_add_uv_records_manifest_with_runtime(invoke, fake_dirs, monkeypatch, skill_repo):
    # Fake the install so it doesn't actually run uv venv.
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 0))
    res = invoke("registry", "add", str(skill_repo), "external-demo")
    assert res.exit_code == 0, res.output
    import json
    manifest = json.loads(fake_dirs["manifest"].read_text())
    assert manifest["external-demo"]["runtime"] in ("uv", "skills-only")


def test_run_uses_env_binary_not_global_path(invoke, fake_dirs, monkeypatch, skill_repo):
    # Register a uv tool with a binary, then fake the env binary and ensure run execs it.
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 0))
    import json
    # Build a manifest entry directly with a binary and an env binary on disk.
    aidem_paths.ensure_data_dirs()
    manifest = {
        "mytool": {
            "path": "skill/mytool", "binary": "mytool", "runtime": "uv",
            "source": "https://github.com/o/mytool", "kind": "skill",
        }
    }
    fake_dirs["manifest"].write_text(json.dumps(manifest))
    env_bin = aidem_paths.env_dir("mytool") / "bin" / "mytool"
    env_bin.parent.mkdir(parents=True, exist_ok=True)
    env_bin.write_text("#!/bin/sh\necho from-env\n")
    execved = {}
    def fake_execvp(cmd, args):
        execved["cmd"] = cmd
        raise SystemExit(0)
    monkeypatch.setattr("aidem.cli.os.execvp", fake_execvp)
    res = invoke("run", "mytool", "--help")
    assert env_bin.as_posix() in str(execved.get("cmd", "")) or execved.get("cmd") == str(env_bin)
