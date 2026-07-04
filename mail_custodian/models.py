from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage


@dataclass(frozen=True)
class Criteria:
    match: str = "all"
    sender: tuple[str, ...] = ()
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    subject_contains: tuple[str, ...] = ()
    body_contains: tuple[str, ...] = ()
    header_contains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    seen: bool | None = None
    flagged: bool | None = None
    answered: bool | None = None
    has_attachments: bool | None = None
    older_than_days: int | None = None
    younger_than_days: int | None = None
    size_larger_than: int | None = None
    size_smaller_than: int | None = None

    def matches(self, message: "MessageData") -> bool:
        checks: list[bool] = []

        if self.sender:
            checks.append(_contains_any(message.sender, self.sender))
        if self.to:
            checks.append(_contains_any(message.to, self.to))
        if self.cc:
            checks.append(_contains_any(message.cc, self.cc))
        if self.subject_contains:
            checks.append(_contains_any(message.subject, self.subject_contains))
        if self.body_contains:
            checks.append(_contains_any(message.body_text, self.body_contains))

        for header_name, values in self.header_contains.items():
            header_value = "\n".join(message.email_message.get_all(header_name, []))
            checks.append(_contains_any(header_value, values))

        if self.seen is not None:
            checks.append(message.has_flag("\\Seen") is self.seen)
        if self.flagged is not None:
            checks.append(message.has_flag("\\Flagged") is self.flagged)
        if self.answered is not None:
            checks.append(message.has_flag("\\Answered") is self.answered)
        if self.has_attachments is not None:
            checks.append(message.has_attachments is self.has_attachments)
        if self.older_than_days is not None:
            checks.append(message.internal_date <= _cutoff(self.older_than_days))
        if self.younger_than_days is not None:
            checks.append(message.internal_date >= _cutoff(self.younger_than_days))
        if self.size_larger_than is not None:
            checks.append(message.size > self.size_larger_than)
        if self.size_smaller_than is not None:
            checks.append(message.size < self.size_smaller_than)

        if not checks:
            return True
        if self.match == "any":
            return any(checks)
        return all(checks)


@dataclass(frozen=True)
class Actions:
    move_to: str | None = None
    copy_to: str | None = None
    mark_read: bool = False
    mark_unread: bool = False
    add_flags: tuple[str, ...] = ()
    remove_flags: tuple[str, ...] = ()
    delete: bool = False
    stop_processing: bool = False


@dataclass(frozen=True)
class Rule:
    name: str
    mailbox: str
    criteria: Criteria
    actions: Actions


@dataclass(frozen=True)
class AccountConfig:
    name: str
    host: str
    username: str
    password: str
    port: int = 993
    ssl: bool = True
    timeout: int = 30
    default_mailbox: str = "INBOX"
    create_missing_mailboxes: bool = False
    rules: tuple[Rule, ...] = ()


@dataclass(frozen=True)
class AppConfig:
    log_level: str
    accounts: tuple[AccountConfig, ...]


@dataclass(frozen=True)
class MessageData:
    uid: str
    mailbox: str
    sender: str
    to: str
    cc: str
    subject: str
    body_text: str
    size: int
    flags: frozenset[str]
    internal_date: datetime
    has_attachments: bool
    email_message: EmailMessage

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags


@dataclass(frozen=True)
class ActionResult:
    expunge_needed: bool = False
    block_further_rules: bool = False


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    haystack = text.casefold()
    return any(needle.casefold() in haystack for needle in needles)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
