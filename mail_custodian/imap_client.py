from __future__ import annotations

import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import unescape

from .gmail_oauth import build_xoauth2_response, refresh_access_token
from .mailer import forward_message
from .models import AccountConfig, ActionResult, Actions, ActionTarget, Criteria, MessageData, resolve_mailbox_name

LOGGER = logging.getLogger(__name__)
COPY_ID_HEADER = "X-Mail-Custodian-Id"


class IMAPSession:
    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.connection: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        self.capabilities: set[str] = set()
        self.current_mailbox: str | None = None
        self.current_uidvalidity: int | None = None
        self.mailbox_uid_horizons: dict[str, int] = {}
        self.mailbox_uidvalidities: dict[str, int] = {}
        self.appended_messages_by_mailbox: dict[str, list[bytes]] = {}
        self.search_result_cache: dict[tuple[str, tuple[str, ...]], tuple[str, ...]] = {}

    def __enter__(self) -> "IMAPSession":
        if self.account.ssl:
            connection = imaplib.IMAP4_SSL(self.account.host, self.account.port, timeout=self.account.timeout)
        else:
            connection = imaplib.IMAP4(self.account.host, self.account.port, timeout=self.account.timeout)

        if self.account.gmail_oauth is not None:
            access_token = refresh_access_token(self.account)
            xoauth2_response = build_xoauth2_response(self.account.username, access_token)

            def auth_callback(challenge: bytes) -> bytes:
                if challenge:
                    return b""
                return xoauth2_response

            _run_imap_command(
                "AUTHENTICATE",
                f"mechanism=XOAUTH2 user={self.account.username}",
                lambda: connection.authenticate("XOAUTH2", auth_callback),
            )
        else:
            if self.account.password is None:
                raise RuntimeError(f"account '{self.account.name}' does not have a password configured")
            _run_imap_command(
                "LOGIN",
                f"user={self.account.username}",
                lambda: connection.login(self.account.username, self.account.password),
            )
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
            _run_imap_command("LOGOUT", "", self.connection.logout)
        finally:
            self.connection = None

    def select_mailbox(self, mailbox: str, *, need_uidvalidity: bool = False) -> None:
        connection = self._require_connection()
        resolved_mailbox = resolve_mailbox_name(self.account, mailbox)
        if self.current_mailbox == resolved_mailbox:
            self._freeze_mailbox_snapshot(resolved_mailbox, need_uidvalidity=need_uidvalidity)
            if need_uidvalidity:
                self.current_uidvalidity = self.mailbox_uidvalidities.get(resolved_mailbox)
            return
        status, data = _run_imap_command(
            "SELECT",
            f"mailbox={resolved_mailbox}",
            lambda: connection.select(resolved_mailbox),
        )
        if status != "OK":
            raise RuntimeError(f"failed to select mailbox '{resolved_mailbox}': {data}")
        self.current_mailbox = resolved_mailbox
        self._freeze_mailbox_snapshot(resolved_mailbox, need_uidvalidity=need_uidvalidity)
        self.current_uidvalidity = self.mailbox_uidvalidities.get(resolved_mailbox) if need_uidvalidity else None

    def get_mailbox_uidvalidity(self) -> int:
        if self.current_uidvalidity is None:
            raise RuntimeError(f"UIDVALIDITY is unavailable for mailbox '{self.current_mailbox}'")
        return self.current_uidvalidity

    def list_uids(self, *, since_uid: int | None = None) -> list[str]:
        terms = ["ALL"]
        uid_range = self._selected_mailbox_uid_range(since_uid=since_uid)
        if uid_range == "":
            return []
        if uid_range is not None:
            terms.extend(["UID", uid_range])
        return self._search_selected_mailbox_uids(terms)

    def search_uids(self, criteria: Criteria, *, since_uid: int | None = None) -> list[str]:
        terms = self._build_search_terms(criteria)
        uid_range = self._selected_mailbox_uid_range(since_uid=since_uid)
        if uid_range == "":
            return []
        if uid_range is not None:
            terms.extend(["UID", uid_range])
        return self._search_selected_mailbox_uids(terms)

    def fetch_message(self, uid: str) -> MessageData:
        connection = self._require_connection()
        status, data = _run_imap_command(
            "UID FETCH",
            f"mailbox={self.current_mailbox} uid={uid}",
            lambda: connection.uid("fetch", uid, "(FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[])"),
        )
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
            mailbox=self.current_mailbox or resolve_mailbox_name(self.account, self.account.default_mailbox),
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
        copy_session: "IMAPSession | None" = None,
        copy_create_missing_mailboxes: bool | None = None,
        move_session: "IMAPSession | None" = None,
        move_create_missing_mailboxes: bool | None = None,
    ) -> ActionResult:
        uid = message.uid
        blocked = actions.stop_processing or bool(actions.move_to) or actions.delete
        expunge_needed = False
        copy_session = copy_session or self
        move_session = move_session or self

        if dry_run:
            LOGGER.info(
                "dry-run UID %s in %s: copy_to=%s move_to=%s forward_to=%s mark_read=%s mark_unread=%s add_flags=%s remove_flags=%s delete=%s",
                uid,
                self.current_mailbox,
                _format_action_target(actions.copy_to),
                _format_action_target(actions.move_to),
                list(actions.forward_to),
                actions.mark_read,
                actions.mark_unread,
                list(actions.add_flags),
                list(actions.remove_flags),
                actions.delete,
            )
            move_requires_delete = bool(actions.move_to) and (move_session is not self or "MOVE" not in self.capabilities)
            return ActionResult(
                expunge_needed=actions.delete or move_requires_delete,
                block_further_rules=blocked,
            )
        if actions.copy_to:
            copy_session._copy_message(
                message,
                actions.copy_to.mailbox,
                create_missing_mailboxes=create_missing_mailboxes
                if copy_session is self
                else bool(copy_create_missing_mailboxes),
            )
        if actions.forward_to:
            forward_message(self.account.username, actions.forward_to, message)

        if actions.move_to:
            if move_session is self:
                expunge_needed = self._move_message(
                    message,
                    actions.move_to.mailbox,
                    create_missing_mailboxes=create_missing_mailboxes,
                ) or expunge_needed
            else:
                expunge_needed = self._move_message_to_other_session(
                    message,
                    target_session=move_session,
                    target_mailbox=actions.move_to.mailbox,
                    create_missing_mailboxes=bool(move_create_missing_mailboxes),
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
        status, data = _run_imap_command(
            "EXPUNGE",
            f"mailbox={self.current_mailbox}",
            connection.expunge,
        )
        if status != "OK":
            raise RuntimeError(f"failed to expunge mailbox '{self.current_mailbox}': {data}")
        if self.current_mailbox is not None:
            self._invalidate_mailbox_search_cache(self.current_mailbox)

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

    def _ensure_target_mailbox(self, mailbox: str, create_missing_mailboxes: bool) -> str:
        connection = self._require_connection()
        resolved_mailbox = resolve_mailbox_name(self.account, mailbox)
        status, data = _run_imap_command(
            "LIST",
            f"mailbox={resolved_mailbox}",
            lambda: connection.list("", resolved_mailbox),
        )
        if status == "OK" and any(item for item in data if item):
            self._freeze_mailbox_snapshot(resolved_mailbox, need_uidvalidity=False)
            return resolved_mailbox
        if not create_missing_mailboxes:
            raise RuntimeError(f"target mailbox does not exist: {resolved_mailbox}")

        create_status, create_data = _run_imap_command(
            "CREATE",
            f"mailbox={resolved_mailbox}",
            lambda: connection.create(resolved_mailbox),
        )
        if create_status != "OK":
            raise RuntimeError(f"failed to create mailbox '{resolved_mailbox}': {create_data}")
        self._freeze_mailbox_snapshot(resolved_mailbox, need_uidvalidity=False)
        return resolved_mailbox

    def _copy_message(self, message: MessageData, target_mailbox: str, *, create_missing_mailboxes: bool) -> None:
        resolved_target_mailbox = self._ensure_target_mailbox(target_mailbox, create_missing_mailboxes)
        if self._mailbox_contains_duplicate(resolved_target_mailbox, message):
            LOGGER.info(
                "skipping copy of UID %s to %s; matching message already exists",
                message.uid,
                resolved_target_mailbox,
            )
            return

        self._append_message(resolved_target_mailbox, message)

    def _move_message(self, message: MessageData, target_mailbox: str, *, create_missing_mailboxes: bool) -> bool:
        resolved_target_mailbox = self._ensure_target_mailbox(target_mailbox, create_missing_mailboxes)
        if self._mailbox_contains_duplicate(resolved_target_mailbox, message):
            LOGGER.info(
                "deleting UID %s from %s; matching message already exists in %s",
                message.uid,
                self.current_mailbox,
                resolved_target_mailbox,
            )
            self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
            return True

        if "MOVE" in self.capabilities:
            self._uid_command("move", message.uid, resolved_target_mailbox)
            if self.current_mailbox is not None:
                self._invalidate_mailbox_search_cache(self.current_mailbox)
            self._invalidate_mailbox_search_cache(resolved_target_mailbox)
            return False

        self._uid_command("copy", message.uid, resolved_target_mailbox)
        self._invalidate_mailbox_search_cache(resolved_target_mailbox)
        self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
        return True

    def _move_message_to_other_session(
        self,
        message: MessageData,
        *,
        target_session: "IMAPSession",
        target_mailbox: str,
        create_missing_mailboxes: bool,
    ) -> bool:
        resolved_target_mailbox = target_session._ensure_target_mailbox(target_mailbox, create_missing_mailboxes)
        if target_session._mailbox_contains_duplicate(resolved_target_mailbox, message):
            LOGGER.info(
                "deleting UID %s from %s; matching message already exists in %s:%s",
                message.uid,
                self.current_mailbox,
                target_session.account.name,
                resolved_target_mailbox,
            )
            self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
            return True

        target_session._append_message(resolved_target_mailbox, message)
        self._store_flags(message.uid, operation="+FLAGS.SILENT", flags=["\\Deleted"])
        return True

    def _mailbox_contains_duplicate(self, mailbox: str, message: MessageData) -> bool:
        for appended_raw_message in self.appended_messages_by_mailbox.get(mailbox, []):
            if _canonical_message_bytes(appended_raw_message) == _canonical_message_bytes(message.raw_message):
                return True

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
        bounded_terms = self._bound_search_terms_to_current_horizon(search_terms)
        if bounded_terms is None:
            return []
        if self.current_mailbox is None:
            raise RuntimeError("mailbox must be selected before searching")

        cache_key = (self.current_mailbox, tuple(bounded_terms))
        cached = self.search_result_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        status, data = _run_imap_command(
            "UID SEARCH",
            f"mailbox={self.current_mailbox} terms={bounded_terms!r}",
            lambda: connection.uid("search", None, *bounded_terms),
        )
        if status != "OK":
            raise RuntimeError(f"failed to search mailbox '{self.current_mailbox}': {data}")

        raw = data[0] if data and data[0] else b""
        if isinstance(raw, bytes):
            result = tuple(item for item in raw.decode("ascii", errors="ignore").split() if item)
            self.search_result_cache[cache_key] = result
            return list(result)
        return []

    def _store_flags(self, uid: str, *, operation: str, flags: list[str]) -> None:
        flag_list = _format_flag_list(flags)
        self._uid_command("store", uid, operation, flag_list)
        if self.current_mailbox is not None:
            self._invalidate_mailbox_search_cache(self.current_mailbox)

    def _append_message(self, target_mailbox: str, message: MessageData) -> None:
        connection = self._require_connection()
        append_flags = _format_flag_list(sorted(message.flags))
        append_date = imaplib.Time2Internaldate(message.internal_date)
        status, data = _run_imap_command(
            "APPEND",
            f"mailbox={target_mailbox} flags={append_flags!r} size={len(message.raw_message)}",
            lambda: connection.append(target_mailbox, append_flags, append_date, message.raw_message),
        )
        if status != "OK":
            raise RuntimeError(f"failed to append message UID {message.uid} to mailbox '{target_mailbox}': {data}")
        self.appended_messages_by_mailbox.setdefault(target_mailbox, []).append(message.raw_message)
        self._invalidate_mailbox_search_cache(target_mailbox)

    def _uid_command(self, command: str, *args: str) -> None:
        connection = self._require_connection()
        status, data = _run_imap_command(
            f"UID {command.upper()}",
            f"mailbox={self.current_mailbox} args={args!r}",
            lambda: connection.uid(command, *args),
        )
        if status != "OK":
            raise RuntimeError(f"IMAP UID {command.upper()} failed for {args}: {data}")

    def _require_connection(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        if not self.connection:
            raise RuntimeError("IMAP session is not connected")
        return self.connection

    def mailbox_uid_horizon(self, mailbox: str) -> int:
        resolved_mailbox = resolve_mailbox_name(self.account, mailbox)
        horizon = self.mailbox_uid_horizons.get(resolved_mailbox)
        if horizon is None:
            raise RuntimeError(f"UID horizon is unavailable for mailbox '{resolved_mailbox}'")
        return horizon

    def _freeze_mailbox_snapshot(self, mailbox: str, *, need_uidvalidity: bool) -> None:
        if mailbox not in self.mailbox_uid_horizons:
            connection = self._require_connection()
            horizon = _read_uid_horizon(connection, mailbox)
            self.mailbox_uid_horizons[mailbox] = horizon
        if need_uidvalidity and mailbox not in self.mailbox_uidvalidities:
            connection = self._require_connection()
            self.mailbox_uidvalidities[mailbox] = _read_uidvalidity(connection, mailbox)

    def _selected_mailbox_uid_range(self, *, since_uid: int | None) -> str | None:
        if self.current_mailbox is None:
            return None
        horizon = self.mailbox_uid_horizons.get(self.current_mailbox)
        if horizon is None:
            return None
        start_uid = 1 if since_uid is None else since_uid + 1
        if start_uid > horizon:
            return ""
        return f"{start_uid}:{horizon}"

    def _bound_search_terms_to_current_horizon(self, search_terms: list[str]) -> list[str] | None:
        if self.current_mailbox is None:
            return list(search_terms)
        if "UID" in search_terms:
            return list(search_terms)

        horizon = self.mailbox_uid_horizons.get(self.current_mailbox)
        if horizon is None:
            return list(search_terms)
        if horizon < 1:
            return None
        return [*search_terms, "UID", f"1:{horizon}"]

    def _invalidate_mailbox_search_cache(self, mailbox: str) -> None:
        stale_keys = [key for key in self.search_result_cache if key[0] == mailbox]
        for key in stale_keys:
            del self.search_result_cache[key]


def _parse_flags(metadata: bytes) -> list[str]:
    text = metadata.decode("utf-8", errors="ignore")
    match = re.search(r"FLAGS \((.*?)\)", text)
    if not match or not match.group(1).strip():
        return []
    return [item for item in match.group(1).split() if item]


def _read_uidvalidity(
    connection: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    mailbox: str,
) -> int:
    status_map = _read_status_values(connection, mailbox, {"UIDVALIDITY"})
    status_uidvalidity = status_map.get("UIDVALIDITY")
    if status_uidvalidity is not None:
        return status_uidvalidity

    for raw_value in _uidvalidity_candidates(connection, mailbox):
        text = raw_value.decode("ascii", errors="ignore") if isinstance(raw_value, bytes) else str(raw_value)
        if text.isdigit():
            return int(text)

    raise RuntimeError(f"failed to read UIDVALIDITY for mailbox '{mailbox}'")


def _read_uid_horizon(
    connection: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    mailbox: str,
) -> int:
    status_map = _read_status_values(connection, mailbox, {"UIDNEXT"})
    uidnext = status_map.get("UIDNEXT")
    if uidnext is not None:
        return max(uidnext - 1, 0)

    if getattr(connection, "state", None) == "SELECTED":
        status, data = _run_imap_command(
            "UID SEARCH",
            f"mailbox={mailbox} terms={('ALL',)!r}",
            lambda: connection.uid("search", None, "ALL"),
        )
        if status != "OK":
            raise RuntimeError(f"failed to determine UID horizon for mailbox '{mailbox}': {data}")
        raw = data[0] if data and data[0] else b""
        if isinstance(raw, bytes):
            uids = [int(item) for item in raw.decode("ascii", errors="ignore").split() if item]
            return max(uids, default=0)
    raise RuntimeError(f"failed to determine UID horizon for mailbox '{mailbox}'")


def _uidvalidity_candidates(
    connection: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    mailbox: str,
) -> list[bytes | str]:
    del mailbox
    candidates: list[bytes | str] = []
    status, data = _run_imap_command(
        "RESPONSE",
        "code=UIDVALIDITY",
        lambda: connection.response("UIDVALIDITY"),
    )
    if status == "OK" and data:
        candidates.extend(item for item in data if item is not None)

    untagged = getattr(connection, "untagged_responses", {})
    if isinstance(untagged, dict):
        raw_values = untagged.get("UIDVALIDITY") or untagged.get(b"UIDVALIDITY")
        if isinstance(raw_values, list):
            candidates.extend(item for item in raw_values if item is not None)
    return candidates


def _read_status_values(
    connection: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    mailbox: str,
    keys: set[str],
) -> dict[str, int]:
    query = f"({' '.join(sorted(keys))})"
    status, data = _run_imap_command(
        "STATUS",
        f"mailbox={mailbox} query={query}",
        lambda: connection.status(mailbox, query),
    )
    if status != "OK":
        return {}
    return _parse_status_values(data, keys)


def _run_imap_command(command: str, detail: str, operation):
    suffix = f" {detail}" if detail else ""
    LOGGER.debug("IMAP command start: %s%s", command, suffix)
    result = operation()
    LOGGER.debug("IMAP command complete: %s%s -> %s", command, suffix, _summarize_imap_result(result))
    return result


def _summarize_imap_result(result: object) -> str:
    if isinstance(result, tuple) and len(result) == 2:
        status, data = result
        data_summary = f"{len(data)} item(s)" if isinstance(data, list) else repr(data)
        return f"status={status!r} data={data_summary}"
    return "ok"


def _parse_status_values(data: object, keys: set[str]) -> dict[str, int]:
    if not isinstance(data, list):
        return {}
    parsed: dict[str, int] = {}
    for item in data:
        if item is None:
            continue
        text = item.decode("ascii", errors="ignore") if isinstance(item, bytes) else str(item)
        for key in keys:
            match = re.search(rf"{key}\s+(\d+)", text)
            if match:
                parsed[key] = int(match.group(1))
    return parsed


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


def _format_action_target(target: ActionTarget | None) -> str | None:
    if target is None:
        return None
    if target.account is None:
        return target.mailbox
    return f"{target.account}:{target.mailbox}"
