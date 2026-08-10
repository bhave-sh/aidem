"""Org content synchronization for the enterprise server connection.

Server = control plane (policy + content source list); git = data plane.
This module clones/pulls the org's content repos into
~/.aidem/org/<org>/registry/<kind>/<name> and copies them into the shared
libraries with collision-safe names, reusing the existing registry helpers
in cli.py (imported lazily to avoid a circular import and to stay safe
under the test suite's module-reload fixture).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import server_auth, server_state
from .paths import kind_dir, org_registry_dir
from .server_http import ServerError, api_request

POLICY_TTL_SECONDS = 900  # 15 min
ORG_CONTENT_KINDS = {"skill", "rule", "mcp"}
SAFE_REF = re.compile(r"^[A-Za-z0-9._/@-]+$")


def _safe_component(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 200
        and ".." not in value
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _safe_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith("-")
        and SAFE_REF.fullmatch(value) is not None
        and ".." not in value
        and "@{" not in value
        and not value.endswith((".", ".lock"))
    )


def _safe_git_url(value: object) -> bool:
    if (not isinstance(value, str) or not value or value.startswith("-")
            or any(ord(char) < 32 for char in value)):
        return False
    if value.startswith("git@"):
        return ":" in value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"https", "ssh", "git"} and bool(parsed.netloc)


def _safe_content_source(source: dict) -> bool:
    kind = source.get("kind", "skill")
    ref = source.get("ref")
    return (
        isinstance(kind, str)
        and kind in ORG_CONTENT_KINDS
        and _safe_component(source.get("name"))
        and _safe_git_url(source.get("git_url"))
        and (not ref or _safe_ref(ref))
    )


def _parse_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Policy refresh (stale-check)
# ---------------------------------------------------------------------------


def maybe_refresh_policy(*, force: bool = False) -> str | None:
    """Best-effort policy refresh. Returns a drift hint for the user or None.

    With force=True (explicit user actions like `server sync`) failures
    raise ServerError — fail loud. Without force this is the stale-check
    that runs before every command and must never break local work, so all
    failures are swallowed (deliberate, sanctioned fail-silent path).
    """
    if force:
        return _refresh_policy(force=True)
    try:
        return _refresh_policy(force=False)
    except Exception:
        # Deliberate fail-silent: an unreachable/broken server must not
        # break local commands. This is the sole sanctioned exception to
        # the repo's fail-loud rule.
        return None


def _refresh_policy(*, force: bool) -> str | None:
    state = server_state.load_state()
    if state is None:
        return None
    if not force:
        now = datetime.now(timezone.utc)
        for key in ("last_policy_check", "last_policy_attempt"):
            ts = _parse_ts(state.get(key))
            if ts and (now - ts).total_seconds() < POLICY_TTL_SECONDS:
                return None
    token = server_auth.ensure_access_token()
    if token is None:
        server_state.record_policy_check(False)
        hint = "server session expired, run `aidem server login`"
        if force:
            raise ServerError(hint, status=None, code="login_required")
        return hint
    old_version = state.get("content_version")
    try:
        payload = api_request("GET", "/v1/sync", token=token,
                              timeout=10.0 if force else 1.0)
    except ServerError:
        server_state.record_policy_check(False)
        if force:
            raise
        return None
    state["policy"] = payload.get("policy")
    state["content_sources"] = payload.get("content_sources", [])
    state["content_version"] = payload.get("content_version")
    server_state.save_state(state)
    server_state.record_policy_check(True)
    if old_version is not None and payload.get("content_version") != old_version:
        return "org content changed, run `aidem server sync`"
    return None


# ---------------------------------------------------------------------------
# Collision-safe link names (coexist semantics)
# ---------------------------------------------------------------------------


def _is_org_owned(target: Path, org: str) -> bool:
    """True when the manifest identifies `target` as this org's content."""
    from . import cli as aidem_cli

    target_name = target.name
    for meta in aidem_cli.load_manifest().values():
        if meta.get("owner") != "org" or meta.get("org") != org:
            continue
        link_name = meta.get("link_name")
        if isinstance(link_name, str) and (
            target_name == link_name
            or target_name == f"{link_name}.md"
            or target_name.startswith(f"{link_name}_")
        ):
            return True
    return False


