from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from .models import AccountConfig
from .state import GmailOAuthStore


class GmailOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class _AuthorizationResponse:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


def build_xoauth2_response(username: str, access_token: str) -> bytes:
    return f"user={username}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


def refresh_access_token(account: AccountConfig, *, token_store: GmailOAuthStore | None = None) -> str:
    oauth = _require_gmail_oauth(account)
    refresh_token = oauth.refresh_token
    if refresh_token is None:
        store = token_store or GmailOAuthStore()
        refresh_token = store.get(account.name)
    if not refresh_token:
        raise GmailOAuthError(
            f"account '{account.name}' has no stored Gmail refresh token; run "
            f"'mail-custodian --authorize-gmail {account.name}' first"
        )

    payload = _post_form(
        oauth.token_uri,
        {
            "client_id": oauth.client_id,
            "client_secret": oauth.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GmailOAuthError("Google OAuth token response did not include an access_token")
    return access_token


def authorize_account(account: AccountConfig, *, token_store: GmailOAuthStore | None = None) -> str:
    oauth = _require_gmail_oauth(account)
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii")
    code_challenge = code_challenge.rstrip("=")
    response = _await_browser_callback(account, state=state, code_challenge=code_challenge)
    if response.error:
        description = response.error_description or response.error
        raise GmailOAuthError(f"Gmail authorization failed: {description}")
    if response.state != state:
        raise GmailOAuthError("Gmail authorization response did not match the expected state")
    if not response.code:
        raise GmailOAuthError("Gmail authorization response did not include an authorization code")

    redirect_uri = _last_redirect_uri()
    payload = _post_form(
        oauth.token_uri,
        {
            "client_id": oauth.client_id,
            "client_secret": oauth.client_secret,
            "code": response.code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise GmailOAuthError(
            "Google OAuth token response did not include a refresh_token; "
            "make sure the OAuth client is a Desktop app and consent was granted with offline access"
        )

    store = token_store or GmailOAuthStore()
    store.put(account.name, refresh_token)
    store.save()
    return refresh_token


def _require_gmail_oauth(account: AccountConfig):
    if account.provider != "gmail" or account.gmail_oauth is None:
        raise GmailOAuthError(f"account '{account.name}' is not configured as a Gmail OAuth account")
    return account.gmail_oauth


_redirect_uri_local = threading.local()


def _build_authorization_url(account: AccountConfig, *, state: str, code_challenge: str) -> str:
    oauth = _require_gmail_oauth(account)
    redirect_uri = _last_redirect_uri()
    query = urllib.parse.urlencode(
        {
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": oauth.scope,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{oauth.auth_uri}?{query}"


def _await_browser_callback(account: AccountConfig, *, state: str, code_challenge: str) -> _AuthorizationResponse:
    response: _AuthorizationResponse | None = None
    ready = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal response
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            response = _AuthorizationResponse(
                code=_first_value(params, "code"),
                state=_first_value(params, "state"),
                error=_first_value(params, "error"),
                error_description=_first_value(params, "error_description"),
            )
            body = (
                "Mail Custodian authorization received. You can close this browser window."
                if response.error is None
                else "Mail Custodian authorization failed. You can close this browser window."
            )
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            ready.set()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            del format, args
            return

    with HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
        _redirect_uri_local.value = f"http://127.0.0.1:{server.server_port}/"
        authorization_url = _build_authorization_url(account, state=state, code_challenge=code_challenge)
        if not webbrowser.open(authorization_url):
            print("Open this URL in your browser to authorize Gmail access:")
            print(authorization_url)

        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        if not ready.wait(timeout=300):
            raise GmailOAuthError("timed out waiting for Gmail authorization response")
        thread.join(timeout=1)

    if response is None:
        raise GmailOAuthError("Gmail authorization did not return a usable response")
    return response


def _last_redirect_uri() -> str:
    redirect_uri = getattr(_redirect_uri_local, "value", None)
    if not isinstance(redirect_uri, str) or not redirect_uri:
        return "__REDIRECT_URI__"
    return redirect_uri


def _first_value(values: dict[str, list[str]], key: str) -> str | None:
    raw_values = values.get(key)
    if not raw_values:
        return None
    value = raw_values[0]
    return value if value else None


def _post_form(url: str, fields: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        raise GmailOAuthError(f"Google OAuth request failed: {raw_error}") from exc
    except urllib.error.URLError as exc:
        raise GmailOAuthError(f"Google OAuth request failed: {exc.reason}") from exc

    if not isinstance(payload, dict):
        raise GmailOAuthError("Google OAuth response was not a JSON object")
    return payload
