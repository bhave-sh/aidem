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
    env_dir,
    manifest_path,
    skills_dir as user_skills_dir,
    rules_dir as user_rules_dir,
    kind_dir,
    REGISTRY_KINDS,
    overlays_dir,
    canonical_agents,
    ensure_data_dirs,
    PACKAGE_ROOT,
)

import aidem_paths  # noqa: F401  (re-exported for tests)

# Skill file names scanned for in a registered repo (checked in order).
SKILL_FILE_CANDIDATES = ("skill.md", "SKILL.md", "SKILL.MD")

# Rule file names scanned for in a registered rule repo (checked in order).
RULE_FILE_CANDIDATES = ("rule.md", "RULE.md")

# Runtime kinds and deferred set come from a single source of truth in
# config.runtimes to avoid drift between the CLI and the adapters.
from config.runtimes import SUPPORTED_RUNTIMES as RUNTIME_KINDS
from config.runtimes import DEFERRED_RUNTIMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str) -> str:
    """Reject registry names that could escape the data directory."""
    if not name:
        raise click.BadParameter("name must not be empty")
    if ".." in name or "/" in name or "\\" in name:
        raise click.BadParameter(
            "name must not contain path separators or '..'"
        )
    return name


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
    """Detect a default runtime kind from file markers in the cloned repo.

    Delegates to config.runtimes (file-presence detection, not README parsing).
    Returns a supported kind ('uv'/'binary'/'docker'), a deferred kind
    ('npm'/'cargo'/'go'), or None (no marker / skills-only).
    """
    from config.runtimes import detect_runtime
    return detect_runtime(tool_path)


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


def _content_source_for(repo_path: Path, kind: str) -> Path | None:
    """Find the canonical content source for a registered repo of the given kind."""
    if kind == "rule":
        for candidate_name in RULE_FILE_CANDIDATES:
            candidate = repo_path / candidate_name
            if candidate.exists():
                return candidate
        rules_subdir = repo_path / "rules"
        if rules_subdir.exists() and any(rules_subdir.glob("*.md")):
            return rules_subdir
        return None
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


def _symlink_sibling_dirs(skill_dir: Path, source_parent: Path) -> None:
    for item in source_parent.iterdir():
        if not item.is_dir() or item.name == ".git":
            continue
        dest = skill_dir / item.name
        if dest.exists() or dest.is_symlink():
            if dest.is_symlink():
                dest.unlink()
            elif dest.is_dir():
                shutil.rmtree(dest)
        dest.symlink_to(item)


def _add_shared_skill(name: str, source: Path, target_dir: Path | None = None) -> bool:
    if target_dir is None:
        target_dir = user_skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    old_flat = target_dir / f"{name}.md"
    if old_flat.exists() or old_flat.is_symlink():
        old_flat.unlink()
    for old in target_dir.glob(f"{name}_*.md"):
        if old.is_symlink() or old.exists():
            old.unlink()

    if source.is_dir():
        added = False
        md_files = sorted(source.glob("*.md"))
        if md_files:
            for f in md_files:
                skill_dir = target_dir / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                link = skill_dir / "SKILL.md"
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(f)
                _symlink_sibling_dirs(skill_dir, source)
                added = True
        if not added:
            for f in sorted(source.glob("**/SKILL.md")):
                sub = f.parent.name
                skill_dir = target_dir / f"{name}_{sub}"
                skill_dir.mkdir(parents=True, exist_ok=True)
                link = skill_dir / "SKILL.md"
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(f)
                _symlink_sibling_dirs(skill_dir, f.parent)
                added = True
        return added

    skill_dir = target_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    link = skill_dir / "SKILL.md"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(source)
    _symlink_sibling_dirs(skill_dir, source.parent)
    return True


