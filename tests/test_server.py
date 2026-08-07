"""Focused tests for server state, token state, and org paths."""

from datetime import datetime, timedelta, timezone

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


def test_token_status_distinguishes_valid_and_refreshable_tokens():
    from aidem.server_auth import token_status

    assert token_status({"access_expires_at": _iso(timedelta(hours=1))}) == "valid"
    assert token_status({
        "access_expires_at": _iso(-timedelta(minutes=1)),
        "refresh_token": "refresh",
        "refresh_expires_at": _iso(timedelta(days=1)),
    }) == "expired-but-refreshable"
