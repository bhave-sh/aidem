"""Focused tests for server state, token state, and org paths."""

from datetime import datetime, timedelta, timezone
import json
import os
from urllib.error import URLError
from urllib.request import Request

import pytest


def _iso(offset: timedelta) -> str:
    return (datetime.now(timezone.utc) + offset).isoformat().replace("+00:00", "Z")


def test_org_manifest_path_resolves_inside_data_dir(fake_dirs):
    from aidem import cli as aidem_cli

    path = aidem_cli._repo_abs_path("org/acme/registry/skill/docs")

    assert path == fake_dirs["data"] / "org" / "acme" / "registry" / "skill" / "docs"


def test_manifest_path_rejects_escape(fake_dirs):
    from aidem import cli as aidem_cli

    with pytest.raises(ValueError):
        aidem_cli._repo_abs_path("org/../../outside")


def test_server_url_requires_https(fake_dirs):
    from aidem import cli as aidem_cli

    assert aidem_cli._normalize_server_url("https://example.test") == "https://example.test"
    with pytest.raises(Exception):
        aidem_cli._normalize_server_url("http://example.test")


def test_invalid_connected_policy_fails_closed():
    from aidem import server_state

    with pytest.warns(UserWarning):
        policy = server_state.validate_policy(None)

    assert policy["mode"] == "enforced"
    assert set(policy["blocked_commands"]) == set(server_state.KNOWN_COMMAND_IDS)


def test_cross_origin_redirect_is_rejected():
    from aidem.server_http import _SameOriginRedirectHandler

    request = Request("https://aidem.example/v1/sync")
    with pytest.raises(URLError):
        _SameOriginRedirectHandler().redirect_request(
            request, None, 302, "found", {}, "https://evil.example/collect"
        )


def test_fallback_credentials_replace_symlink_safely(fake_dirs):
    from aidem import server_auth
    from aidem.paths import credentials_fallback_path

    path = credentials_fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    outside = fake_dirs["data"].parent / "outside-credentials"
    outside.write_text("must remain unchanged")
    path.symlink_to(outside)

    server_auth._fallback_store("https://aidem.example", {"refresh_token": "secret"})

    assert outside.read_text() == "must remain unchanged"
    assert path.is_file() and not path.is_symlink()
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_org_source_validation_rejects_path_and_git_option_injection():
    from aidem import server_sync

    valid = {"name": "team-skills", "kind": "skill", "git_url": "https://git.example/team/skills"}
    assert server_sync._safe_content_source(valid)
    assert not server_sync._safe_content_source({**valid, "name": "../outside"})
    assert not server_sync._safe_content_source({**valid, "git_url": "--upload-pack=evil"})


def test_mcp_source_accepts_only_valid_definitions(tmp_path):
    from aidem.server_sync import _mcp_entries_for

    clone = tmp_path / "mcp-repo"
    clone.mkdir()
    (clone / "mcp.json").write_text(json.dumps({"mcpServers": {
        "valid": {"url": "https://mcp.example/api"},
        "http": {"url": "http://mcp.example/api"},
        "unknown": {"command": "tool", "unexpected": True},
        "empty": {"command": ""},
    }}))

    assert _mcp_entries_for(clone) == {"valid": {"url": "https://mcp.example/api"}}


def test_token_status_distinguishes_valid_and_refreshable_tokens():
    from aidem.server_auth import token_status

    assert token_status({"access_expires_at": _iso(timedelta(hours=1))}) == "valid"
    assert token_status({
        "access_expires_at": _iso(-timedelta(minutes=1)),
        "refresh_token": "refresh",
        "refresh_expires_at": _iso(timedelta(days=1)),
    }) == "expired-but-refreshable"
