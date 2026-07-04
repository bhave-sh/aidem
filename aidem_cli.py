#!/usr/bin/env python3
"""aidem: AI development environment manager.

Layers:
  0. Registry  — git clone skill/tool repos and (optionally) install binaries via uv.
  1. Bridging  — one-time dir symlinks from each IDE's skills dir into ~/.aidem/skills;
                 plus repo init (a single committed AGENTS.md).
  2. Execution — pass-through run of registered tools in isolated uv environments.

User data (skills, registry, manifest) lives in ~/.aidem (overridable via
AIDEM_DATA_DIR). Shipped package assets (generators, overlays, canonical
AGENTS.md) travel with the install and are read-only.
"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import click

from aidem_paths import (
    data_dir,
    registry_dir,
    manifest_path,
    skills_dir as user_skills_dir,
    overlays_dir,
    canonical_agents,
    ensure_data_dirs,
    PACKAGE_ROOT,
)

import aidem_paths  # noqa: F401  (re-exported for tests)

# Skill file names scanned for in a registered repo (checked in order).
SKILL_FILE_CANDIDATES = ("skill.md", "SKILL.md", "SKILL.MD")

# Runtime markers: filename -> (runtime, notes)
RUNTIME_MARKERS = {
    "pyproject.toml": ("uv", None),
    "package.json": ("npm", "npm-installed tools are not yet supported for `aidem run`"),
    "Cargo.toml": ("cargo", "cargo-installed tools are not yet supported for `aidem run`"),
    "go.mod": ("go", "go-installed tools are not yet supported for `aidem run`"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_manifest() -> dict:
    if manifest_path().exists():
        return json.loads(manifest_path().read_text())
    return {}


def save_manifest(manifest: dict) -> None:
    ensure_data_dirs()
    manifest_path().write_text(json.dumps(manifest, indent=2))


def _resolve_agents_source(template: str) -> Path:
    overlay = overlays_dir() / template / "AGENTS.md"
    if overlay.exists():
        return overlay
    return canonical_agents()


def _detect_runtime(tool_path: Path) -> str | None:
    for filename, (runtime, _notes) in RUNTIME_MARKERS.items():
        if (tool_path / filename).exists():
            return runtime
    return None


def _detect_binary(tool_path: Path) -> str | None:
    pyproject = tool_path / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
        scripts = data.get("project", {}).get("scripts", {})
        if scripts:
            return list(scripts.keys())[0]
    except Exception:
        pass
    return None


def _skill_source_for(repo_path: Path) -> Path | None:
    for candidate_name in SKILL_FILE_CANDIDATES:
        candidate = repo_path / candidate_name
        if candidate.exists():
            return candidate
    skills_subdir = repo_path / "skills"
    if skills_subdir.exists():
        if any(skills_subdir.glob("*.md")):
            return skills_subdir
        if any(skills_subdir.glob("**/SKILL.md")):
            return skills_subdir
    return None


def _add_shared_skill(name: str, source: Path) -> bool:
    skills = user_skills_dir()
    skills.mkdir(parents=True, exist_ok=True)

    old_flat = skills / f"{name}.md"
    if old_flat.exists() or old_flat.is_symlink():
        old_flat.unlink()
    for old in skills.glob(f"{name}_*.md"):
        if old.is_symlink() or old.exists():
            old.unlink()

    if source.is_dir():
        added = False
        md_files = sorted(source.glob("*.md"))
        if md_files:
            for f in md_files:
                skill_dir = skills / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                link = skill_dir / "SKILL.md"
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(f)
                added = True
        if not added:
            for f in sorted(source.glob("**/SKILL.md")):
                sub = f.parent.name
                skill_dir = skills / f"{name}_{sub}"
                skill_dir.mkdir(parents=True, exist_ok=True)
                link = skill_dir / "SKILL.md"
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(f)
                added = True
        return added

    skill_dir = skills / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    link = skill_dir / "SKILL.md"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(source)
    return True


def _remove_shared_skill(name: str) -> int:
    skills = user_skills_dir()
    removed = 0
    for f in list(skills.glob(f"{name}.md")) + list(skills.glob(f"{name}_*.md")):
        if f.is_symlink() or f.exists():
            f.unlink()
            removed += 1
    for d in [skills / name] + list(skills.glob(f"{name}_*")):
        if d.is_dir():
            shutil.rmtree(d)
            removed += 1
    return removed


def _regenerate_mirrors() -> int:
    from config.generators import regenerate_all_mirrors
    return regenerate_all_mirrors(PACKAGE_ROOT / "config")


def _refresh_skill_links() -> int:
    count = 0
    for name, meta in load_manifest().items():
        repo_path = _repo_abs_path(meta["path"])
        source = _skill_source_for(repo_path)
        if source is not None and _add_shared_skill(name, source):
            count += 1
    return count


def _repo_rel_path(abs_path: Path) -> str:
    """Store paths in the manifest relative to the registry root."""
    try:
        return str(abs_path.relative_to(registry_dir()))
    except ValueError:
        return str(abs_path)


def _repo_abs_path(stored: str) -> Path:
    p = Path(stored)
    if p.is_absolute():
        return p
    return registry_dir() / stored


def _clean_git_modules(rel_path: str) -> None:
    # Plain clones leave no .git/modules; kept for safety against legacy manifests.
    mod = PACKAGE_ROOT / ".git" / "modules" / rel_path
    if mod.exists():
        shutil.rmtree(mod)


# ---------------------------------------------------------------------------
# Top-level CLI
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """aidem: AI development environment manager."""
    pass


# ---------------------------------------------------------------------------
# Layer 0: Registry Management
# ---------------------------------------------------------------------------


@cli.group()
def registry():
    """Manage skill/tool registry (git clones + runtime installs)."""
    pass


@registry.command()
@click.argument("git_url")
@click.argument("name")
@click.option("--category", default="skills", help="Registry category directory.")
def add(git_url: str, name: str, category: str):
    """Clone a skill/tool repo into ~/.aidem/registry and register it."""
    ensure_data_dirs()
    category_dir = registry_dir() / category
    category_dir.mkdir(parents=True, exist_ok=True)
    target = category_dir / name

    if target.exists():
        click.echo(f"Error: {target} already exists.", err=True)
        sys.exit(1)

    try:
        subprocess.run(["git", "clone", git_url, str(target)], check=True)
    except subprocess.CalledProcessError as exc:
        click.echo(f"Failed to clone: {exc}", err=True)
        sys.exit(1)

    runtime = _detect_runtime(target)
    binary = None

    if runtime == "uv":
        binary = _detect_binary(target)
        if binary is None:
            if click.confirm(
                f"No [project.scripts] found in pyproject.toml. "
                f"Register '{name}' as a skills-only repo (no binary)?",
                default=True,
            ):
                binary = ""
            else:
                shutil.rmtree(target)
                click.echo("Aborted. No entry added to the registry.")
                sys.exit(1)
    elif runtime is None:
        click.echo(
            f"No runtime marker (pyproject.toml/package.json/Cargo.toml/go.mod) found. "
            f"Registering '{name}' as a skills-only repo."
        )
        binary = ""
    else:
        _, notes = RUNTIME_MARKERS[
            next(k for k in RUNTIME_MARKERS if (target / k).exists())
        ]
        install_choice = click.prompt(
            f"Detected runtime '{runtime}'. {notes}.\n"
            f"  1) Register as skills-only (no `aidem run`)\n"
            f"  2) Provide a binary name already on PATH to wrap\n"
            f"  3) Abort",
            type=click.Choice(["1", "2", "3"], case_sensitive=False),
            default="1",
        )
        if install_choice == "3":
            shutil.rmtree(target)
            click.echo("Aborted. No entry added to the registry.")
            sys.exit(1)
        elif install_choice == "2":
            binary = click.prompt("Binary name (must already exist on PATH)")
            if not shutil.which(binary):
                click.echo(
                    f"Warning: '{binary}' not currently on PATH. "
                    f"`aidem run {name}` will fail until it is installed."
                )
        else:
            binary = ""

    manifest = load_manifest()
    manifest[name] = {
        "path": _repo_rel_path(target),
        "binary": binary or "",
        "runtime": runtime or "skills-only",
        "source": git_url,
        "category": category,
    }
    save_manifest(manifest)

    if runtime == "uv" and binary:
        try:
            subprocess.run(
                ["uv", "tool", "install", "--editable", str(target)],
                cwd=PACKAGE_ROOT, check=True,
            )
        except subprocess.CalledProcessError as exc:
            click.echo(f"Added to registry but failed to install: {exc}", err=True)
            sys.exit(1)

    source = _skill_source_for(target)
    if source is not None:
        _add_shared_skill(name, source)
        mirrored = _regenerate_mirrors()
        click.echo(
            f"Linked skill '{name}' into {user_skills_dir()} "
            f"(mirrored to {mirrored} transform tool(s))."
        )

    kind = "skill" if not binary else "tool"
    click.echo(f"Added {kind} '{name}' ({category}). Run `aidem setup` to build tool bridges.")


@registry.command()
def setup():
    """Re-clone missing registered repos and install those with binaries."""
    ensure_data_dirs()
    manifest = load_manifest()
    for name, meta in manifest.items():
        tool_path = _repo_abs_path(meta["path"])
        if not tool_path.exists():
            try:
                subprocess.run(
                    ["git", "clone", meta["source"], str(tool_path)], check=True
                )
            except subprocess.CalledProcessError as exc:
                click.echo(f"Failed to clone {name}: {exc}", err=True)
                continue

        runtime = meta.get("runtime", "uv")
        pyproject = tool_path / "pyproject.toml"
        binary = meta.get("binary", "")
        if runtime == "uv" and binary and pyproject.exists():
            try:
                subprocess.run(
                    ["uv", "tool", "install", "--editable", str(tool_path)],
                    cwd=PACKAGE_ROOT, check=True,
                )
            except subprocess.CalledProcessError as exc:
                click.echo(f"Failed to install {name}: {exc}", err=True)
        elif not binary:
            click.echo(f"  {name}: skills-only repo, no binary to install.")

    # Reconcile skill links and mirrors.
    linked = _refresh_skill_links()
    mirrored = _regenerate_mirrors()
    click.echo(f"Registry setup complete ({linked} skill link(s), {mirrored} mirror file(s)).")


@registry.command()
def update():
    """Pull updates for all cloned registry repos."""
    manifest = load_manifest()
    if not manifest:
        click.echo("Nothing to update. Registry is empty.")
        return
    for name, meta in manifest.items():
        tool_path = _repo_abs_path(meta["path"])
        if not tool_path.exists():
            continue
        try:
            subprocess.run(["git", "pull", "--ff-only"], cwd=tool_path, check=True)
        except subprocess.CalledProcessError as exc:
            click.echo(f"Failed to update {name}: {exc}", err=True)
    mirrored = _regenerate_mirrors()
    click.echo("Registry updated. Run `aidem setup` to refresh skill links/mirrors.")


@registry.command(name="list")
def list_registry():
    """List registered skills/tools."""
    manifest = load_manifest()
    if not manifest:
        click.echo("No skills/tools registered. Use `aidem registry add <git-url> <name>`.")
        return
    click.echo("Registered skills/tools:")
    for name, meta in manifest.items():
        binary = meta.get("binary", "")
        category = meta.get("category", "skills")
        repo_path = _repo_abs_path(meta["path"])
        has_skill = any((repo_path / c).exists() for c in SKILL_FILE_CANDIDATES)
        has_skill = has_skill or (
            (repo_path / "skills").exists() and any((repo_path / "skills").glob("*.md"))
        )
        has_skill = has_skill or (
            (repo_path / "skills").exists() and any((repo_path / "skills").glob("**/SKILL.md"))
        )
        skill_mark = "+" if has_skill else " "
        runtime = meta.get("runtime", "uv")
        if not binary:
            kind = "skills-only"
        elif runtime != "uv":
            kind = f"{runtime} binary={binary}"
        else:
            installed = "✓" if shutil.which(binary) else "✗"
            kind = f"binary={binary} installed={installed}"
        click.echo(f"  {skill_mark} {name} ({category}) {kind}")


@registry.command()
@click.argument("name")
def install(name: str):
    """Install (or reinstall) a registered tool's binary."""
    manifest = load_manifest()
    if name not in manifest:
        click.echo(f"Error: '{name}' not found in registry.", err=True)
        sys.exit(1)
    meta = manifest[name]
    tool_path = _repo_abs_path(meta["path"])
    runtime = meta.get("runtime", "uv")
    pyproject = tool_path / "pyproject.toml"
    if not pyproject.exists() or not meta.get("binary"):
        click.echo(f"'{name}' is a skills-only repo (no binary to install).")
        return
    if runtime == "uv":
        subprocess.run(
            ["uv", "tool", "install", "--editable", str(tool_path)],
            cwd=PACKAGE_ROOT, check=True,
        )
    else:
        click.echo(f"Runtime '{runtime}' not yet supported.", err=True)
        sys.exit(1)
    click.echo(f"Installed '{name}'.")