def resolve_link_name(org: str, name: str, kind: str) -> str:
    """Pick the shared-library name for an org entry.

    Returns `name`, or `<org>__<name>` when a personal (non-org-owned)
    entry already occupies it. Multi-file repos produce `<name>_<sub>`
    suffixed entries, so those are checked for collisions too.
    """
    kd = kind_dir(kind)
    if kind == "rule":
        candidates = [kd / f"{name}.md"] + sorted(kd.glob(f"{name}_*.md"))
    else:
        candidates = [kd / name] + sorted(kd.glob(f"{name}_*"))
    for target in candidates:
        if not (target.exists() or target.is_symlink()):
            continue
        if not _is_org_owned(target, org):
            return f"{org}__{name}"
    return name


# ---------------------------------------------------------------------------
# Git data plane
# ---------------------------------------------------------------------------


def _clone_or_pull(source: dict, dest: Path, echo) -> bool:
    """Clone (shallow) or fast-forward an org content repo.

    On failure: warn, keep the last good clone, and return False so the
    caller can continue with other sources.
    """
    ref = source.get("ref") or ""
    try:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", "clone", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += ["--", source["git_url"], str(dest)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        else:
            if ref:
                subprocess.run(["git", "fetch", "origin", ref], cwd=dest, check=True,
                               capture_output=True, text=True)
                subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=dest, check=True,
                               capture_output=True, text=True)
            else:
                subprocess.run(["git", "pull", "--ff-only"], cwd=dest, check=True,
                               capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or str(exc)).strip().splitlines()
        echo(f"Warning: failed to update org source '{source['name']}' "
             f"({detail[-1] if detail else exc}); keeping last good copy.")
        return False
    return True


