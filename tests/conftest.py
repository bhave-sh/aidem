"""Shared pytest fixtures.

Every test runs against a throwaway data dir and a throwaway HOME, so the
real ~/.aidem and the real IDE dot-folders are never touched. aidem resolves
its data dir from the AIDEM_DATA_DIR env var (see aidem_paths.data_dir).
"""

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture()
def fake_dirs(tmp_path, monkeypatch):
    """Point aidem's data dir and HOME at tmp_path; reimport path module.

    Returns a SimpleNamespace-ish dict with the resolved dirs.
    """
    data = tmp_path / "aidem-data"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("AIDEM_DATA_DIR", str(data))
    monkeypatch.setenv("HOME", str(home))

    # Reimport aidem_paths so module-level resolution picks up the env var.
    import aidem_paths
    importlib.reload(aidem_paths)
    import aidem_cli
    importlib.reload(aidem_cli)

    return {
        "data": data,
        "home": home,
        "skills": data / "skill",
        "registry": data / "registry",
        "manifest": data / "registry" / "manifest.json",
    }


@pytest.fixture()
def invoke(fake_dirs):
    """Click CLI runner pre-wired to the fake dirs."""
    from click.testing import CliRunner
    import aidem_cli

    runner = CliRunner()

    def _run(*args, **kwargs):
        return runner.invoke(aidem_cli.cli, list(args), **kwargs)

    return _run