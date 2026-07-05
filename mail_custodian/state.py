from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import MailboxCheckpoint


class StateError(ValueError):
    pass


def default_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "mail-custodian" / "checkpoints.json"
    return Path.home() / ".local" / "state" / "mail-custodian" / "checkpoints.json"


def default_gmail_oauth_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "mail-custodian" / "gmail-oauth.json"
    return Path.home() / ".local" / "state" / "mail-custodian" / "gmail-oauth.json"


class MailboxStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_path()
        self._loaded = False
        self._dirty = False
        self._data: dict[str, dict[str, MailboxCheckpoint]] = {}

    def get(self, account_name: str, mailbox: str) -> MailboxCheckpoint | None:
        self._ensure_loaded()
        return self._data.get(account_name, {}).get(mailbox)

    def put(self, account_name: str, mailbox: str, checkpoint: MailboxCheckpoint) -> None:
        self._ensure_loaded()
        account_data = self._data.setdefault(account_name, {})
        if account_data.get(mailbox) == checkpoint:
            return
        account_data[mailbox] = checkpoint
        self._dirty = True

    def save(self) -> None:
        self._ensure_loaded()
        if not self._dirty:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "mailboxes": {
                account_name: {
                    mailbox: {
                        "uidvalidity": checkpoint.uidvalidity,
                        "last_uid": checkpoint.last_uid,
                    }
                    for mailbox, checkpoint in sorted(mailboxes.items())
                }
                for account_name, mailboxes in sorted(self._data.items())
            },
        }

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".checkpoints.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

        temp_path.replace(self.path)
        self._dirty = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return

        try:
            raw_data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"failed to parse checkpoint state file {self.path}: {exc}") from exc

        if not isinstance(raw_data, dict):
            raise StateError(f"checkpoint state file {self.path} must contain a JSON object")
        version = raw_data.get("version")
        if version != 1:
            raise StateError(f"unsupported checkpoint state version in {self.path}: {version!r}")

        raw_mailboxes = raw_data.get("mailboxes", {})
        if not isinstance(raw_mailboxes, dict):
            raise StateError(f"checkpoint state file {self.path} has an invalid 'mailboxes' section")

        parsed: dict[str, dict[str, MailboxCheckpoint]] = {}
        for account_name, mailboxes in raw_mailboxes.items():
            if not isinstance(account_name, str) or not account_name:
                raise StateError(f"checkpoint state file {self.path} has an invalid account name")
            if not isinstance(mailboxes, dict):
                raise StateError(f"checkpoint state file {self.path} has an invalid mailbox map")

            parsed_mailboxes: dict[str, MailboxCheckpoint] = {}
            for mailbox, raw_checkpoint in mailboxes.items():
                if not isinstance(mailbox, str) or not mailbox:
                    raise StateError(f"checkpoint state file {self.path} has an invalid mailbox name")
                if not isinstance(raw_checkpoint, dict):
                    raise StateError(f"checkpoint state file {self.path} has an invalid checkpoint entry")

                uidvalidity = raw_checkpoint.get("uidvalidity")
                last_uid = raw_checkpoint.get("last_uid")
                if (
                    isinstance(uidvalidity, bool)
                    or not isinstance(uidvalidity, int)
                    or uidvalidity < 1
                    or isinstance(last_uid, bool)
                    or not isinstance(last_uid, int)
                    or last_uid < 0
                ):
                    raise StateError(f"checkpoint state file {self.path} has an invalid checkpoint value")

                parsed_mailboxes[mailbox] = MailboxCheckpoint(
                    uidvalidity=uidvalidity,
                    last_uid=last_uid,
                )
            parsed[account_name] = parsed_mailboxes

        self._data = parsed


class GmailOAuthStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_gmail_oauth_path()
        self._loaded = False
        self._dirty = False
        self._data: dict[str, str] = {}

    def get(self, account_name: str) -> str | None:
        self._ensure_loaded()
        return self._data.get(account_name)

    def put(self, account_name: str, refresh_token: str) -> None:
        self._ensure_loaded()
        if self._data.get(account_name) == refresh_token:
            return
        self._data[account_name] = refresh_token
        self._dirty = True

    def save(self) -> None:
        self._ensure_loaded()
        if not self._dirty:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "accounts": dict(sorted(self._data.items())),
        }

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".gmail-oauth.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

        temp_path.replace(self.path)
        self._dirty = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return

        try:
            raw_data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"failed to parse Gmail OAuth state file {self.path}: {exc}") from exc

        if not isinstance(raw_data, dict):
            raise StateError(f"Gmail OAuth state file {self.path} must contain a JSON object")
        version = raw_data.get("version")
        if version != 1:
            raise StateError(f"unsupported Gmail OAuth state version in {self.path}: {version!r}")

        raw_accounts = raw_data.get("accounts", {})
        if not isinstance(raw_accounts, dict):
            raise StateError(f"Gmail OAuth state file {self.path} has an invalid 'accounts' section")

        parsed: dict[str, str] = {}
        for account_name, refresh_token in raw_accounts.items():
            if not isinstance(account_name, str) or not account_name:
                raise StateError(f"Gmail OAuth state file {self.path} has an invalid account name")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise StateError(f"Gmail OAuth state file {self.path} has an invalid refresh token entry")
            parsed[account_name] = refresh_token

        self._data = parsed
