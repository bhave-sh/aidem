"""Tests for registry add/remove/list and skill bridging on add."""

import subprocess
from pathlib import Path

import pytest


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture()
def skill_repo(tmp_path):
    """A throwaway local git repo exposing a skill.md at the repo root."""
    repo = tmp_path / "src-repo"
    repo.mkdir()
    (repo / "skill.md").write_text(
        "# Skill: external-demo\n## Purpose\nDemo external skill.\n"
    )
    _init_git_repo(repo)
    return repo


@pytest.fixture()
def nested_skill_repo(tmp_path):
    """A repo with a nested skills/<name>/SKILL.md and subdirectories like rules/."""
    repo = tmp_path / "nested-repo"
    skill_dir = repo / "skills" / "codeguard"
    rules_dir = skill_dir / "rules"
    rules_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: codeguard\n---\n# CodeGuard\nRefer to rules/ for rules.\n"
    )
    (rules_dir / "codeguard-1-hardcoded-credentials.md").write_text(
        "# Hardcoded Credentials\nDo not hardcode secrets.\n"
    )
    (rules_dir / "codeguard-0-input-validation-injection.md").write_text(
        "# Input Validation\nValidate all user input.\n"
    )
    _init_git_repo(repo)
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
    # Cloned into the data dir registry under kind=skill (the default).
    assert (fake_dirs["registry"] / "skill" / "external-demo" / "skill.md").exists()
    # Skill linked into shared library as a directory with SKILL.md inside.
    skill_dir = fake_dirs["skills"] / "external-demo"
    link = skill_dir / "SKILL.md"
    assert link.is_symlink()
    assert link.resolve().name == "skill.md"
    # Manifest records the entry with kind=skill.
    import json
    manifest = json.loads(fake_dirs["manifest"].read_text())
    assert "external-demo" in manifest
    assert manifest["external-demo"]["binary"] == ""
    assert manifest["external-demo"]["runtime"] == "skills-only"
    assert manifest["external-demo"]["kind"] == "skill"


def test_registry_add_nested_skill_symlinks_subdirs(invoke, fake_dirs, nested_skill_repo):
    res = invoke("registry", "add", str(nested_skill_repo), "project-codeguard")
    assert res.exit_code == 0, res.output
    skill_dir = fake_dirs["skills"] / "project-codeguard_codeguard"
    assert skill_dir.is_dir()
    sk_link = skill_dir / "SKILL.md"
    assert sk_link.is_symlink()
    assert sk_link.resolve().name == "SKILL.md"
    # rules/ subdirectory must be symlinked.
    rules_link = skill_dir / "rules"
    assert rules_link.is_symlink()
    # The individual rule files must be accessible through the symlink.
    assert (rules_link / "codeguard-1-hardcoded-credentials.md").exists()
    assert (rules_link / "codeguard-0-input-validation-injection.md").exists()


def test_registry_list_shows_skill(invoke, skill_repo):
    invoke("registry", "add", str(skill_repo), "external-demo")
    res = invoke("registry", "list")
    assert res.exit_code == 0
    assert "external-demo" in res.output
    assert "kind=skill" in res.output


def test_registry_remove_unlinks_skill_and_clone(invoke, fake_dirs, skill_repo):
    invoke("registry", "add", str(skill_repo), "external-demo")
    res = invoke("registry", "remove", "external-demo")
    assert res.exit_code == 0, res.output
    assert "Removed" in res.output
    # Clone gone.
    assert not (fake_dirs["registry"] / "skill" / "external-demo").exists()
    # Skill link gone from the shared library.
    assert not (fake_dirs["skills"] / "external-demo").exists()
    # Cursor mirror pruned.
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