@registry.command()
@click.argument("name")
def remove(name: str):
    """Unregister and remove a skill/tool."""
    manifest = load_manifest()
    if name not in manifest:
        click.echo(f"Error: '{name}' not found in registry.", err=True)
        sys.exit(1)
    meta = manifest[name]
    binary = meta.get("binary", name)

    if binary and shutil.which(binary):
        subprocess.run(["uv", "tool", "uninstall", binary], cwd=PACKAGE_ROOT, check=False)

    repo_path = _repo_abs_path(meta["path"])
    if repo_path.exists():
        shutil.rmtree(repo_path)

    _clean_git_modules(meta["path"])

    removed = _remove_shared_skill(name)
    if removed:
        _regenerate_mirrors()

    del manifest[name]
    save_manifest(manifest)
    msg = f"Removed '{name}'."
    if removed:
        msg += f" Removed {removed} skill file(s) from {user_skills_dir()}."
    click.echo(msg)


# ---------------------------------------------------------------------------
# Layer 1A: Centralized bridging  (aidem setup)
# ---------------------------------------------------------------------------


@cli.command()
def setup():
    """Build the one-time dir bridges from each IDE's skills folder into aidem.

    Each AI tool's global skills directory is symlinked ONCE to an aidem-internal
    staging directory, so every skill you add afterwards (via `aidem skill
    create` or `aidem registry add`) surfaces in every bridged tool with no
    further wiring:

      Kilo:     ~/.kilo/skills                -> ~/.aidem/skills
      Claude:   ~/.claude/skills              -> ~/.aidem/skills
      Cursor:   ~/.cursor/skills              -> ~/.aidem/skills
      OpenCode: ~/.config/opencode/skills     -> ~/.aidem/skills
      Windsurf: ~/.codeium/windsurf/skills    -> ~/.aidem/skills

    Also reconciles ~/.aidem/skills against the registry and regenerates
    transformed mirrors. Idempotent — safe to re-run. Refuses to clobber an
    existing real directory (it tells you how to proceed).
    Skips tools whose parent directory is not present (tool not installed).
    """
    from config.generators import ensure_all_bridges, shared_skills_dir

    ensure_data_dirs()
    linked = _refresh_skill_links()

    skills = shared_skills_dir(PACKAGE_ROOT / "config")
    if not any(skills.glob("*.md")) and linked == 0:
        click.echo(
            "No skills in ~/.aidem/skills. Create one with `aidem skill create <name>` "
            "or register a repo with `aidem registry add`."
        )

    results = ensure_all_bridges(PACKAGE_ROOT / "config")
    click.echo("Tool bridges:")
    for status, msg in results:
        click.echo(f"  [{status}] {msg}")

    mirrored = _regenerate_mirrors()
    click.echo(f"Refreshed skill library ({linked} link(s), {mirrored} mirror file(s)).")


