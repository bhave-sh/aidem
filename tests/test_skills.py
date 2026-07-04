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
    # No skills yet: setup still builds (empty) bridges.
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    assert (home / ".config" / "kilo" / "skills").is_symlink()
    assert (home / ".claude" / "commands").is_symlink()
    assert (home / ".cursor" / "rules").is_symlink()
    # Bridges point into the fake data dir, not the real package or real home.
    assert (home / ".config" / "kilo" / "skills").resolve() == fake_dirs["skills"]
    assert (home / ".cursor" / "rules").resolve() == fake_dirs["cursor_skills"]


def test_setup_idempotent(invoke):
    res1 = invoke("setup")
    res2 = invoke("setup")
    assert res1.exit_code == 0 and res2.exit_code == 0
    assert "already in place" in res2.output


def test_setup_refuses_to_clobber_real_dir(invoke, fake_dirs):
    home = fake_dirs["home"]
    real = home / ".config" / "kilo" / "skills"
    real.mkdir(parents=True)
    (real / "user-rule.md").write_text("mine")
    res = invoke("setup")
    assert res.exit_code == 0
    assert "exists and is not a symlink" in res.output
    # The real directory and its content must survive untouched.
    assert (real / "user-rule.md").read_text() == "mine"


def test_skill_create_writes_and_mirrors(invoke, fake_dirs):
    res = invoke("skill", "create", "demo-review", "--body", SKILL_BODY)
    assert res.exit_code == 0, res.output
    skill_file = fake_dirs["skills"] / "demo-review.md"
    assert skill_file.exists()
    assert skill_file.read_text().lstrip().startswith("# Skill: demo-review")
    # Cursor mirror regenerated with frontmatter.
    mirror = fake_dirs["cursor_skills"] / "demo-review.mdc"
    assert mirror.exists()
    content = mirror.read_text()
    assert content.startswith("---")
    assert "description: demo-review skill bridged from aidem" in content


def test_skill_visible_through_bridges(invoke, fake_dirs):
    home = fake_dirs["home"]
    invoke("skill", "create", "demo-review", "--body", SKILL_BODY)
    invoke("setup")
    # Kilo & Claude (passthrough) see the same .md via the dir symlink.
    assert (home / ".config" / "kilo" / "skills" / "demo-review.md").exists()
    assert (home / ".claude" / "commands" / "demo-review.md").exists()
    # Cursor sees the .mdc mirror via its dir symlink.
    assert (home / ".cursor" / "rules" / "demo-review.mdc").exists()


def test_skill_create_warns_on_wrong_format(invoke, fake_dirs):
    res = invoke("skill", "create", "bad", "--body", "no heading here")
    assert res.exit_code == 0
    assert "does not start with '# Skill:'" in res.output


def test_skill_create_overwrite_prompt(invoke, fake_dirs):
    invoke("skill", "create", "demo-review", "--body", SKILL_BODY)
    # Refuse overwrite via stdin "n".
    res = invoke("skill", "create", "demo-review", "--body", SKILL_BODY, input="n\n")
    assert "Aborted" in res.output


def test_setup_help_lists_commands(invoke):
    res = invoke("--help")
    assert res.exit_code == 0
    for cmd in ("init", "setup", "run", "skill", "registry"):
        assert cmd in res.output