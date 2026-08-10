#!/usr/bin/env python3
"""aidem: AI development environment manager.

Layers:
  0. Registry  -- git clone skill/tool repos and (optionally) install binaries via uv.
  1. Bridging  -- one-time dir symlinks from each IDE's skills dir into ~/.aidem/skills.
  2. Execution -- pass-through run of registered tools in isolated uv environments.

User data (skills, registry, manifest) lives in ~/.aidem (overridable via
AIDEM_DATA_DIR). Shipped package assets (generators and runtimes) travel with
the install and are read-only.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import click

from .paths import (
    data_dir,
    registry_dir,
    env_dir,
    manifest_path,
    skills_dir as user_skills_dir,
    rules_dir as user_rules_dir,
    kind_dir,
    REGISTRY_KINDS,
    ensure_data_dirs,
    PACKAGE_ROOT,
)

from . import paths as aidem_paths  # noqa: F401  (re-exported for tests)

# Skill file names scanned for in a registered repo (checked in order).
SKILL_FILE_CANDIDATES = ("skill.md", "SKILL.md", "SKILL.MD")
SKILL_SUPPORT_DIRS = {"references", "scripts", "assets", "rules"}

# Rule file names scanned for in a registered rule repo (checked in order).
RULE_FILE_CANDIDATES = ("rule.md", "RULE.md")

# Runtime kinds and deferred set come from a single source of truth in
# config.runtimes to avoid drift between the CLI and the adapters.
from .config.runtimes import SUPPORTED_RUNTIMES as RUNTIME_KINDS
from .config.runtimes import DEFERRED_RUNTIMES


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


def _enforce_policy(command_id: str, *, url: str | None = None) -> None:
    """Block a command when the org server policy forbids it (enforced mode)."""
    from .server_state import policy_allows
    allowed, reason = policy_allows(command_id, url=url)
    if not allowed:
        click.echo(f"Error: {reason}", err=True)
        sys.exit(1)


def _normalize_server_url(url: str) -> str:
    """Normalize a server URL, allowing HTTP only for loopback development."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise click.BadParameter("invalid server URL") from exc
    if not hostname or parsed.username or parsed.password:
        raise click.BadParameter("server URL must contain a host and no credentials")
    if parsed.scheme.lower() != "https":
        if not (parsed.scheme.lower() == "http" and
                hostname.lower() in {"127.0.0.1", "::1", "localhost"}):
            raise click.BadParameter(
                "server URL must use HTTPS (HTTP is allowed only on loopback)"
            )
    return url.rstrip("/")


def _sync_org_content_best_effort() -> None:
    """Pull org content after setup/update when a server connection exists.

    Sync failures (server unreachable, expired session) are warnings here —
    only an explicit `aidem server sync` fails the command.
    """
    from . import server_state
    if not server_state.is_connected():
        return
    from . import server_sync
    from .server_http import ServerError
    try:
        linked, removed, mcp = server_sync.sync_org_content(echo=click.echo)
        click.echo(f"Org content synced ({linked} item(s), {removed} removed, "
                   f"{mcp} MCP entries).")
    except ServerError as exc:
        click.echo(f"Warning: org content sync failed: {exc.message}", err=True)


def load_manifest() -> dict:
    if manifest_path().exists():
        return json.loads(manifest_path().read_text())
    return {}


def save_manifest(manifest: dict) -> None:
    ensure_data_dirs()
    manifest_path().write_text(json.dumps(manifest, indent=2))


def _detect_runtime(tool_path: Path) -> str | None:
    """Detect a default runtime kind from file markers in the cloned repo.

    Delegates to config.runtimes (file-presence detection, not README parsing).
    Returns a supported kind ('uv'/'binary'/'docker'), a deferred kind
    ('npm'/'cargo'/'go'), or None (no marker / skills-only).
    """
    from .config.runtimes import detect_runtime
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


def _copy_regular_tree(source: Path, target: Path) -> None:
    """Copy content without following symlinks or special files."""
    try:
        mode = source.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"cannot inspect content source {source}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise ValueError(f"refusing symlinked content source: {source}")
    if stat.S_ISREG(mode):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(f"refusing special content source: {source}")

    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == ".git":
            continue
        _copy_regular_tree(item, target / item.name)