# ---------------------------------------------------------------------------
# Layer 1B: Repo init  (aidem init)  — single AGENTS.md
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--template", default="default", help="Template/overlay name to apply.")
@click.option("--link/--copy", default=False, help="Symlink AGENTS.md to canonical (default: copy).")
@click.option("--force", is_flag=True, help="Skip the cwd confirmation prompt.")
@click.argument("project_path", default=".")
def init(template: str, link: bool, force: bool, project_path: str):
    """Write a single AGENTS.md into a repo (the portable team standard).

    aidem does NOT pollute the repo with per-tool config files. Tools that read
    AGENTS.md natively (Cursor, Copilot, Kilo) get it for free. Your personal
    skills are bridged globally via `aidem setup`, not committed per-repo.

    When PROJECT_PATH is omitted aidem defaults to the current directory. Writing
    into cwd (especially the aidem package dir) is easy to do by accident, so
    aidem asks for confirmation unless --force is given.
    """
    project = Path(project_path).resolve()

    defaulted = project_path in (".", "")
    if defaulted and not force:
        if project == PACKAGE_ROOT:
            click.echo(
                "Refusing to write AGENTS.md into the aidem package dir by default. "
                "Pass an explicit path, or use --force to proceed.",
                err=True,
            )
            sys.exit(1)
        if not click.confirm(
            f"Write AGENTS.md into the current directory ({project})?",
            default=False,
        ):
            click.echo("Aborted.")
            sys.exit(1)

    project.mkdir(parents=True, exist_ok=True)

    agents_source = _resolve_agents_source(template)
    if not agents_source.exists():
        click.echo(f"Error: template '{template}' not found.", err=True)
        sys.exit(1)

    target = project / "AGENTS.md"
    if target.exists() and not target.is_symlink():
        click.echo(f"Warning: {target} already exists; overwriting.", err=True)

    if target.exists() or target.is_symlink():
        target.unlink()

    if link:
        target.symlink_to(agents_source)
        mode = "link"
    else:
        shutil.copy2(agents_source, target)
        mode = "copy"

    click.echo(f"Wrote AGENTS.md to {project} (template '{template}', {mode}).")


