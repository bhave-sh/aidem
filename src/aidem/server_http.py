"""Thin stdlib HTTP wrapper for aidem server API calls.

Kept to a single function (api_request) so tests can stub exactly one object.
Uses urllib.request only — no third-party HTTP deps in the CLI. All path
resolution happens at call time (test-isolation safe).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import server_state


class ServerError(Exception):
    """An aidem server request failed.

    Carries the HTTP status (None for network-level failures), the server's
    error code when present, and a human-readable message.
    """

    def __init__(self, message: str, status: int | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def validate_server_url(url: str) -> str:
    """Allow HTTPS endpoints, plus HTTP only for loopback development servers."""
    if not isinstance(url, str):
        raise ServerError("server URL must be a string", code="bad_server_url")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        scheme = parsed.scheme.lower()
        parsed.port  # Validate malformed ports before making a request.
    except ValueError as exc:
        raise ServerError(f"invalid server URL: {url}", code="bad_server_url") from exc
    if not hostname or parsed.username or parsed.password:
        raise ServerError("server URL must contain a host and no credentials",
                          code="bad_server_url")
    if scheme == "https":
        return url
    if scheme == "http" and hostname.lower() in _LOOPBACK_HOSTS:
        return url
    raise ServerError("server API requires HTTPS (HTTP is allowed only on loopback)",
                      code="insecure_transport")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that could move credentials to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is None:
            return None
        old = urlsplit(req.full_url)
        new = urlsplit(new_request.full_url)
        try:
            old_origin = (old.scheme.lower(), old.hostname, old.port)
            new_origin = (new.scheme.lower(), new.hostname, new.port)
        except ValueError as exc:
            raise urllib.error.URLError("malformed redirect rejected") from exc
        if old_origin != new_origin:
            raise urllib.error.URLError("cross-origin redirect rejected")
        return new_request


def api_request(method: str, path: str, *, token: str | None = None,
                json_body: dict | None = None, timeout: float = 10.0) -> dict:
    """Perform a JSON request against the connected aidem server.

    `path` may be absolute (https://...) — used by the login flow before any
    connection state exists — or a server-relative path like "/v1/sync",
    resolved against the stored server_url.

    Raises ServerError on 4xx/5xx (with the server-provided code/message when
    present) and on network failures (status=None).
    """
    if path.startswith(("http://", "https://")):
        url = path
    else:
        state = server_state.load_state()
        if state is None:
            raise ServerError("not connected to an aidem server", status=None,
                              code="not_connected")
        url = state["server_url"].rstrip("/") + "/" + path.lstrip("/")
    url = validate_server_url(url)

    body = json.dumps(json_body).encode() if json_body is not None else None
    request = urllib.request.Request(url, data=body, method=method.upper())
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    opener = urllib.request.build_opener(_SameOriginRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as resp:
            raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RESPONSE_BYTES:
                raise ServerError("server response is too large", status=None,
                                  code="response_too_large")
            raw = raw_bytes.decode() or "{}"
    except urllib.error.HTTPError as exc:
        code, message = None, exc.reason or f"HTTP {exc.code}"
        try:
            payload = json.loads(exc.read(MAX_RESPONSE_BYTES).decode() or "{}")
            if isinstance(payload, dict):
                code = payload.get("error")
                message = payload.get("message") or payload.get("error") or message
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise ServerError(str(message), status=exc.code, code=code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServerError(f"server unreachable: {exc}", status=None,
                          code="unreachable") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServerError(f"invalid JSON response from {url}", status=None,
                          code="bad_response") from exc
    return parsed if isinstance(parsed, dict) else {}
