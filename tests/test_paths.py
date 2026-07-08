"""Tests for aidem_paths path resolution and data-dir override."""

import importlib
import os
from pathlib import Path

import aidem_paths


def test_data_dir_default_to_dotaidem(monkeypatch, tmp_path):
    monkeypatch.delenv("AIDEM_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(aidem_paths)
    assert aidem_paths.data_dir() == tmp_path / ".aidem"


def test_data_dir_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom-aidem"
    monkeypatch.setenv("AIDEM_DATA_DIR", str(custom))
    importlib.reload(aidem_paths)
    assert aidem_paths.data_dir() == custom


def test_ensure_data_dirs_creates_all(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    aidem_paths.ensure_data_dirs()
    for p in ("skill", "registry"):
        assert (tmp_path / "d" / p).is_dir()


def test_shipped_paths_understandable(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    # Overlays come from the package (read-only), not the data dir.
    assert aidem_paths.overlays_dir().name == "overlays"
    assert aidem_paths.canonical_agents().name == "AGENTS.md"
    # User-writable skills live under the data dir.
    assert aidem_paths.skills_dir() == tmp_path / "d" / "skill"