def _mcp_entries_for(clone: Path) -> dict[str, dict]:
    """Read MCP server definitions from an org repo.

    Canonical on-disk shape (checked in order): `mcp.json` at the repo root,
    or `mcp/*.json` files. Each file is either {"mcpServers": {...}} or a
    bare {name: definition} mapping.
    """
    root_file = clone / "mcp.json"
    if root_file.exists() and not root_file.is_symlink():
        files = [root_file]
    else:
        mcp_sub = clone / "mcp"
        files = (
            [f for f in sorted(mcp_sub.glob("*.json")) if not f.is_symlink()]
            if mcp_sub.is_dir() and not mcp_sub.is_symlink() else []
        )
    entries: dict[str, dict] = {}

    def valid_entry(name: object, entry: object) -> bool:
        if not _safe_component(name) or not isinstance(entry, dict):
            return False
        allowed = {"command", "args", "env", "url", "headers"}
        if set(entry) - allowed:
            return False
        if "url" in entry:
            url = entry["url"]
            try:
                parsed = urlsplit(url)
            except (TypeError, ValueError):
                return False
            if parsed.scheme != "https" or not parsed.netloc:
                return False
            headers = entry.get("headers", {})
            return isinstance(headers, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            )
        command = entry.get("command")
        args = entry.get("args", [])
        env = entry.get("env", {})
        return (
            isinstance(command, str) and bool(command) and "\x00" not in command
            and isinstance(args, list)
            and all(isinstance(arg, str) and "\x00" not in arg for arg in args)
            and isinstance(env, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        )

    for f in files:
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers", data)
        if isinstance(servers, dict):
            for name, entry in servers.items():
                if valid_entry(name, entry):
                    entries[name] = entry
    return entries


def _remove_manifest_entry(
    aidem_cli, manifest: dict, name: str, meta: dict
) -> tuple[int, int]:
    """Unlink content, delete the clone, and drop an org manifest entry."""
    kind = meta.get("kind", "skill")
    link_name = meta.get("link_name", name)
    removed_links = 0
    if kind == "rule":
        removed_links = aidem_cli._remove_shared_rule(link_name, kind_dir("rule"))
    elif kind != "mcp":
        removed_links = aidem_cli._remove_shared_skill(link_name)
    removed_clones = 0
    try:
        clone = aidem_cli._repo_abs_path(meta["path"])
        if clone.exists():
            shutil.rmtree(clone)
            removed_clones = 1
    except ValueError:
        pass  # corrupt path entry — the manifest entry is dropped regardless
    del manifest[name]
    return removed_links, removed_clones


# ---------------------------------------------------------------------------
# Full sync / teardown
# ---------------------------------------------------------------------------


def sync_org_content(echo=print, *, allow_mcp: bool = False) -> tuple[int, int, int]:
    """Clone/pull org content, refresh copies + MCP configs, prune stale entries.

    Returns (linked, removed, mcp) counts. Raises ServerError when the
    policy refresh fails (explicit user action — fail loud). Per-source git
    failures are warnings; the last good clone stays in force.
    """
    from . import cli as aidem_cli

    maybe_refresh_policy(force=True)
    state = server_state.load_state()
    org = state["org"]
    sources = []
    for source in server_state.org_content_sources():
        if _safe_content_source(source):
            sources.append(source)
        else:
            echo(f"Warning: skipped invalid org content source {source.get('name')!r}.")

    manifest = aidem_cli.load_manifest()
    desired = {s["name"] for s in sources}
    removed = 0
    for name, meta in list(manifest.items()):
        if meta.get("owner") == "org" and meta.get("org") == org and name not in desired:
            _remove_manifest_entry(aidem_cli, manifest, name, meta)
            removed += 1

    linked = 0
    mcp_entries: dict[str, dict] = {}
    for src in sources:
        kind = src.get("kind", "skill")
        name = src["name"]
        clone = org_registry_dir(org) / kind / name
        if not _clone_or_pull(src, clone, echo) and not clone.exists():
            continue  # no last-good clone to link from
        entry = {
            "path": f"org/{org}/registry/{kind}/{name}",
            "binary": "",
            "runtime": "skills-only",
            "source": src["git_url"],
            "kind": kind,
            "owner": "org",
            "org": org,
        }
        if kind == "mcp":
            mcp_entries.update(_mcp_entries_for(clone))
            entry["link_name"] = name
            manifest[name] = entry
            continue
        source_path = aidem_cli._content_source_for(clone, kind)
        if source_path is None:
            echo(f"Warning: no {kind} content found in org source '{name}'; skipped.")
            continue
        link_name = resolve_link_name(org, name, kind)
        try:
            if kind == "rule":
                aidem_cli._add_shared_rule(link_name, source_path, kind_dir("rule"))
            else:
                aidem_cli._add_shared_skill(link_name, source_path)
        except ValueError as exc:
            echo(f"Warning: skipped unsafe org content '{name}': {exc}")
            continue
        entry["link_name"] = link_name
        manifest[name] = entry
        linked += 1

    aidem_cli.save_manifest(manifest)
    aidem_cli._regenerate_mirrors()
    aidem_cli._regenerate_rule_mirrors()

    mcp_count = 0
    has_mcp = any(s.get("kind") == "mcp" for s in sources)
    if has_mcp or mcp_entries:
        if not allow_mcp:
            echo("MCP content skipped; rerun `aidem server sync --allow-org-mcp` "
                 "to review and enable organization MCP entries.")
            mcp_entries = {}
        else:
            from .config.generators.mcp import McpBridge
            for status, msg in McpBridge(org).apply(mcp_entries):
                echo(f"  [{status}] {msg}")
            mcp_count = len(mcp_entries)

    server_state.record_sync(server_state.load_state().get("content_version") or "")
    return linked, removed, mcp_count


def remove_org_content(org: str) -> dict:
    """Tear down everything an org owns locally (server logout).

    Removes content links, clones, manifest entries, and org MCP entries.
    Personal content and other registries are never touched.
    """
    from . import cli as aidem_cli

    manifest = aidem_cli.load_manifest()
    removed_links = 0
    removed_clones = 0
    for name, meta in list(manifest.items()):
        if meta.get("owner") != "org" or meta.get("org") != org:
            continue
        links, clones = _remove_manifest_entry(aidem_cli, manifest, name, meta)
        removed_links += links
        removed_clones += clones
    aidem_cli.save_manifest(manifest)

    from .config.generators.mcp import McpBridge
    mcp_results = McpBridge(org).clear()
    aidem_cli._regenerate_mirrors()
    aidem_cli._regenerate_rule_mirrors()
    return {"links": removed_links, "clones": removed_clones, "mcp": mcp_results}
