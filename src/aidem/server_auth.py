"""Credential storage and authentication flows for the aidem server.

Tokens live in the OS keychain (keyring, service "aidem-server", username =
normalized server URL). Headless Linux systems commonly have no keyring
backend, so any keyring failure falls back to a 0600 JSON file at
~/.aidem/.credentials.json (with a one-line warning, once per process).

Login uses OIDC authorization-code + PKCE against the aidem server (which
brokers the IdP); the CLI never sees IdP credentials. A device-code flow is
the headless fallback. All network calls go through server_http.api_request
so tests stub a single object.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

import keyring

from . import server_state
from .paths import credentials_fallback_path
from .server_http import ServerError, api_request, validate_server_url

KEYRING_SERVICE = "aidem-server"
ACCESS_TOKEN_MARGIN_SECONDS = 60
REFRESH_TOKEN_TTL_DAYS = 30  # documented server default (~30d)
LOGIN_TIMEOUT_SECONDS = 300

# One-line fallback warning is printed at most once per process.
_fallback_warned = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Credential storage (keyring with 0600-file fallback)
# ---------------------------------------------------------------------------


def _warn_fallback() -> None:
    global _fallback_warned
    if not _fallback_warned:
        _fallback_warned = True
        print("aidem: OS keychain unavailable; storing credentials in "
              f"{credentials_fallback_path()} (0600).", file=sys.stderr)


def _fallback_load_all() -> dict:
    path = credentials_fallback_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _fallback_store(server_url: str, payload: dict) -> None:
    path = credentials_fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _fallback_load_all()
    data[server_url] = payload
    _write_fallback(data)


def _write_fallback(data: dict) -> None:
    """Atomically replace the fallback credential file with mode 0600."""
    path = credentials_fallback_path()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            os.fchmod(tmp.fileno(), 0o600)
            json.dump(data, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def store_credentials(server_url: str, payload: dict) -> None:
    """Persist the token payload for a server (keychain, else 0600 file)."""
    raw = json.dumps(payload)
    try:
        keyring.set_password(KEYRING_SERVICE, server_url, raw)
        return
    except Exception:
        # Headless Linux raises NoKeyringError; other backends raise their
        # own errors. The 0600-file fallback is required, not optional.
        _warn_fallback()
    _fallback_store(server_url, payload)


def load_credentials(server_url: str | None = None) -> dict | None:
    """Load the stored token payload for the connected server, if any."""
    if server_url is None:
        state = server_state.load_state()
        if state is None:
            return None
        server_url = state["server_url"]
    raw = None
    try:
        raw = keyring.get_password(KEYRING_SERVICE, server_url)
    except Exception:
        _warn_fallback()
        raw = None
    if raw is None:
        entry = _fallback_load_all().get(server_url)
        return entry if isinstance(entry, dict) else None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def clear_credentials() -> None:
    """Delete stored credentials for the connected server (both stores)."""
    state = server_state.load_state()
    server_url = state["server_url"] if state else None
    if server_url:
        try:
            keyring.delete_password(KEYRING_SERVICE, server_url)
        except Exception:
            pass  # already absent or backend unavailable — nothing to delete
        data = _fallback_load_all()
        if server_url in data:
            del data[server_url]
            _write_fallback(data)


def credentials_payload(exchange_response: dict) -> dict:
    """Build the stored payload from a /v1/auth/exchange (or device poll) response."""
    return {
        "access_token": exchange_response["access_token"],
        "access_expires_at": _iso(_now() + timedelta(
            seconds=int(exchange_response.get("expires_in", 3600)))),
        "refresh_token": exchange_response["refresh_token"],
        "refresh_expires_at": _iso(_now() + timedelta(days=REFRESH_TOKEN_TTL_DAYS)),
    }


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------


def token_status(creds: dict | None = None) -> str:
    """Classify the local token state without any network call.

    One of "valid", "expired-but-refreshable", "login-required".
    """
    if creds is None:
        creds = load_credentials()
    if not creds:
        return "login-required"
    access_exp = _parse_iso(creds.get("access_expires_at"))
    if access_exp and _now() + timedelta(seconds=ACCESS_TOKEN_MARGIN_SECONDS) < access_exp:
        return "valid"
    refresh_exp = _parse_iso(creds.get("refresh_expires_at"))
    if creds.get("refresh_token") and (refresh_exp is None or _now() < refresh_exp):
        return "expired-but-refreshable"
    return "login-required"


def ensure_access_token() -> str | None:
    """Return a valid access token, refreshing once if needed.

    Never opens a browser — re-login is the user's explicit action via
    `aidem server login` (via login_flow). Returns None when no valid
    session exists.
    """
    creds = load_credentials()
    if not creds:
        return None
    status = token_status(creds)
    if status == "valid":
        return creds["access_token"]
    if status != "expired-but-refreshable":
        return None
    try:
        resp = api_request("POST", "/v1/auth/refresh",
                           json_body={"refresh_token": creds["refresh_token"]})
    except ServerError as exc:
        if exc.status == 401:
            # Refresh token rejected — the session is gone for good.
            clear_credentials()
        return None
    state = server_state.load_state()
    if state is None:
        return None
    # Rotation is mandatory: the server issues a new refresh token each time.
    store_credentials(state["server_url"], credentials_payload(resp))
    return resp["access_token"]


# ---------------------------------------------------------------------------
# Login flows
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the server's redirect back to the CLI (code + state)."""

    def do_GET(self):  # noqa: N802 (http.server API)
        query = parse_qs(urlparse(self.path).query)
        self.server.auth_result = {  # type: ignore[attr-defined]
            "code": query.get("code", [None])[0],
            "state": query.get("state", [None])[0],
            "error": query.get("error", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h3>aidem login complete. "
                         b"You can close this tab.</h3></body></html>")

    def log_message(self, *args):  # silence request logging
        pass


def _browser_login(server_url: str, authorize_url: str) -> dict:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    httpd = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    httpd.timeout = 1.0
    port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    url = (f"{authorize_url}?client_redirect_uri={quote(redirect_uri, safe='')}"
           f"&state={state}&code_challenge={challenge}&code_challenge_method=S256")
    import webbrowser
    print(f"Opening browser to log in: {authorize_url}")
    webbrowser.open(url)
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            httpd.handle_request()
            result = getattr(httpd, "auth_result", None)
            if result:
                break
        else:
            raise ServerError("login timed out waiting for the browser callback",
                              status=None, code="login_timeout")
    finally:
        httpd.server_close()
    if result.get("error"):
        raise ServerError(f"login failed: {result['error']}",
                          status=None, code=result["error"])
    if result.get("state") != state or not result.get("code"):
        raise ServerError("login failed: state mismatch or missing code",
                          status=None, code="bad_callback")
    return api_request("POST", f"{server_url}/v1/auth/exchange",
                       json_body={"code": result["code"],
                                  "code_verifier": verifier,
                                  "redirect_uri": redirect_uri})


def _device_login(server_url: str) -> dict:
    device = api_request("POST", f"{server_url}/v1/auth/device", json_body={})
    print(f"\nTo log in, visit: {device['verification_uri']}")
    print(f"and enter code:     {device['user_code']}\n")
    interval = max(int(device.get("interval", 5)), 1)
    deadline = time.monotonic() + int(device.get("expires_in", 600))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            return api_request("POST", f"{server_url}/v1/auth/device/poll",
                               json_body={"device_code": device["device_code"]})
        except ServerError as exc:
            if exc.code == "authorization_pending":
                continue
            if exc.code == "slow_down":
                interval += 5
                continue
            raise
    raise ServerError("device login expired before authorization",
                      status=None, code="expired_token")


def login_flow(server_url: str, *, device_code: bool) -> dict:
    """Run a login flow against the server; return the token exchange response.

    Browser flow (default): PKCE authorization-code with a localhost redirect.
    Device flow (--device-code): headless; prints a code for the user to enter
    on the server's verification page. The response shape is
    {access_token, refresh_token, expires_in, user: {email, role}, org}.
    """
    config = api_request("GET", f"{server_url}/v1/auth/config")
    if device_code:
        if not config.get("device_supported", True):
            raise ServerError("this server does not support device-code login",
                              status=None, code="device_unsupported")
        return _device_login(server_url)
    authorize_url = validate_server_url(config["authorize_url"])
    return _browser_login(server_url, authorize_url)


def logout_remote_best_effort() -> None:
    """Revoke the session server-side. Best-effort by design: logout must
    complete locally even when the server is unreachable."""
    token = ensure_access_token()
    if not token:
        return
    try:
        api_request("POST", "/v1/auth/logout", token=token, json_body={})
    except ServerError:
        pass
