"""Tests for registry add/remove/list and skill bridging on add."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def skill_repo(tmp_path):
    """A throwaway local git repo exposing a skill.md."""
    repo = tmp_path / "src-repo"
    repo.mkdir()
    (repo / "skill.md").write_text(
        "# Skill: external-demo\n## Purpose\nDemo external skill.\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "init"], cwd=repo, check=True
    )
    return repo


@pytest.fixture(autouse=True)
def _allow_file_transport(monkeypatch):
    # git clones local file paths only when file transport is allowed.
    monkeypatch.setenv(
        "GIT_CONFIG_COUNT", "1", prepend=False
    )
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow", prepend=False)
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always", prepend=False)


def test_registry_add_skill_repo_bridges_skill(invoke, fake_dirs, skill_repo):
    res = invoke("registry", "add", str(skill_repo), "external-demo")
    assert res.exit_code == 0, res.output
    # Cloned into the data dir registry.
    assert (fake_dirs["registry"] / "skills" / "external-demo" / "skill.md").exists()
    # Skill linked into the shared skills library.
    link = fake_dirs["skills"] / "external-demo.md"
    assert link.is_symlink()
    assert link.resolve().name == "skill.md"
    # Cursor mirror regenerated.
    assert (fake_dirs["cursor_skills"] / "external-demo.mdc").exists()
    # Manifest records the entry.
    import json
    manifest = json.loads(fake_dirs["manifest"].read_text())
    assert "external-demo" in manifest
    assert manifest["external-demo"]["binary"] == ""
    assert manifest["external-demo"]["runtime"] == "skills-only"


def test_registry_list_shows_skill(invoke, skill_repo):
    invoke("registry", "add", str(skill_repo), "external-demo")
    res = invoke("registry", "list")
    assert res.exit_code == 0
    assert "external-demo" in res.output
    assert "skills-only" in res.output


def test_registry_remove_unlinks_skill_and_clone(invoke, fake_dirs, skill_repo):
    invoke("registry", "add", str(skill_repo), "external-demo")
    res = invoke("registry", "remove", "external-demo")
    assert res.exit_code == 0, res.output
    assert "Removed" in res.output
    # Clone gone.
    assert not (fake_dirs["registry"] / "skills" / "external-demo").exists()
    # Skill link gone from the shared library.
    assert not (fake_dirs["skills"] / "external-demo.md").exists()
    # Cursor mirror pruned.
    assert not (fake_dirs["cursor_skills"] / "external-demo.mdc").exists()
    # Manifest entry gone.
    import json
    manifest = json.loads(fake_dirs["manifest"].read_text())
    assert "external-demo" not in manifest


def test_registry_list_empty(invoke):
    res = invoke("registry", "list")
    assert res.exit_code == 0
    assert "No skills/tools registered" in res.output


def test_run_missing_tool_errors(invoke):
    res = invoke("run", "no-such-tool", "--help")
    assert res.exit_code != 0
    assert "not found in registry" in res.output


def test_run_skills_only_repos_error(invoke, fake_dirs, skill_repo):
    invoke("registry", "add", str(skill_repo), "external-demo")
    res = invoke("run", "external-demo")
    assert res.exit_code != 0
    assert "skills-only repo" in res.output