def _path_contains_symlink(root: Path, path: Path) -> bool:
    current = root
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    for part in parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _copy_skill_support_dirs(source_parent: Path, target: Path) -> None:
    for item in source_parent.iterdir():
        if item.is_dir() and item.name in SKILL_SUPPORT_DIRS:
            _copy_regular_tree(item, target / item.name)


def _materialize_skill_entry(name: str, source: Path, staging_root: Path) -> bool:
    if source.is_symlink():
        raise ValueError(f"refusing symlinked skill source: {source}")
    skill_dir = staging_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        md_files = sorted(source.glob("*.md"))
        if md_files:
            if any(_path_contains_symlink(source, f) for f in md_files):
                raise ValueError(f"refusing symlinked skill content under {source}")
            _copy_regular_tree(md_files[-1], skill_dir / "SKILL.md")
            _copy_skill_support_dirs(source, skill_dir)
            return True
        added = False
        for f in sorted(source.glob("**/SKILL.md")):
            if f.is_symlink() or _path_contains_symlink(source, f):
                raise ValueError(f"refusing symlinked skill source: {f}")
            sub = f.parent.name
            nested_dir = staging_root / f"{name}_{sub}"
            nested_dir.mkdir(parents=True, exist_ok=True)
            _copy_regular_tree(f, nested_dir / "SKILL.md")
            _copy_skill_support_dirs(f.parent, nested_dir)
            added = True
        return added

    _copy_regular_tree(source, skill_dir / "SKILL.md")
    _copy_skill_support_dirs(source.parent, skill_dir)
    return True


def _replace_materialized_entries(staging_root: Path, target_dir: Path, name: str) -> int:
    entries = [p for p in staging_root.iterdir() if p.name == name or p.name.startswith(f"{name}_")]
    if not entries:
        return 0
    old_entries = [target_dir / name, target_dir / f"{name}.md"] + list(target_dir.glob(f"{name}_*"))
    backup_root = Path(tempfile.mkdtemp(prefix=f".{name}.backup.", dir=target_dir))
    moved_new: list[Path] = []
    try:
        for old in old_entries:
            if old.exists() or old.is_symlink():
                os.replace(old, backup_root / old.name)
        for entry in entries:
            destination = target_dir / entry.name
            os.replace(entry, destination)
            moved_new.append(destination)
    except Exception:
        for destination in moved_new:
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            elif destination.is_dir():
                shutil.rmtree(destination)
        for old in backup_root.iterdir():
            os.replace(old, target_dir / old.name)
        raise
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)
    return len(entries)