# ---------------------------------------------------------------------------
# Layer 1C: Skill authoring  (aidem skill ...)
# ---------------------------------------------------------------------------


SKILL_TEMPLATE = """---
name: {name}
description: Describe what this skill does and when to use it.
---

# {name}

Instructions for the AI agent.
"""


@cli.group()
def skill():
    """Create and manage skills in aidem's central library (~/.aidem/skills)."""
    pass


@skill.command()
@click.argument("name")
@click.option("--body", "body", default=None,
              help="Skill body text (alternative to $EDITOR). Falls back to editor/paste.")
def create(name: str, body: str | None):
    """Create a new skill in ~/.aidem/skills/<name>/SKILL.md and refresh mirrors.

    Skills follow the Agent Skills spec (skills/<name>/SKILL.md with YAML
    frontmatter). By default this opens $EDITOR (or prompts you to paste);
    pass --body "..." to provide it non-interactively.
    """
    ensure_data_dirs()
    skills = user_skills_dir()
    skill_dir = skills / name
    target = skill_dir / "SKILL.md"

    if skill_dir.exists() and not click.confirm(
        f"{skill_dir} already exists. Overwrite?", default=False
    ):
        click.echo("Aborted.")
        return

    if body is None:
        edited = click.edit(SKILL_TEMPLATE.format(name=name))
        if not edited:
            click.echo("Aborted: no content provided.", err=True)
            sys.exit(1)
        body = edited

    if "name:" not in body.split("---")[1] if "---" in body else True:
        click.echo("Warning: missing 'name' in frontmatter. "
                   "Skill may not be recognized by all tools.", err=True)

    skill_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    mirrored = _regenerate_mirrors()
    click.echo(
        f"Created skill '{name}' at {target} "
        f"(mirrored to {mirrored} transform tool(s))."
    )