def _add_shared_rule(name: str, source: Path, target_dir: Path) -> bool:
    """Link a rule source (file or rules/ dir) into the shared rules library.

    Rules are flat: one file per rule at ~/.aidem/rules/<name>.md (or
    <name>_<stem>.md when a repo contributes multiple rule files).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for old in [target_dir / f"{name}.md"] + list(target_dir.glob(f"{name}_*.md")):
        if old.is_symlink() or old.exists():
            old.unlink()

    if source.is_dir():
        added = False
        for f in sorted(source.glob("*.md")):
            stem = f.stem
            link_name = f"{name}.md" if stem.lower() == name.lower() else f"{name}_{stem}.md"
            link = target_dir / link_name
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(f)
            added = True
        return added

    link = target_dir / f"{name}.md"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(source)
    return True


def _add_shared_content(name: str, source: Path, kind: str) -> bool:
    target = kind_dir(kind)
    if kind == "rule":
        return _add_shared_rule(name, source, target)
    return _add_shared_skill(name, source, target)


def _remove_shared_skill(name: str, target_dir: Path | None = None) -> int:
    if target_dir is None:
        target_dir = user_skills_dir()
    removed = 0
    for f in list(target_dir.glob(f"{name}.md")) + list(target_dir.glob(f"{name}_*.md")):
        if f.is_symlink() or f.exists():
            f.unlink()
            removed += 1
    for d in [target_dir / name] + list(target_dir.glob(f"{name}_*")):
        if d.is_dir():
            shutil.rmtree(d)
            removed += 1
    return removed


def _remove_shared_rule(name: str, target_dir: Path) -> int:
    removed = 0
    for f in [target_dir / f"{name}.md"] + list(target_dir.glob(f"{name}_*.md")):
        if f.is_symlink() or f.exists():
            f.unlink()
            removed += 1
    return removed


def _remove_shared_content(name: str, kind: str) -> int:
    target = kind_dir(kind)
    if kind == "rule":
        return _remove_shared_rule(name, target)
    return _remove_shared_skill(name, target)


def _regenerate_mirrors() -> int:
    from config.generators import regenerate_all_mirrors
    return regenerate_all_mirrors(PACKAGE_ROOT / "config")


def _regenerate_rule_mirrors() -> int:
    from config.generators import regenerate_all_rule_mirrors
    return regenerate_all_rule_mirrors(PACKAGE_ROOT / "config")


def _refresh_content_links() -> int:
    count = 0
    for name, meta in load_manifest().items():
        repo_path = _repo_abs_path(meta["path"])
        source = _content_source_for(repo_path, meta.get("kind") or meta.get("category", "skill"))
        kd = meta.get("kind") or meta.get("category", "skill")
        if source is not None and _add_shared_content(name, source, kd):
            count += 1
    return count


def _repo_rel_path(abs_path: Path) -> str:
    """Store paths in the manifest relative to the registry root."""
    reg = registry_dir().resolve()
    resolved = abs_path.resolve()
    try:
        return str(resolved.relative_to(reg))
    except ValueError:
        raise ValueError(
            f"path '{abs_path}' is outside the registry directory ({reg})"
        )


def _repo_abs_path(stored: str) -> Path:
    p = Path(stored)
    resolved = p.resolve() if p.is_absolute() else (registry_dir() / stored).resolve()
    reg = registry_dir().resolve()
    try:
        resolved.relative_to(reg)
    except ValueError:
        raise ValueError(
            f"manifest entry path '{stored}' escapes the registry directory ({reg})"
        )
    return resolved


def _clean_git_modules(rel_path: str) -> None:
    # Plain clones leave no .git/modules; kept for safety against legacy manifests.
    mod = PACKAGE_ROOT / ".git" / "modules" / rel_path
    if mod.exists():
        shutil.rmtree(mod)


def _runtime_for(meta: dict, name: str) -> "object":
    """Construct the runtime adapter for a manifest entry (env-owned)."""
    from config.runtimes import runtime_for
    enriched = dict(meta)
    enriched.setdefault("name", name)
    enriched.setdefault("binary", meta.get("binary", name))
    return runtime_for(enriched)


def _resolve_run_binary(meta: dict, name: str) -> tuple[str | None, bool]:
    """Resolve the binary path for `aidem run`.

    Returns (path_or_None, migrated). Prefers the aidem-owned env
    (~/.aidem/envs/<name>/bin/<binary>); falls back to a legacy global-PATH
    install (uv tool) and flags it for one-time migration nudges. Never writes
    to or depends on ~/.local/bin going forward.
    """
    binary = meta.get("binary", "")
    if not binary:
        return None, False
    env_bin = env_dir(name) / "bin" / binary
    if env_bin.exists():
        return str(env_bin), False
    # Legacy fallback: an entry installed via the old `uv tool` path lives on
    # global PATH. Resolve it so existing installs keep working, but flag it so
    # aidem can nudge the user toward `aidem registry install <name>`.
    legacy = shutil.which(binary)
    return (legacy if legacy else None), bool(legacy)


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
@click.option("--kind", default="skill", type=click.Choice(REGISTRY_KINDS, case_sensitive=False),
              help="Content kind: skill, rule, mcp, memory, or plan.")
@click.option("--runtime", "runtime_opt", default=None,
              type=click.Choice(RUNTIME_KINDS + DEFERRED_RUNTIMES, case_sensitive=False),
              help="Execution runtime: uv (default for Python), binary (prebuilt release "
                   "asset), docker (sandboxed container). Override the auto-detected default.")
@click.option("--spec", "spec", default=None,
              help="uv runtime: PyPI install spec (e.g. 'headroom-ai[all]'). Defaults to the "
                   "clone's package name; prefer a published wheel over an editable build.")
@click.option("--extras", "extras", default=None,
              help="uv runtime: optional-dependency group(s) to install (e.g. 'all', 'proxy'). "
                   "Defaults to 'all' if the clone declares it.")
@click.option("--asset", "asset", default=None,
              help="binary runtime: release-asset name substring/glob to pick (e.g. "
                   "'rtk-aarch64-apple-darwin'). Defaults to a platform-matching heuristic.")
@click.option("--image", "image", default=None,
              help="docker runtime: published image ref to pull (e.g. 'ghcr.io/org/tool:latest'). "
                   "Defaults to building the clone's Dockerfile as aidem/<name>.")
@click.option("--no-install", is_flag=True,
              help="Register the repo without installing the binary now. Install later with "
                   "`aidem registry install <name>`.")
def add(git_url: str, name: str, kind: str, runtime_opt: str | None,
        spec: str | None, extras: str | None, asset: str | None, image: str | None,
        no_install: bool):
    """Clone a skill/tool repo into ~/.aidem/registry and register it.

    aidem detects a default runtime from file markers (pyproject.toml -> uv,
    Cargo.toml/go.mod -> binary, Dockerfile -> docker) and installs the tool
    into an aidem-owned, isolated env at ~/.aidem/envs/<name>/ — never onto the
    global PATH. Override the runtime with --runtime, and pass runtime-specific
    hints (--spec/--extras, --asset, --image) when the auto-heuristic needs help.
    """
    name = _validate_name(name)
    kind = _validate_name(kind)
    ensure_data_dirs()
    kind_registry_dir = registry_dir() / kind
    kind_registry_dir.mkdir(parents=True, exist_ok=True)
    target = kind_registry_dir / name

    if target.exists():
        click.echo(f"Error: {target} already exists.", err=True)
        sys.exit(1)

    try:
        subprocess.run(["git", "clone", git_url, str(target)], check=True)
    except subprocess.CalledProcessError as exc:
        click.echo(f"Failed to clone: {exc}", err=True)
        sys.exit(1)

    runtime = runtime_opt or _detect_runtime(target)
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
            f"No runtime marker (pyproject.toml/Dockerfile/Cargo.toml/go.mod/package.json) "
            f"found. Registering '{name}' as a skills-only repo."
        )
        binary = ""
        runtime = "skills-only"
    elif runtime in DEFERRED_RUNTIMES:
        click.echo(
            f"Detected runtime '{runtime}' is not yet implemented. "
            f"Registering '{name}' as a skills-only repo (no `aidem run`)."
        )
        binary = ""
        runtime = "skills-only"
    else:
        # binary / docker: binary name defaults to the repo name unless overridden.
        binary = click.prompt(
            f"Runtime '{runtime}'. Binary name to exec (Enter for '{name}')",
            default=name,
        ) if not binary else binary

    manifest = load_manifest()
    entry: dict = {
        "path": _repo_rel_path(target),
        "binary": binary or "",
        "runtime": runtime or "skills-only",
        "source": git_url,
        "kind": kind,
    }
    if spec:
        entry["spec"] = spec
    if extras is not None:
        entry["extras"] = extras
    if asset:
        entry["asset"] = asset
    if image:
        entry["image"] = image
    manifest[name] = entry
    save_manifest(manifest)

    if binary and runtime in RUNTIME_KINDS and not no_install:
        meta = dict(entry)
        meta["name"] = name
        meta["_repo_path"] = str(target)
        rt = _runtime_for(meta, name)
        try:
            msg = rt.install(git_url)
            click.echo(f"Installed into isolated env: {msg}")
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            click.echo(f"Added to registry but failed to install: {exc}", err=True)

    source = _content_source_for(target, kind)
    if source is not None:
        target_dir = kind_dir(kind)
        _add_shared_content(name, source, kind)
        mirrored = _regenerate_mirrors()
        rule_mirrored = _regenerate_rule_mirrors()
        click.echo(
            f"Linked {kind} '{name}' into {target_dir} "
            f"({mirrored} skill mirror(s), {rule_mirrored} rule mirror(s))."
        )

    label = kind if not binary else "tool"
    click.echo(f"Added {label} '{name}' (kind={kind}, runtime={runtime}). "
               f"Run `aidem setup` to build tool bridges.")


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
        binary = meta.get("binary", "")
        if binary and runtime in RUNTIME_KINDS:
            rt = _runtime_for({**meta, "name": name, "_repo_path": str(tool_path)}, name)
            if not rt.is_installed():
                try:
                    msg = rt.install(meta.get("source"))
                    click.echo(f"  {name}: {msg}")
                except (subprocess.CalledProcessError, RuntimeError) as exc:
                    click.echo(f"Failed to install {name}: {exc}", err=True)
            else:
                click.echo(f"  {name}: env already installed.")
        elif not binary:
            click.echo(f"  {name}: skills-only repo, no binary to install.")

    # Reconcile content links and mirrors.
    linked = _refresh_content_links()
    mirrored = _regenerate_mirrors()
    rule_mirrored = _regenerate_rule_mirrors()
    click.echo(
        f"Registry setup complete ({linked} link(s), {mirrored} skill mirror(s), "
        f"{rule_mirrored} rule mirror(s))."
    )


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
    rule_mirrored = _regenerate_rule_mirrors()
    click.echo(
        f"Registry updated ({mirrored} skill mirror(s), {rule_mirrored} rule mirror(s)). "
        f"Run `aidem setup` to refresh links/mirrors."
    )


@registry.command(name="list")
def list_registry():
    """List registered skills/tools."""
    manifest = load_manifest()
    if not manifest:
        click.echo("No skills/tools registered. Use `aidem registry add <git-url> <name>`.")
        return
    # Collect docker image refs present in one call so listing doesn't fire a
    # `docker image inspect` subprocess per docker entry.
    docker_images: set[str] | None = None
    has_docker = any(
        m.get("binary") and m.get("runtime") == "docker" for m in manifest.values()
    )
    if has_docker and shutil.which("docker"):
        try:
            out = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True, text=True, check=False,
            )
            docker_images = {line.strip() for line in out.stdout.splitlines() if line.strip()}
        except Exception:
            docker_images = set()
    click.echo("Registered skills/tools:")
    for name, meta in manifest.items():
        binary = meta.get("binary", "")
        kd = meta.get("kind") or meta.get("category", "skill")
        repo_path = _repo_abs_path(meta["path"])
        has_content = _content_source_for(repo_path, kd) is not None
        skill_mark = "+" if has_content else " "
        runtime = meta.get("runtime", "uv")
        if not binary:
            kind_label = "skills-only"
        elif runtime not in RUNTIME_KINDS:
            kind_label = f"{runtime} binary={binary}"
        elif runtime == "docker":
            image = meta.get("image") or f"aidem/{name}"
            installed = "yes" if (docker_images is not None and image in docker_images) else "no"
            kind_label = f"runtime=docker image={image} env={installed}"
        else:
            rt = _runtime_for({**meta, "name": name, "_repo_path": str(repo_path)}, name)
            installed = "yes" if rt.is_installed() else "no"
            kind_label = f"runtime={runtime} binary={binary} env={installed}"
        click.echo(f"  {skill_mark} {name} (kind={kd}) {kind_label}")


@registry.command()
@click.argument("name")
def install(name: str):
    """Install (or reinstall) a registered tool into its isolated env."""
    name = _validate_name(name)
    manifest = load_manifest()
    if name not in manifest:
        click.echo(f"Error: '{name}' not found in registry.", err=True)
        sys.exit(1)
    meta = manifest[name]
    tool_path = _repo_abs_path(meta["path"])
    runtime = meta.get("runtime", "uv")
    if not meta.get("binary"):
        click.echo(f"'{name}' is a skills-only repo (no binary to install).")
        return
    if runtime not in RUNTIME_KINDS:
        click.echo(f"Runtime '{runtime}' is not yet supported for install.", err=True)
        sys.exit(1)
    rt = _runtime_for({**meta, "name": name, "_repo_path": str(tool_path)}, name)
    try:
        msg = rt.install(meta.get("source"))
        click.echo(f"Installed '{name}': {msg}")
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        click.echo(f"Failed to install '{name}': {exc}", err=True)
        sys.exit(1)


@registry.command()
@click.argument("name")
def remove(name: str):
    """Unregister and remove a skill/tool."""
    name = _validate_name(name)
    manifest = load_manifest()
    if name not in manifest:
        click.echo(f"Error: '{name}' not found in registry.", err=True)
        sys.exit(1)
    meta = manifest[name]
    binary = meta.get("binary", name)
    runtime = meta.get("runtime", "uv")

    # Tear down the aidem-owned isolated env (no global PATH involvement).
    if binary and runtime in RUNTIME_KINDS:
        rt = _runtime_for({**meta, "name": name}, name)
        try:
            click.echo(rt.uninstall())
        except Exception as exc:
            click.echo(f"Warning: env teardown failed: {exc}", err=True)

    # Legacy cleanup: an entry installed via the old `uv tool` path lives on the
    # global PATH, not in ~/.aidem/envs/. If the env dir is absent but the binary
    # resolves globally, uninstall it too so removal is complete.
    if binary and not env_dir(name).exists() and shutil.which(binary):
        subprocess.run(["uv", "tool", "uninstall", binary],
                       cwd=PACKAGE_ROOT, check=False)

    repo_path = _repo_abs_path(meta["path"])
    if repo_path.exists():
        shutil.rmtree(repo_path)

    _clean_git_modules(meta["path"])

    kd = meta.get("kind") or meta.get("category", "skill")
    removed = _remove_shared_content(name, kd)
    if removed:
        _regenerate_mirrors()
        _regenerate_rule_mirrors()

    del manifest[name]
    save_manifest(manifest)
    msg = f"Removed '{name}'."
    if removed:
        msg += f" Removed {removed} file(s) from {kind_dir(kd)}."
    click.echo(msg)


# ---------------------------------------------------------------------------
# Layer 1A: Centralized bridging  (aidem setup)
# ---------------------------------------------------------------------------


@cli.command()
def setup():
    """Build the one-time dir bridges from each IDE's content folders into aidem.

    Each AI tool's global skills/rules directory is symlinked ONCE to an
    aidem-internal staging library, so every entry you add afterwards (via
    `aidem create` or `aidem registry add`) surfaces in every bridged tool with
    no further wiring:

      Skills:
        Kilo:     ~/.kilo/skills                -> ~/.aidem/skills
        Claude:   ~/.claude/skills              -> ~/.aidem/skills
        Cursor:   ~/.cursor/skills              -> ~/.aidem/skills
        OpenCode: ~/.config/opencode/skills     -> ~/.aidem/skills
        Windsurf: ~/.codeium/windsurf/skills    -> ~/.aidem/skills

      Rules:
        Claude:   ~/.claude/rules               -> ~/.aidem/rules   (passthrough)
        Kilo:     instructions[] in ~/.config/kilo/kilo.jsonc      (config glob)
        OpenCode: instructions[] in ~/.config/opencode/opencode.json (config glob)
        Windsurf: ~/.codeium/windsurf/memories/global_rules.md     (concat mirror)
        Cursor/GitHub: skipped (no file-based global rules path)

    Also reconciles ~/.aidem content against the registry and regenerates
    transformed mirrors. Idempotent — safe to re-run. Refuses to clobber an
    existing real directory (it tells you how to proceed). Skips tools whose
    parent directory is not present (tool not installed).
    """
    from config.generators import (
        ensure_all_bridges, ensure_all_rule_bridges, shared_skills_dir,
        collect_rule_warnings,
    )

    ensure_data_dirs()
    linked = _refresh_content_links()

    skills = shared_skills_dir(PACKAGE_ROOT / "config")
    if not any(skills.iterdir()) and linked == 0:
        click.echo(
            "No skills in ~/.aidem/skills. Create one with `aidem create <name> --skill` "
            "or register a repo with `aidem registry add`."
        )

    click.echo("Skill bridges:")
    for status, msg in ensure_all_bridges(PACKAGE_ROOT / "config"):
        click.echo(f"  [{status}] {msg}")

    click.echo("Rule bridges:")
    for status, msg in ensure_all_rule_bridges(PACKAGE_ROOT / "config"):
        click.echo(f"  [{status}] {msg}")

    mirrored = _regenerate_mirrors()
    rule_mirrored = _regenerate_rule_mirrors()
    for warning in collect_rule_warnings(PACKAGE_ROOT / "config"):
        click.echo(f"  [warn] {warning}")
    click.echo(
        f"Refreshed libraries (skills: {linked} link(s), {mirrored} mirror(s); "
        f"rules: {rule_mirrored} mirror(s))."
    )


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
# Layer 1C: Content authoring  (aidem create ...)
# ---------------------------------------------------------------------------


SKILL_TEMPLATE = """---
name: {name}
description: Describe what this skill does and when to use it.
---

