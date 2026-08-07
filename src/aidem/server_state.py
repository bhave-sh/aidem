"""Enterprise server connection state and policy enforcement.

Pure state I/O + policy logic: no HTTP, no click. All paths are resolved
inside functions (never at import time) so tests can relocate the data dir
via AIDEM_DATA_DIR and reload modules safely.

State lives in ~/.aidem/server.json and is written atomically (tmp file +
os.replace) because any command may update last_policy_check.
"""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timezone
from urllib.parse import urlparse

from .paths import server_state_path

# Commands the server may block in enforced mode. Reads, run, setup and all
# server.* commands are never blockable.
KNOWN_COMMAND_IDS = ("create", "registry.add", "registry.remove",
                     "registry.update", "init")

DEFAULT_POLICY = {"mode": "coexist", "blocked_commands": [], "allowed_hosts": []}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_connected() -> bool:
    """True when a server connection state file exists."""
    return load_state() is not None


def load_state() -> dict | None:
    """Load the connection state, or None when not connected.

    Raises ValueError on a corrupt state file (fail loud — a corrupt file
    means local state was tampered with or a bug wrote garbage).
    """
    path = server_state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"corrupt server state file {path}: {exc}") from exc
    if not isinstance(state, dict) or not state.get("server_url"):
        raise ValueError(f"invalid server state file {path}: missing server_url")
    return state


def save_state(state: dict) -> None:
    """Write the connection state atomically (tmp file + os.replace)."""
    path = server_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, path)


def clear_state() -> None:
    """Remove the connection state (server logout)."""
    path = server_state_path()
    if path.exists():
        path.unlink()


def validate_policy(raw: object) -> dict:
    """Validate an untrusted policy payload from the server.

    Fail-closed on shape: an unrecognized `mode` is treated as "enforced"
    (with a warning) so a hostile/buggy server can't silently downgrade.
    Fail-open on structure: a non-dict policy falls back to the coexist
    default (with a warning) — the server is treated as broken and the user
    is not locked out. Unknown keys and unknown command ids are ignored.
    """
    if not isinstance(raw, dict):
        warnings.warn("server policy is not an object; treating as coexist")
        return dict(DEFAULT_POLICY)
    mode = raw.get("mode", "coexist")
    if mode not in ("coexist", "enforced"):
        warnings.warn(f"unknown server policy mode {mode!r}; treating as enforced")
        mode = "enforced"
    blocked = raw.get("blocked_commands", [])
    if not isinstance(blocked, list):
        blocked = []
    blocked = [c for c in blocked if isinstance(c, str) and c in KNOWN_COMMAND_IDS]
    hosts = raw.get("allowed_hosts", [])
    if not isinstance(hosts, list):
        hosts = []
    hosts = [h for h in hosts if isinstance(h, str) and h]
    return {"mode": mode, "blocked_commands": blocked, "allowed_hosts": hosts}


def load_policy() -> dict:
    """The validated current policy, or the coexist default when disconnected."""
    state = load_state()
    if state is None:
        return dict(DEFAULT_POLICY)
    return validate_policy(state.get("policy"))


def org_content_sources() -> list[dict]:
    """Org content git sources from the last sync (empty when disconnected)."""
    state = load_state()
    if not state:
        return []
    sources = state.get("content_sources", [])
    return [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []


def record_policy_check(ok: bool) -> None:
    """Stamp the last policy check time.

    Success updates last_policy_check; failure updates last_policy_attempt
    (used for TTL backoff so a broken server isn't hammered).
    """
    state = load_state()
    if state is None:
        return
    state["last_policy_check" if ok else "last_policy_attempt"] = _now_iso()
    save_state(state)


def record_sync(content_version: str) -> None:
    """Stamp a successful content sync."""
    state = load_state()
    if state is None:
        return
    state["last_sync"] = _now_iso()
    state["content_version"] = content_version
    save_state(state)


def _url_host_and_path(url: str) -> tuple[str, str]:
    """Extract (host, first-path-segment) from a git URL.

    Handles https://host/org/repo.git and scp-like git@host:org/repo.git.
    """
    if "://" not in url and "@" in url and ":" in url:
        # scp-like syntax: git@host:org/repo.git
        host = url.split("@", 1)[1].split(":", 1)[0]
        path = url.split(":", 1)[1]
    else:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
    first_segment = path.split("/", 1)[0] if path else ""
    return host.lower(), first_segment


def _host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    host, first_segment = _url_host_and_path(url)
    if not host:
        return False
    for entry in allowed_hosts:
        entry = entry.strip().lower().rstrip("/")
        if "/" in entry:
            want_host, want_org = entry.split("/", 1)
            if host == want_host and first_segment == want_org:
                return True
        elif host == entry:
            return True
    return False


def policy_allows(command_id: str, *, url: str | None = None) -> tuple[bool, str | None]:
    """Check whether a command is permitted under the current server policy.

    Returns (allowed, reason). Disconnected, coexist mode, and unblocked
    commands are always allowed. In enforced mode, `registry.add` may still
    pass when the git URL's host (optionally host/org) is in allowed_hosts.
    """
    state = load_state()
    if state is None:
        return True, None
    policy = validate_policy(state.get("policy"))
    if policy["mode"] != "enforced":
        return True, None
    if command_id not in policy["blocked_commands"]:
        return True, None
    if command_id == "registry.add" and url and _host_allowed(url, policy["allowed_hosts"]):
        return True, None
    org = state.get("org", "org")
    return False, f"blocked by {org} server policy (enforced mode)"
