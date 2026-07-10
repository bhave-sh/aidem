"""Tests for skill authoring and centralized bridging."""

import os
from pathlib import Path


SKILL_BODY = """# Skill: demo-review
## Purpose
Review code for correctness.
## When to Apply
During PR review.
"""


def test_setup_creates_dir_bridges(invoke, fake_dirs):
    home = fake_dirs["home"]
    # Create parent dirs so symlink bridges can be established.
    (home / ".kilo").mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".cursor").mkdir(parents=True, exist_ok=True)
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    # Bridges point into the fake data dir, not the real package or real home.
    assert (home / ".kilo" / "skills").is_symlink()
    assert (home / ".kilo" / "skills").resolve() == fake_dirs["skills"]
    assert (home / ".claude" / "skills").is_symlink()
    assert (home / ".claude" / "skills").resolve() == fake_dirs["skills"]
    assert (home / ".cursor" / "skills").is_symlink()
    assert (home / ".cursor" / "skills").resolve() == fake_dirs["skills"]


def test_setup_idempotent(invoke):
    res1 = invoke("setup")
    res2 = invoke("setup")
    assert res1.exit_code == 0 and res2.exit_code == 0
    assert "bridge already in place" in res2.output or "already contains" in res2.output


def test_setup_refuses_to_clobber_real_dir(invoke, fake_dirs):
    home = fake_dirs["home"]
    real = home / ".kilo" / "skills"
    real.mkdir(parents=True)
    (real / "user-rule.md").write_text("mine")
    res = invoke("setup")
    assert res.exit_code == 0
    # The real directory and its content must survive untouched.
    assert (real / "user-rule.md").read_text() == "mine"
    # Kilo falls back to kilo.jsonc when it can't symlink.
    assert "skills.paths" in res.output


def test_skill_create_writes_and_mirrors(invoke, fake_dirs):
    res = invoke("create", "demo-review", "--skill", "--body", SKILL_BODY)
    assert res.exit_code == 0, res.output
    skill_file = fake_dirs["skills"] / "demo-review" / "SKILL.md"
    assert skill_file.exists()
    assert skill_file.read_text().lstrip().startswith("# Skill: demo-review")
    # Cursor is passthrough now — no separate .mdc mirror.
    assert "mirrored to 0 transform tool(s)" in res.output


def test_skill_visible_through_bridges(invoke, fake_dirs):
    home = fake_dirs["home"]
    (home / ".kilo").mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".cursor").mkdir(parents=True, exist_ok=True)
    invoke("create", "demo-review", "--skill", "--body", SKILL_BODY)
    invoke("setup")
    # Passthrough tools see the directory-based skill via the dir symlink.
    assert (home / ".kilo" / "skills" / "demo-review" / "SKILL.md").exists()
    assert (home / ".claude" / "skills" / "demo-review" / "SKILL.md").exists()
    assert (home / ".cursor" / "skills" / "demo-review" / "SKILL.md").exists()


def test_skill_create_warns_on_wrong_format(invoke, fake_dirs):
    res = invoke("create", "bad", "--skill", "--body", "no heading here")
    assert res.exit_code == 0
    assert "missing 'name' in frontmatter" in res.output


def test_skill_create_overwrite_prompt(invoke, fake_dirs):
    invoke("create", "demo-review", "--skill", "--body", SKILL_BODY)
    # Refuse overwrite via stdin "n".
    res = invoke("create", "demo-review", "--skill", "--body", SKILL_BODY, input="n\n")
    assert "Aborted" in res.output


def test_setup_help_lists_commands(invoke):
    res = invoke("--help")
    assert res.exit_code == 0
    for cmd in ("init", "setup", "run", "create", "registry"):
        assert cmd in res.output