# {name}

Instructions for the AI agent.
"""

RULE_TEMPLATE = "# Rule: {name}\n\nInstructions the AI agent must always follow.\n"


@cli.command()
@click.argument("name")
@click.option("--skill", "kind", flag_value="skill", help="Create a skill (default).")
@click.option("--rule", "kind", flag_value="rule", help="Create a rule.")
@click.option("--body", "body", default=None,
              help="Content text (alternative to $EDITOR). Falls back to editor/paste.")
def create(name: str, kind: str | None, body: str | None):
    """Create a skill or rule in aidem's central library and refresh mirrors.

    Skills (default, --skill) live at ~/.aidem/skills/<name>/SKILL.md and follow
    the Agent Skills spec (YAML frontmatter). Rules (--rule) live at
    ~/.aidem/rules/<name>.md as plain markdown (add tool-specific frontmatter
    like Claude `paths` if you want path scoping). By default this opens
    $EDITOR (or prompts you to paste); pass --body "..." to provide it
    non-interactively.
    """
    name = _validate_name(name)
    if kind is None:
        kind = "skill"
    ensure_data_dirs()

    if kind == "rule":
        target = user_rules_dir() / f"{name}.md"
        template = RULE_TEMPLATE.format(name=name)
    else:
        target = user_skills_dir() / name / "SKILL.md"
        template = SKILL_TEMPLATE.format(name=name)

    if target.exists() and not click.confirm(
        f"{target} already exists. Overwrite?", default=False
    ):
        click.echo("Aborted.")
        return

    if body is None:
        edited = click.edit(template)
        if not edited:
            click.echo("Aborted: no content provided.", err=True)
            sys.exit(1)
        body = edited

    if kind == "skill":
        if "name:" not in body.split("---")[1] if "---" in body else True:
            click.echo("Warning: missing 'name' in frontmatter. "
                       "Skill may not be recognized by all tools.", err=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    mirrored = _regenerate_mirrors()
    rule_mirrored = _regenerate_rule_mirrors()
    if kind == "rule":
        click.echo(
            f"Created rule '{name}' at {target} "
            f"({rule_mirrored} rule mirror(s) refreshed)."
        )
    else:
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

    meta = manifest[tool]
    binary = meta.get("binary", "")
    runtime = meta.get("runtime", "uv")
    if not binary:
        click.echo(
            f"Error: '{tool}' is a skills-only repo (no binary).",
            err=True,
        )
        sys.exit(1)

    # Docker runtime dispatches via its own run() (container exec).
    if runtime == "docker":
        rt = _runtime_for({**meta, "name": tool}, tool)
        try:
            rt.run(list(args))
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    # uv / binary: resolve from the aidem-owned env, with a legacy fallback.
    resolved, migrated = _resolve_run_binary(meta, tool)
    if resolved is None:
        click.echo(
            f"Error: Binary '{binary}' not installed. "
            f"Run `aidem registry install {tool}`.",
            err=True,
        )
        sys.exit(1)
    if migrated:
        click.echo(
            f"Note: '{tool}' is running from a legacy global install. "
            f"Run `aidem registry install {tool}` to migrate it into aidem's "
            f"sandboxed env (~/.aidem/envs/{tool}/).",
            err=True,
        )
    os.execvp(resolved, [resolved] + list(args))


if __name__ == "__main__":
    cli()