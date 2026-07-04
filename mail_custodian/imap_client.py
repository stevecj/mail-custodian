from __future__ import annotations

import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import unescape

from .models import AccountConfig, ActionResult, Actions, Criteria, MessageData

LOGGER = logging.getLogger(__name__)
COPY_ID_HEADER = "X-Mail-Custodian-Id"


class IMAPSession:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.connection: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        self.capabilities: set[str] = set()
        self.current_mailbox: str | None = None

    def __enter__(self) -> "IMAPSession":
        if self.account.ssl:
            connection = imaplib.IMAP4_SSL(self.account.host, self.account.port, timeout=self.account.timeout)
        else:
            connection = imaplib.IMAP4(self.account.host, self.account.port, timeout=self.account.timeout)

        connection.login(self.account.username, self.account.password)
        self.connection = connection
        self.capabilities = {
            value.decode("ascii", errors="ignore").upper() if isinstance(value, bytes) else value.upper()
            for value in connection.capabilities
        }
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.connection:
            return
        try:
            self.connection.logout()
        finally:
            self.connection = None

    def select_mailbox(self, mailbox: str) -> None:
        connection = self._require_connection()
        if self.current_mailbox == mailbox:
            return
        status, data = connection.select(mailbox)
        if status != "OK":
            raise RuntimeError(f"failed to select mailbox '{mailbox}': {data}")
        self.current_mailbox = mailbox

    def search_uids(self, criteria: Criteria) -> list[str]:
        return self._search_selected_mailbox_uids(self._build_search_terms(criteria))

    def fetch_message(self, uid: str) -> MessageData:
        connection = self._require_connection()
        status, data = connection.uid("fetch", uid, "(FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[])")
        if status != "OK":
            raise RuntimeError(f"failed to fetch message UID {uid}: {data}")

        metadata_chunks: list[bytes] = []
        payload_chunks: list[bytes] = []
        for item in data:
            if isinstance(item, tuple):
                if item[0]:
                    metadata_chunks.append(item[0])
                if len(item) > 1 and item[1]:
                    payload_chunks.append(item[1])
            elif isinstance(item, bytes):
                metadata_chunks.append(item)

        metadata = b" ".join(metadata_chunks)
        raw_message = b"".join(payload_chunks)
        email_message = BytesParser(policy=policy.default).parsebytes(raw_message)

        return MessageData(
            uid=uid,
            mailbox=self.current_mailbox or self.account.default_mailbox,
            sender=email_message.get("From", ""),
            to=email_message.get("To", ""),
            cc=email_message.get("Cc", ""),
            subject=email_message.get("Subject", ""),
            body_text=_extract_body_text(email_message),
            size=_parse_size(metadata),
            flags=frozenset(_parse_flags(metadata)),
            internal_date=_parse_internal_date(metadata),
            has_attachments=_has_attachments(email_message),
            email_message=email_message,
            raw_message=raw_message,
        )

    def apply_actions(
        self,
        message: MessageData,
        actions: Actions,
        *,
        create_missing_mailboxes: bool,
        dry_run: bool,
    ) -> ActionResult:
        uid = message.uid
        blocked = actions.stop_processing or bool(actions.move_to) or actions.delete
        expunge_needed = False

        if dry_run:
            LOGGER.info(
                "dry-run UID %s in %s: copy_to=%s move_to=%s mark_read=%s mark_unread=%s add_flags=%s remove_flags=%s delete=%s",
                uid,
                self.current_mailbox,
                actions.copy_to,
                actions.move_to,
                actions.mark_read,
                actions.mark_unread,
                list(actions.add_flags),
                list(actions.remove_flags),
                actions.delete,
            )
            return ActionResult(expunge_needed=actions.delete or bool(actions.move_to and "MOVE" not in self.capabilities), block_further_rules=blocked)
        if actions.copy_to:
            self._copy_message(message, actions.copy_to, create_missing_mailboxes=create_missing_mailboxes)

        if actions.move_to:
            expunge_needed = self._move_message(
                message,
                actions.move_to,
                create_missing_mailboxes=create_missing_mailboxes,
            ) or expunge_needed

        if actions.mark_read:
            self._store_flags(uid, operation="+FLAGS.SILENT", flags=["\\Seen"])
        if actions.mark_unread:
            self._store_flags(uid, operation="-FLAGS.SILENT", flags=["\\Seen"])
        if actions.add_flags:
            self._store_flags(uid, operation="+FLAGS.SILENT", flags=list(actions.add_flags))
        if actions.remove_flags:
            self._store_flags(uid, operation="-FLAGS.SILENT", flags=list(actions.remove_flags))
        if actions.delete:
            self._store_flags(uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
            expunge_needed = True

        return ActionResult(expunge_needed=expunge_needed, block_further_rules=blocked)

    def expunge(self) -> None:
        connection = self._require_connection()
        status, data = connection.expunge()
        if status != "OK":
            raise RuntimeError(f"failed to expunge mailbox '{self.current_mailbox}': {data}")

    def _build_search_terms(self, criteria: Criteria) -> list[str]:
        terms = ["UNDELETED"]

        if criteria.seen is True:
            terms.append("SEEN")
        elif criteria.seen is False:
            terms.append("UNSEEN")

        if criteria.flagged is True:
            terms.append("FLAGGED")
        elif criteria.flagged is False:
            terms.append("UNFLAGGED")

        if criteria.answered is True:
            terms.append("ANSWERED")
        elif criteria.answered is False:
            terms.append("UNANSWERED")

        now = datetime.now(timezone.utc)
        if criteria.older_than_days is not None:
            cutoff = (now - timedelta(days=criteria.older_than_days)).strftime("%d-%b-%Y")
            terms.extend(["BEFORE", cutoff])
        if criteria.younger_than_days is not None:
            cutoff = (now - timedelta(days=criteria.younger_than_days)).strftime("%d-%b-%Y")
            terms.extend(["SINCE", cutoff])

        return terms

    def _ensure_target_mailbox(self, mailbox: str, create_missing_mailboxes: bool) -> None:
        connection = self._require_connection()
        status, data = connection.list("", mailbox)
        if status == "OK" and any(item for item in data if item):
            return
        if not create_missing_mailboxes:
            raise RuntimeError(f"target mailbox does not exist: {mailbox}")

        create_status, create_data = connection.create(mailbox)
        if create_status != "OK":
            raise RuntimeError(f"failed to create mailbox '{mailbox}': {create_data}")

    def _copy_message(self, message: MessageData, target_mailbox: str, *, create_missing_mailboxes: bool) -> None:
        self._ensure_target_mailbox(target_mailbox, create_missing_mailboxes)
        if self._mailbox_contains_duplicate(target_mailbox, message):
            LOGGER.info("skipping copy of UID %s to %s; matching message already exists", message.uid, target_mailbox)
            return

        connection = self._require_connection()
        append_flags = _format_flag_list(sorted(message.flags))
        append_date = imaplib.Time2Internaldate(message.internal_date)
        status, data = connection.append(target_mailbox, append_flags, append_date, message.raw_message)
        if status != "OK":
            raise RuntimeError(f"failed to append message UID {message.uid} to mailbox '{target_mailbox}': {data}")

    def _move_message(self, message: MessageData, target_mailbox: str, *, create_missing_mailboxes: bool) -> bool:
        self._ensure_target_mailbox(target_mailbox, create_missing_mailboxes)
        if self._mailbox_contains_duplicate(target_mailbox, message):
            LOGGER.info(
                "deleting UID %s from %s; matching message already exists in %s",
                message.uid,
                self.current_mailbox,
                target_mailbox,
            )
            self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
            return True

        if "MOVE" in self.capabilities:
            self._uid_command("move", message.uid, target_mailbox)
            return False

        self._uid_command("copy", message.uid, target_mailbox)
        self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
        return True

    def _mailbox_contains_duplicate(self, mailbox: str, message: MessageData) -> bool:
        search_terms = _candidate_search_terms(message)
        if not search_terms:
            return False

        original_mailbox = self.current_mailbox
        try:
            self.select_mailbox(mailbox)
            for candidate_uid in self._search_selected_mailbox_uids(search_terms):
                if mailbox == message.mailbox and candidate_uid == message.uid:
                    continue
                if _messages_match(message, self.fetch_message(candidate_uid)):
                    return True
            return False
        finally:
            if original_mailbox and self.current_mailbox != original_mailbox:
                self.select_mailbox(original_mailbox)

    def _search_selected_mailbox_uids(self, search_terms: list[str]) -> list[str]:
        connection = self._require_connection()
        status, data = connection.uid("search", None, *search_terms)
        if status != "OK":
            raise RuntimeError(f"failed to search mailbox '{self.current_mailbox}': {data}")

        raw = data[0] if data and data[0] else b""
        if isinstance(raw, bytes):
            return [item for item in raw.decode("ascii", errors="ignore").split() if item]
        return []

    def _store_flags(self, uid: str, *, operation: str, flags: list[str]) -> None:
        flag_list = _format_flag_list(flags)
        self._uid_command("store", uid, operation, flag_list)

    def _uid_command(self, command: str, *args: str) -> None:
        connection = self._require_connection()
        status, data = connection.uid(command, *args)
        if status != "OK":
            raise RuntimeError(f"IMAP UID {command.upper()} failed for {args}: {data}")

    def _require_connection(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        if not self.connection:
            raise RuntimeError("IMAP session is not connected")
        return self.connection


def _parse_flags(metadata: bytes) -> list[str]:
    text = metadata.decode("utf-8", errors="ignore")
    match = re.search(r"FLAGS \((.*?)\)", text)
    if not match or not match.group(1).strip():
        return []
    return [item for item in match.group(1).split() if item]


def _parse_size(metadata: bytes) -> int:
    text = metadata.decode("utf-8", errors="ignore")
    match = re.search(r"RFC822\.SIZE (\d+)", text)
    if not match:
        return 0
    return int(match.group(1))


def _parse_internal_date(metadata: bytes) -> datetime:
    text = metadata.decode("utf-8", errors="ignore")
    match = re.search(r'INTERNALDATE "([^"]+)"', text)
    if not match:
        return datetime.now(timezone.utc)
    return datetime.strptime(match.group(1), "%d-%b-%Y %H:%M:%S %z")


def _extract_body_text(message: EmailMessage) -> str:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            content = _decode_part(part)
            if content_type == "text/plain":
                text_parts.append(content)
            elif content_type == "text/html":
                html_parts.append(_html_to_text(content))
    else:
        content = _decode_part(message)
        if message.get_content_type() == "text/html":
            html_parts.append(_html_to_text(content))
        else:
            text_parts.append(content)

    return "\n".join(text_parts or html_parts)


def _decode_part(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        payload = str(part.get_payload()).encode("utf-8", errors="ignore")
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _html_to_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    squashed = re.sub(r"\s+", " ", without_tags)
    return unescape(squashed).strip()


def _has_attachments(message: EmailMessage) -> bool:
    return any(part.get_content_disposition() == "attachment" for part in message.walk())


def _header_value(message: EmailMessage, header_name: str) -> str | None:
    value = message.get(header_name, "")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _candidate_search_terms(message: MessageData) -> list[str]:
    message_id = _header_value(message.email_message, "Message-ID")
    if message_id:
        return ["HEADER", "Message-ID", message_id]

    terms: list[str] = []
    for header_name in ("Date", "From", "Subject", "To", "Cc"):
        header_value = _header_value(message.email_message, header_name)
        if header_value:
            terms.extend(["HEADER", header_name, header_value])
    return terms


def _messages_match(first: MessageData, second: MessageData) -> bool:
    return _canonical_message_bytes(first.raw_message) == _canonical_message_bytes(second.raw_message)


def _canonical_message_bytes(raw_message: bytes) -> bytes:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    while COPY_ID_HEADER in message:
        del message[COPY_ID_HEADER]
    return message.as_bytes(policy=policy.SMTP)


def _format_flag_list(flags: list[str]) -> str | None:
    if not flags:
        return None
    if len(flags) == 1:
        return flags[0]
    return f"({' '.join(flags)})"
