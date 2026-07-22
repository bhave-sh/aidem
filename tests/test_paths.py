"""Tests for aidem_paths path resolution and data-dir override."""

import importlib
import os
from pathlib import Path

from aidem import paths as aidem_paths


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
    for p in ("skills", "rules", "registry"):
        assert (tmp_path / "d" / p).is_dir()


def test_shipped_paths_understandable(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    # Overlays come from the package (read-only), not the data dir.
    assert aidem_paths.overlays_dir().name == "overlays"
    assert aidem_paths.canonical_agents().name == "AGENTS.md"
    # User-writable skills live under the data dir (plural container).
    assert aidem_paths.skills_dir() == tmp_path / "d" / "skills"


def test_kind_dir_plural_containers(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    assert aidem_paths.kind_dir("skill") == tmp_path / "d" / "skills"
    assert aidem_paths.kind_dir("rule") == tmp_path / "d" / "rules"


def test_migrate_singular_to_plural_containers(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    (tmp_path / "d").mkdir()
    legacy = tmp_path / "d" / "skill"
    legacy.mkdir()
    (legacy / "keep.md").write_text("mine")
    aidem_paths.ensure_data_dirs()
    assert not (tmp_path / "d" / "skill").exists()
    assert (tmp_path / "d" / "skills" / "keep.md").read_text() == "mine"


def test_migrate_merges_when_both_dirs_exist(monkeypatch, tmp_path, capsys):
    """Dual singular+plural dirs: old entries merge into new, no silent loss."""
    monkeypatch.setenv("AIDEM_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(aidem_paths)
    (tmp_path / "d").mkdir()
    old = tmp_path / "d" / "skill"
    new = tmp_path / "d" / "skills"
    old.mkdir()
    new.mkdir()
    (old / "only-in-old.md").write_text("old")
    (new / "only-in-new.md").write_text("new")
    (old / "conflict.md").write_text("old-ver")
    (new / "conflict.md").write_text("new-ver")
    aidem_paths.ensure_data_dirs()
    assert (new / "only-in-old.md").read_text() == "old"
    assert (new / "only-in-new.md").read_text() == "new"
    # Conflict: new wins; old kept in place with a warning printed.
    assert (new / "conflict.md").read_text() == "new-ver"
    assert (old / "conflict.md").read_text() == "old-ver"
    captured = capsys.readouterr()
    assert "conflict" in captured.out.lower()