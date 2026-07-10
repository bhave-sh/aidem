"""Tests for rule authoring, bridging, and registry integration."""

import subprocess
from pathlib import Path

import pytest


RULE_BODY = "# Rule: no-emoji\nDo not use emojis in code or docs.\n"


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture(autouse=True)
def _allow_file_transport(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1", prepend=False)
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow", prepend=False)
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always", prepend=False)


@pytest.fixture()
def rule_repo(tmp_path):
    repo = tmp_path / "rule-src"
    repo.mkdir()
    (repo / "rule.md").write_text("# Rule: ext-rule\nAlways do X.\n")
    _init_git_repo(repo)
    return repo


@pytest.fixture()
def multi_rule_repo(tmp_path):
    repo = tmp_path / "multi-rule-src"
    rules = repo / "rules"
    rules.mkdir(parents=True)
    (rules / "style.md").write_text("# Style\nUse 2 spaces.\n")
    (rules / "tests.md").write_text("# Tests\nWrite tests.\n")
    _init_git_repo(repo)
    return repo


def test_create_rule_writes_flat_file(invoke, fake_dirs):
    res = invoke("create", "no-emoji", "--rule", "--body", RULE_BODY)
    assert res.exit_code == 0, res.output
    rule_file = fake_dirs["rules"] / "no-emoji.md"
    assert rule_file.exists()
    assert rule_file.is_file()
    assert rule_file.read_text().lstrip().startswith("# Rule: no-emoji")
    assert "Created rule" in res.output


def test_create_default_kind_is_skill(invoke, fake_dirs):
    res = invoke("create", "demo", "--body", "# Skill: demo\n## Purpose\nDemo.\n")
    assert res.exit_code == 0, res.output
    assert (fake_dirs["skills"] / "demo" / "SKILL.md").exists()
    assert not (fake_dirs["rules"] / "demo.md").exists()


def test_rule_claude_bridge_passthrough(invoke, fake_dirs):
    home = fake_dirs["home"]
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    invoke("create", "no-emoji", "--rule", "--body", RULE_BODY)
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    bridge = home / ".claude" / "rules"
    assert bridge.is_symlink()
    assert bridge.resolve() == fake_dirs["rules"]
    assert (bridge / "no-emoji.md").exists()


def test_rule_kilo_bridge_config_array(invoke, fake_dirs):
    home = fake_dirs["home"]
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    config_path = home / ".config" / "kilo" / "kilo.jsonc"
    assert config_path.exists()
    import json
    config = json.loads(config_path.read_text())
    glob = str(fake_dirs["rules"]) + "/*.md"
    assert glob in config.get("instructions", [])

    # Idempotent: a second setup must not duplicate the entry.
    invoke("setup")
    config = json.loads(config_path.read_text())
    assert config.get("instructions", []).count(glob) == 1


def test_rule_opencode_bridge_config_array(invoke, fake_dirs):
    home = fake_dirs["home"]
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    config_path = home / ".config" / "opencode" / "opencode.json"
    assert config_path.exists()
    import json
    config = json.loads(config_path.read_text())
    glob = str(fake_dirs["rules"]) + "/*.md"
    assert glob in config.get("instructions", [])


def test_rule_windsurf_concat_mirror(invoke, fake_dirs):
    home = fake_dirs["home"]
    invoke("create", "style", "--rule", "--body", "# Rule: style\nUse 2 spaces.\n")
    invoke("create", "tests", "--rule", "--body", "# Rule: tests\nWrite tests.\n")
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    target = home / ".codeium" / "windsurf" / "memories" / "global_rules.md"
    assert target.exists()
    content = target.read_text()
    assert "Use 2 spaces." in content
    assert "Write tests." in content


def test_rule_windsurf_6k_warning(invoke, fake_dirs):
    big = "# Rule: big\n" + ("x" * 7000) + "\n"
    res = invoke("create", "big", "--rule", "--body", big)
    assert res.exit_code == 0, res.output
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    assert "[warn]" in res.output
    assert "6000" in res.output


def test_rule_kilo_bridge_preserves_commented_jsonc(invoke, fake_dirs):
    """A commented kilo.jsonc must NOT be clobbered by the rules bridge.

    The user's existing config keys must survive. (Comments are stripped on the
    rewritten file since json.dumps produces plain JSON — the critical invariant
    is that keys/values are preserved, not lost to a parse-failure clobber.)
    """
    home = fake_dirs["home"]
    config_path = home / ".config" / "kilo" / "kilo.jsonc"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{\n  // my model config\n  "model": "claude",\n  "providers": {}\n}\n'
    )
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    content = config_path.read_text()
    # The user's existing keys must survive (the data-loss bug would wipe these).
    assert '"model": "claude"' in content
    assert '"providers"' in content
    # And the rules entry must have been added (JSONC parsed tolerantly).
    assert "instructions" in content


def test_rule_kilo_bridge_warns_on_unparseable_config(invoke, fake_dirs):
    """A genuinely broken kilo.jsonc must be skipped, not clobbered."""
    home = fake_dirs["home"]
    config_path = home / ".config" / "kilo" / "kilo.jsonc"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{ this is not valid json at all }}}")
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    assert "unreadable" in res.output or "refusing" in res.output
    # The broken file must be left untouched.
    assert config_path.read_text() == "{ this is not valid json at all }}}"


def test_rule_opencode_bridge_preserves_commented_config(invoke, fake_dirs):
    home = fake_dirs["home"]
    config_path = home / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{\n  // shared team rules\n  "$schema": "https://opencode.ai/config.json"\n}\n'
    )
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    content = config_path.read_text()
    assert "$schema" in content
    assert "instructions" in content


def test_rule_cursor_and_github_skipped(invoke):
    res = invoke("setup")
    assert res.exit_code == 0, res.output
    assert "cursor: no global rules path" in res.output
    assert "github: no global rules path" in res.output


def test_registry_add_kind_rule_links_flat_file(invoke, fake_dirs, rule_repo):
    res = invoke("registry", "add", str(rule_repo), "ext-rule", "--kind", "rule")
    assert res.exit_code == 0, res.output
    link = fake_dirs["rules"] / "ext-rule.md"
    assert link.is_symlink()
    assert link.resolve().name == "rule.md"
    import json
    manifest = json.loads(fake_dirs["manifest"].read_text())
    assert manifest["ext-rule"]["kind"] == "rule"


def test_registry_add_kind_rule_multi_file(invoke, fake_dirs, multi_rule_repo):
    res = invoke("registry", "add", str(multi_rule_repo), "team", "--kind", "rule")
    assert res.exit_code == 0, res.output
    assert (fake_dirs["rules"] / "team_style.md").is_symlink()
    assert (fake_dirs["rules"] / "team_tests.md").is_symlink()


def test_registry_remove_kind_rule(invoke, fake_dirs, rule_repo):
    invoke("registry", "add", str(rule_repo), "ext-rule", "--kind", "rule")
    res = invoke("registry", "remove", "ext-rule")
    assert res.exit_code == 0, res.output
    assert not (fake_dirs["rules"] / "ext-rule.md").exists()
    import json
    assert "ext-rule" not in json.loads(fake_dirs["manifest"].read_text())