def _add_shared_skill(name: str, source: Path, target_dir: Path | None = None) -> bool:
    if target_dir is None:
        target_dir = user_skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    staging_root = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=target_dir))
    try:
        added = _materialize_skill_entry(name, source, staging_root)
        if not added:
            return False
        _replace_materialized_entries(staging_root, target_dir, name)
        return True
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _add_shared_rule(name: str, source: Path, target_dir: Path) -> bool:
    """Link a rule source (file or rules/ dir) into the shared rules library.

    Rules are flat: one file per rule at ~/.aidem/rules/<name>.md (or
    <name>_<stem>.md when a repo contributes multiple rule files).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=target_dir))
    try:
        if source.is_symlink():
            raise ValueError(f"refusing symlinked rule source: {source}")
        if source.is_dir():
            added = False
            for f in sorted(source.glob("*.md")):
                stem = f.stem
                link_name = f"{name}.md" if stem.lower() == name.lower() else f"{name}_{stem}.md"
                _copy_regular_tree(f, staging_root / link_name)
                added = True
        else:
            _copy_regular_tree(source, staging_root / f"{name}.md")
            added = True
        if not added:
            return False
        old_files = [target_dir / f"{name}.md"] + list(target_dir.glob(f"{name}_*.md"))
        backup_root = Path(tempfile.mkdtemp(prefix=f".{name}.backup.", dir=target_dir))
        moved_new: list[Path] = []
        try:
            for old in old_files:
                if old.is_symlink() or old.exists():
                    os.replace(old, backup_root / old.name)
            for entry in staging_root.iterdir():
                destination = target_dir / entry.name
                os.replace(entry, destination)
                moved_new.append(destination)
        except Exception:
            for destination in moved_new:
                if destination.is_symlink() or destination.is_file():
                    destination.unlink()
            for old in backup_root.iterdir():
                os.replace(old, target_dir / old.name)
            raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        return True
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


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
        if d.is_symlink():
            d.unlink()
            removed += 1
        elif d.is_dir():
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
    from .config.generators import regenerate_all_mirrors
    return regenerate_all_mirrors(PACKAGE_ROOT / "config")


def _regenerate_rule_mirrors() -> int:
    from .config.generators import regenerate_all_rule_mirrors
    return regenerate_all_rule_mirrors(PACKAGE_ROOT / "config")


def _refresh_content() -> int:
    count = 0
    for name, meta in load_manifest().items():
        repo_path = _repo_abs_path(meta["path"])
        source = _content_source_for(repo_path, meta.get("kind") or meta.get("category", "skill"))
        kd = meta.get("kind") or meta.get("category", "skill")
        if source is not None:
            try:
                if _add_shared_content(name, source, kd):
                    count += 1
            except ValueError as exc:
                click.echo(f"Warning: skipped unsafe content for '{name}': {exc}", err=True)
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
    """Resolve a stored manifest path, rejecting escapes from its root.

    Personal entries are relative to the registry dir; org entries carry an
    "org/" prefix and are relative to the data dir (~/.aidem/org/...).
    """
    p = Path(stored)
    root = data_dir() if stored.startswith("org/") and not p.is_absolute() else registry_dir()
    resolved = p.resolve() if p.is_absolute() else (root / stored).resolve()
    root = root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"manifest entry path '{stored}' escapes its root directory ({root})"
        )
    return resolved


def _clean_git_modules(rel_path: str) -> None:
    # Plain clones leave no .git/modules; kept for safety against legacy manifests.
    mod = PACKAGE_ROOT / ".git" / "modules" / rel_path
    if mod.exists():
        shutil.rmtree(mod)


def _runtime_for(meta: dict, name: str) -> "object":
    """Construct the runtime adapter for a manifest entry (env-owned)."""
    from .config.runtimes import runtime_for
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
@click.version_option(version="0.1.3")
@click.pass_context
def cli(ctx):
    """aidem: AI development environment manager."""
    # Best-effort server stale-check: refresh policy when stale and surface a
    # one-line drift hint. Fully guarded — a broken/unreachable server must
    # never break local commands (this runs before `aidem run`'s execvp).
    cmd = ctx.invoked_subcommand or ""
    if cmd == "server":
        return  # the server subgroup handles its own freshness
    from .server_sync import maybe_refresh_policy
    hint = maybe_refresh_policy()
    if hint:
        click.echo(f"Note: {hint}", err=True)


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
                    "asset), docker (hardened container). Override the auto-detected default.")
@click.option("--spec", "spec", default=None,
               help="uv runtime: explicit PyPI install spec (e.g. 'headroom-ai[all]'). "
                    "Without this, install the checked-out clone.")
@click.option("--extras", "extras", default=None,
              help="uv runtime: optional-dependency group(s) to install (e.g. 'all', 'proxy'). "
                   "Defaults to 'all' if the clone declares it.")
@click.option("--asset", "asset", default=None,
               help="binary runtime: release-asset name substring to pick (e.g. "
                    "'rtk-aarch64-apple-darwin').")
@click.option("--release", "release", default=None,
               help="binary runtime: immutable GitHub release tag to install.")
@click.option("--sha256", "sha256", default=None,
               help="binary runtime: required SHA-256 digest for the release asset.")
@click.option("--image", "image", default=None,
               help="docker runtime: digest-pinned image ref to pull (e.g. 'ghcr.io/org/tool@sha256:...'). "
                    "Defaults to building the clone's Dockerfile as aidem/<name>.")
@click.option("--write-worktree", is_flag=True,
               help="docker runtime: allow the container to write the current worktree.")
@click.option("--no-install", is_flag=True,
              help="Register the repo without installing the binary now. Install later with "
                   "`aidem registry install <name>`.")
def add(git_url: str, name: str, kind: str, runtime_opt: str | None,
        spec: str | None, extras: str | None, asset: str | None, release: str | None,
        sha256: str | None, image: str | None, write_worktree: bool, no_install: bool):
    """Clone a skill/tool repo into ~/.aidem/registry and register it.

    aidem detects a default runtime from file markers (pyproject.toml -> uv,
    Cargo.toml/go.mod -> binary, Dockerfile -> docker) and installs the tool
    into an aidem-owned, isolated env at ~/.aidem/envs/<name>/ — never onto the
    global PATH. Override the runtime with --runtime, and pass runtime-specific
    hints (--spec/--extras, --asset, --release/--sha256, --image) when needed.
    """
    _enforce_policy("registry.add", url=git_url)
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
        subprocess.run(["git", "clone", "--", git_url, str(target)], check=True)
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

    if runtime == "binary" and (not release or not sha256):
        shutil.rmtree(target, ignore_errors=True)
        click.echo("Binary registration requires both --release and --sha256.", err=True)
        sys.exit(1)
    if runtime == "binary" and (
        "\x00" in release or "\n" in release or release.startswith("-")
        or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None
    ):
        shutil.rmtree(target, ignore_errors=True)
        click.echo("Binary registration requires a safe release tag and 64-character SHA-256.",
                   err=True)
        sys.exit(1)

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
    if release:
        entry["release"] = release
    if sha256:
        entry["sha256"] = sha256
    if image:
        entry["image"] = image
    if write_worktree:
        entry["write_worktree"] = True
    if binary and runtime in RUNTIME_KINDS and not no_install:
        if not click.confirm(
            f"Install '{name}' now from the registered source?",
            default=False,
        ):
            no_install = True
            click.echo("Registered without installation. Run `aidem registry install` later.")
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
        try:
            _add_shared_content(name, source, kind)
        except ValueError as exc:
            shutil.rmtree(target, ignore_errors=True)
            click.echo(f"Refusing unsafe {kind} content: {exc}", err=True)
            sys.exit(1)
        mirrored = _regenerate_mirrors()
        rule_mirrored = _regenerate_rule_mirrors()
        click.echo(
            f"Linked {kind} '{name}' into {target_dir} "
            f"({mirrored} skill mirror(s), {rule_mirrored} rule mirror(s))."
        )

    manifest[name] = entry
    save_manifest(manifest)

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
                    ["git", "clone", "--", meta["source"], str(tool_path)], check=True
                )
            except subprocess.CalledProcessError as exc:
                click.echo(f"Failed to clone {name}: {exc}", err=True)
                continue

        runtime = meta.get("runtime", "uv")
        binary = meta.get("binary", "")
        if binary and runtime in RUNTIME_KINDS:
            rt = _runtime_for({**meta, "name": name, "_repo_path": str(tool_path)}, name)
            if not rt.is_installed():
                if not click.confirm(
                    f"Install '{name}' from the registered source now?",
                    default=False,
                ):
                    click.echo(f"  {name}: installation skipped.")
                    continue
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
    linked = _refresh_content()
    mirrored = _regenerate_mirrors()
    rule_mirrored = _regenerate_rule_mirrors()
    click.echo(
        f"Registry setup complete ({linked} content item(s), {mirrored} skill mirror(s), "
        f"{rule_mirrored} rule mirror(s))."
    )


@registry.command()
def update():
    """Pull updates for all cloned registry repos."""
    _enforce_policy("registry.update")
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
    linked = _refresh_content()
    mirrored = _regenerate_mirrors()
    rule_mirrored = _regenerate_rule_mirrors()
    click.echo(
        f"Registry updated ({linked} content item(s), {mirrored} skill mirror(s), "
        f"{rule_mirrored} rule mirror(s)). "
        f"Run `aidem setup` to refresh tool bridges/mirrors."
    )
    _sync_org_content_best_effort()


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
        org_label = f" [org:{meta['org']}]" if meta.get("owner") == "org" else ""
        click.echo(f"  {skill_mark} {name} (kind={kd}) {kind_label}{org_label}")


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
    _enforce_policy("registry.remove")
    name = _validate_name(name)
    manifest = load_manifest()
    if name not in manifest:
        click.echo(f"Error: '{name}' not found in registry.", err=True)
        sys.exit(1)
    meta = manifest[name]
    if meta.get("owner") == "org":
        click.echo(
            f"Error: '{name}' is managed by your organization "
            f"({meta.get('org')}). Run `aidem server logout` to disconnect.",
            err=True,
        )
        sys.exit(1)
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
# Enterprise server connection  (aidem server ...)
# ---------------------------------------------------------------------------


@cli.group()
def server():
    """Connect to an enterprise aidem server (SSO, org content, policy)."""
    pass


@server.command()
@click.argument("url")
@click.option("--device-code", is_flag=True, help="Headless login (no browser).")
@click.option("--allow-org-mcp", is_flag=True,
              help="Enable validated organization MCP definitions during initial sync.")
def login(url: str, device_code: bool, allow_org_mcp: bool):
    """Log in to an enterprise aidem server via SSO and sync org content."""
    from . import server_auth, server_state, server_sync
    from .server_http import ServerError

    server_url = _normalize_server_url(url)
    try:
        result = server_auth.login_flow(server_url, device_code=device_code)
    except ServerError as exc:
        click.echo(f"Error: login failed: {exc.message}", err=True)
        sys.exit(1)
    server_auth.store_credentials(server_url,
                                  server_auth.credentials_payload(result))
    server_state.save_state({
        "server_url": server_url,
        "org": result.get("org"),
        "user": result.get("user", {}),
        "policy": dict(server_state.DEFAULT_POLICY),
        "content_sources": [],
        "content_version": None,
        "last_policy_check": None,
        "last_sync": None,
    })
    user = result.get("user", {})
    try:
        linked, removed, mcp = server_sync.sync_org_content(
            echo=click.echo, allow_mcp=allow_org_mcp
        )
    except ServerError as exc:
        click.echo(
            f"Logged in as {user.get('email')} ({result.get('org')}), but the "
            f"initial sync failed: {exc.message}", err=True)
        sys.exit(1)
    click.echo(f"Logged in as {user.get('email')} (org: {result.get('org')}). "
               f"Synced {linked} org item(s), removed {removed}, "
               f"MCP entries {mcp}.")


@server.command()
def logout():
    """Disconnect from the server and remove all org-managed content."""
    from . import server_auth, server_state, server_sync

    state = server_state.load_state()
    if state is None:
        click.echo("Not connected to an aidem server.")
        return
    org = state.get("org")
    if not click.confirm(
        f"Disconnect from {org} ({state['server_url']}) and remove org content?",
        default=True,
    ):
        click.echo("Aborted.")
        return
    server_sync.remove_org_content(org)
    server_auth.logout_remote_best_effort()
    server_auth.clear_credentials()
    server_state.clear_state()
    click.echo(f"Disconnected from {org} ({state['server_url']}). "
               f"Org content removed.")


@server.command()
def status():
    """Show the server connection, policy, and session state (no network)."""
    from . import server_auth, server_state

    state = server_state.load_state()
    if state is None:
        click.echo("Not connected. Run `aidem server login <url>`.")
        return
    policy = server_state.load_policy()
    user = state.get("user") or {}
    click.echo(f"Server:   {state['server_url']}")
    click.echo(f"Org:      {state.get('org')}")
    click.echo(f"User:     {user.get('email', '?')} ({user.get('role', '?')})")
    blocked = ", ".join(policy["blocked_commands"]) or "none"
    click.echo(f"Policy:   {policy['mode']} (blocked: {blocked})")
    click.echo(f"Last sync: {state.get('last_sync') or 'never'}")
    click.echo(f"Session:  {server_auth.token_status()}")
    sources = server_state.org_content_sources()
    if sources:
        click.echo("Content sources:")
        for s in sources:
            click.echo(f"  {s.get('name')} (kind={s.get('kind', 'skill')}) "
                       f"{s.get('git_url')}")
    else:
        click.echo("Content sources: none")


@server.command(name="sync")
@click.option("--allow-org-mcp", is_flag=True,
              help="Enable validated organization MCP definitions during this sync.")
def sync_cmd(allow_org_mcp: bool):
    """Pull the latest org policy and content from the server."""
    from . import server_state, server_sync
    from .server_http import ServerError

    if not server_state.is_connected():
        click.echo("Not connected. Run `aidem server login <url>`.", err=True)
        sys.exit(1)
    try:
        linked, removed, mcp = server_sync.sync_org_content(
            echo=click.echo, allow_mcp=allow_org_mcp
        )
    except ServerError as exc:
        click.echo(f"Error: sync failed: {exc.message}", err=True)
        sys.exit(1)
    click.echo(f"Synced {linked} org item(s), removed {removed}, "
               f"MCP entries {mcp}.")


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
    from .config.generators import (
        ensure_all_bridges, ensure_all_rule_bridges, shared_skills_dir,
        collect_rule_warnings,
    )

    ensure_data_dirs()
    linked = _refresh_content()

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
        f"Refreshed libraries (skills: {linked} content item(s), {mirrored} mirror(s); "
        f"rules: {rule_mirrored} mirror(s))."
    )
    _sync_org_content_best_effort()


# ---------------------------------------------------------------------------
# Layer 1B: Content authoring  (aidem create ...)
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
    _enforce_policy("create")
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
