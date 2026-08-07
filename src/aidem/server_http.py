"""Thin stdlib HTTP wrapper for aidem server API calls.

Kept to a single function (api_request) so tests can stub exactly one object.
Uses urllib.request only — no third-party HTTP deps in the CLI. All path
resolution happens at call time (test-isolation safe).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

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

    body = json.dumps(json_body).encode() if json_body is not None else None
    request = urllib.request.Request(url, data=body, method=method.upper())
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as exc:
        code, message = None, exc.reason or f"HTTP {exc.code}"
        try:
            payload = json.loads(exc.read().decode() or "{}")
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
