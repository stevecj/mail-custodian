from __future__ import annotations

import json

import mail_custodian.gmail_oauth
from mail_custodian.gmail_oauth import _AuthorizationResponse, authorize_account, build_xoauth2_response, refresh_access_token
from mail_custodian.models import AccountConfig, GmailOAuthConfig
from mail_custodian.state import GmailOAuthStore


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_build_xoauth2_response_formats_gmail_auth_payload() -> None:
    assert build_xoauth2_response("person@gmail.com", "access-token") == (
        b"user=person@gmail.com\x01auth=Bearer access-token\x01\x01"
    )


def test_refresh_access_token_uses_stored_refresh_token(monkeypatch, tmp_path) -> None:
    store = GmailOAuthStore(tmp_path / "gmail-oauth.json")
    store.put("gmail", "stored-refresh-token")
    store.save()

    def fake_urlopen(request, timeout: int):
        assert timeout == 30
        body = request.data.decode("utf-8")
        assert "refresh_token=stored-refresh-token" in body
        return _FakeHTTPResponse({"access_token": "fresh-access-token"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    account = AccountConfig(
        name="gmail",
        host="imap.gmail.com",
        username="person@gmail.com",
        provider="gmail",
        gmail_oauth=GmailOAuthConfig(
            client_id="desktop-client-id",
            client_secret="desktop-client-secret",
        ),
    )

    assert refresh_access_token(account, token_store=store) == "fresh-access-token"


def test_authorize_account_stores_refresh_token(monkeypatch, tmp_path) -> None:
    store = GmailOAuthStore(tmp_path / "gmail-oauth.json")

    monkeypatch.setattr(
        mail_custodian.gmail_oauth,
        "_await_browser_callback",
        lambda account, *, state, code_challenge: _AuthorizationResponse(code="auth-code", state=state),
    )
    monkeypatch.setattr(
        mail_custodian.gmail_oauth,
        "_last_redirect_uri",
        lambda: "http://127.0.0.1:43123/",
    )

    def fake_post_form(url: str, fields: dict[str, str]) -> dict[str, object]:
        assert url == "https://oauth2.googleapis.com/token"
        assert fields["client_id"] == "desktop-client-id"
        assert fields["client_secret"] == "desktop-client-secret"
        assert fields["code"] == "auth-code"
        assert fields["grant_type"] == "authorization_code"
        assert fields["redirect_uri"] == "http://127.0.0.1:43123/"
        assert fields["code_verifier"]
        return {"refresh_token": "new-refresh-token"}

    monkeypatch.setattr(mail_custodian.gmail_oauth, "_post_form", fake_post_form)

    account = AccountConfig(
        name="gmail",
        host="imap.gmail.com",
        username="person@gmail.com",
        provider="gmail",
        gmail_oauth=GmailOAuthConfig(
            client_id="desktop-client-id",
            client_secret="desktop-client-secret",
        ),
    )

    assert authorize_account(account, token_store=store) == "new-refresh-token"
    assert GmailOAuthStore(tmp_path / "gmail-oauth.json").get("gmail") == "new-refresh-token"
