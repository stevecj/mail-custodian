from __future__ import annotations

from pathlib import Path

from mail_custodian.models import MailboxCheckpoint
from mail_custodian.state import GmailOAuthStore, MailboxStateStore, default_gmail_oauth_path, default_state_path


def test_default_state_path_uses_xdg_state_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    assert default_state_path() == tmp_path / "state-home" / "mail-custodian" / "checkpoints.json"


def test_state_store_round_trips_checkpoints(tmp_path: Path) -> None:
    store = MailboxStateStore(tmp_path / "checkpoints.json")
    store.put("personal", "INBOX", MailboxCheckpoint(uidvalidity=999, last_uid=42))
    store.save()

    reloaded = MailboxStateStore(tmp_path / "checkpoints.json")
    assert reloaded.get("personal", "INBOX") == MailboxCheckpoint(uidvalidity=999, last_uid=42)


def test_default_gmail_oauth_path_uses_xdg_state_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    assert default_gmail_oauth_path() == tmp_path / "state-home" / "mail-custodian" / "gmail-oauth.json"


def test_gmail_oauth_store_round_trips_refresh_tokens(tmp_path: Path) -> None:
    store = GmailOAuthStore(tmp_path / "gmail-oauth.json")
    store.put("gmail", "refresh-token")
    store.save()

    reloaded = GmailOAuthStore(tmp_path / "gmail-oauth.json")
    assert reloaded.get("gmail") == "refresh-token"
