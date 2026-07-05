from __future__ import annotations

from pathlib import Path

from mail_custodian.models import MailboxCheckpoint
from mail_custodian.state import MailboxStateStore, default_state_path


def test_default_state_path_uses_xdg_state_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    assert default_state_path() == tmp_path / "state-home" / "mail-custodian" / "checkpoints.json"


def test_state_store_round_trips_checkpoints(tmp_path: Path) -> None:
    store = MailboxStateStore(tmp_path / "checkpoints.json")
    store.put("personal", "INBOX", MailboxCheckpoint(uidvalidity=999, last_uid=42))
    store.save()

    reloaded = MailboxStateStore(tmp_path / "checkpoints.json")
    assert reloaded.get("personal", "INBOX") == MailboxCheckpoint(uidvalidity=999, last_uid=42)
