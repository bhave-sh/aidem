"""Tests for repo init (single AGENTS.md) and its cwd guard."""

from pathlib import Path


def test_init_explicit_path_writes_single_file(invoke, tmp_path):
    target = tmp_path / "myproj"
    res = invoke("init", "--template", "default", str(target))
    assert res.exit_code == 0, res.output
    assert (target / "AGENTS.md").exists()
    # Crucially, NOTHING else is written into the repo.
    assert [p.name for p in target.iterdir()] == ["AGENTS.md"]


def test_init_uses_overlay_template(invoke, tmp_path):
    target = tmp_path / "myproj"
    res = invoke("init", "--template", "web-app", str(target))
    assert res.exit_code == 0
    content = (target / "AGENTS.md").read_text()
    assert "web application" in content.lower()


def test_init_link_symlinks_to_canonical(invoke, tmp_path):
    target = tmp_path / "myproj"
    res = invoke("init", "--template", "python-cli", "--link", str(target))
    assert res.exit_code == 0
    agents = target / "AGENTS.md"
    assert agents.is_symlink()
    assert "python" in agents.resolve().name.lower() or "AGENTS" in agents.resolve().name


def test_init_cwd_prompts_and_aborts_on_n(invoke, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = invoke("init", input="n\n")
    assert "Aborted" in res.output
    assert not (tmp_path / "AGENTS.md").exists()


def test_init_cwd_prompts_and_proceeds_on_y(invoke, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = invoke("init", input="y\n")
    assert res.exit_code == 0
    assert (tmp_path / "AGENTS.md").exists()


def test_init_force_skips_prompt(invoke, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = invoke("init", "--force")
    assert res.exit_code == 0, res.output
    assert (tmp_path / "AGENTS.md").exists()


def test_init_refuses_inside_package_dir_by_default(invoke, monkeypatch):
    import aidem_paths
    monkeypatch.chdir(aidem_paths.PACKAGE_ROOT)
    res = invoke("init")
    assert "Refusing to write" in res.output
    assert not (aidem_paths.PACKAGE_ROOT / "AGENTS.md").exists()