# ---------------------------------------------------------------------------
# Layer 2: Execution
# ---------------------------------------------------------------------------


@cli.command(
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
    add_help_option=False,
)
@click.argument("tool", required=False)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run(tool: str | None, args: tuple):
    """Run a registered tool in its isolated environment."""
    if not tool:
        click.echo("Usage: aidem run [OPTIONS] TOOL [ARGS]...")
        click.echo("")
        click.echo("  Run a registered tool in its isolated environment.")
        click.echo("")
        click.echo("Options:")
        click.echo("  -h, --help  Show this message and exit.")
        return

    manifest = load_manifest()
    if tool not in manifest:
        click.echo(
            f"Error: Tool '{tool}' not found in registry. Run `aidem registry list`.",
            err=True,
        )
        sys.exit(1)

    binary = manifest[tool]["binary"]
    if not binary:
        click.echo(
            f"Error: '{tool}' is a skills-only repo (no binary).",
            err=True,
        )
        sys.exit(1)
    if not shutil.which(binary):
        click.echo(
            f"Error: Binary '{binary}' not found. "
            f"Run `aidem registry setup` or `aidem registry install {tool}`.",
            err=True,
        )
        sys.exit(1)

    os.execvp(binary, [binary] + list(args))


if __name__ == "__main__":
    cli()