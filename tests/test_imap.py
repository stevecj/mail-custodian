from __future__ import annotations

from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from mail_custodian.imap_client import COPY_ID_HEADER, IMAPSession
from mail_custodian.models import AccountConfig, Actions, ActionTarget, Criteria, MessageData


class FakeConnection:
    def __init__(self) -> None:
        self.created_mailboxes: set[str] = set()
        self.mailboxes: dict[str, list[dict[str, object]]] = {"INBOX": []}
        self.selected_mailbox: str | None = None
        self.uid_calls: list[tuple[str, tuple[str, ...]]] = []
        self.append_calls: list[tuple[str, str | None, str, bytes]] = []
        self._next_uid = 1

    def uid(self, command: str, *args):
        self.uid_calls.append((command.lower(), tuple("" if arg is None else str(arg) for arg in args)))
        command = command.lower()
        if command == "search":
            return self._uid_search(*args)
        if command == "fetch":
            return self._uid_fetch(*args)
        return "OK", [b""]

    def select(self, mailbox: str):
        self.selected_mailbox = mailbox
        self.mailboxes.setdefault(mailbox, [])
        return "OK", [b""]

    def list(self, _reference: str, mailbox: str):
        if mailbox in self.mailboxes or mailbox in self.created_mailboxes:
            return "OK", [f'() "/" "{mailbox}"'.encode()]
        return "OK", [None]

    def create(self, mailbox: str):
        self.created_mailboxes.add(mailbox)
        self.mailboxes.setdefault(mailbox, [])
        return "OK", [b"created"]

    def append(self, mailbox: str, flags: str | None, date_time: str, message: bytes):
        self.mailboxes.setdefault(mailbox, []).append(
            {
                "uid": str(self._next_uid),
                "flags": flags or "",
                "date_time": date_time,
                "message": message,
            }
        )
        self._next_uid += 1
        self.append_calls.append((mailbox, flags, date_time, message))
        return "OK", [b"appended"]

    def add_message(
        self,
        mailbox: str,
        message: bytes,
        *,
        uid: str | None = None,
        flags: str = "\\Seen",
        date_time: str = '"04-Jul-2026 03:00:00 +0000"',
    ) -> str:
        self.mailboxes.setdefault(mailbox, []).append(
            {
                "uid": uid or str(self._next_uid),
                "flags": flags,
                "date_time": date_time,
                "message": message,
            }
        )
        if uid is None:
            self._next_uid += 1
            return str(self._next_uid - 1)
        return uid

    def _uid_search(self, _charset, *criteria: str):
        if self.selected_mailbox is None:
            return "NO", [b"no mailbox selected"]
        records = self.mailboxes.get(self.selected_mailbox, [])
        matches: list[str] = []
        for record in records:
            message = BytesParser(policy=policy.default).parsebytes(record["message"])
            if _matches_search(message, criteria):
                matches.append(str(record["uid"]))
        return "OK", [" ".join(matches).encode()]

    def _uid_fetch(self, uid: str, _query: str):
        if self.selected_mailbox is None:
            return "NO", [b"no mailbox selected"]
        for record in self.mailboxes.get(self.selected_mailbox, []):
            if record["uid"] != uid:
                continue
            raw_message = record["message"]
            metadata = (
                f'FLAGS ({record["flags"]}) RFC822.SIZE {len(raw_message)} '
                f'INTERNALDATE "{record["date_time"]}"'
            ).encode()
            return "OK", [(metadata, raw_message)]
        return "NO", [b"message not found"]


def _matches_search(message: EmailMessage, criteria: tuple[str, ...]) -> bool:
    if not criteria:
        return True
    if len(criteria) % 3 != 0:
        return False

    for index in range(0, len(criteria), 3):
        if criteria[index] != "HEADER":
            return False
        header_name = criteria[index + 1]
        needle = criteria[index + 2].casefold()
        header_value = "\n".join(message.get_all(header_name, []))
        if needle not in header_value.casefold():
            return False
    return True


def _build_session(name: str = "test") -> IMAPSession:
    session = IMAPSession.__new__(IMAPSession)
    session.account = AccountConfig(
        name=name,
        host="imap.example.com",
        username="user",
        password="secret",
        rules=(),
    )
    session.connection = FakeConnection()
    session.capabilities = {"IMAP4REV1"}
    session.current_mailbox = "INBOX"
    return session


def _build_message(
    uid: str = "42",
    *,
    message_id: str | None = "<message-42@example.com>",
    subject: str = "Subject",
    body: str = "body",
    attachment: bytes | None = None,
) -> MessageData:
    email_message = EmailMessage()
    email_message["From"] = "sender@example.com"
    email_message["To"] = "user@example.com"
    email_message["Date"] = "Fri, 04 Jul 2026 03:00:00 +0000"
    email_message["Subject"] = subject
    if message_id:
        email_message["Message-ID"] = message_id
    email_message.set_content(body)
    if attachment is not None:
        email_message.add_attachment(
            attachment,
            maintype="application",
            subtype="octet-stream",
            filename="attachment.bin",
        )
    raw_message = email_message.as_bytes(policy=policy.SMTP)
    return MessageData(
        uid=uid,
        mailbox="INBOX",
        sender=email_message["From"],
        to=email_message["To"],
        cc="",
        subject=email_message["Subject"],
        body_text=f"{body}\n",
        size=len(raw_message),
        flags=frozenset({"\\Seen"}),
        internal_date=datetime.now(timezone.utc),
        has_attachments=False,
        email_message=email_message,
        raw_message=raw_message,
    )


def test_search_terms_include_flag_and_age_filters() -> None:
    session = _build_session()
    criteria = Criteria(seen=False, flagged=True, older_than_days=5, younger_than_days=2)
    terms = session._build_search_terms(criteria)

    assert terms[0] == "UNDELETED"
    assert "UNSEEN" in terms
    assert "FLAGGED" in terms
    assert "BEFORE" in terms
    assert "SINCE" in terms


def test_apply_actions_creates_and_updates_mailboxes() -> None:
    session = _build_session()
    message = _build_message()
    result = session.apply_actions(
        message,
        Actions(
            copy_to=ActionTarget(mailbox="Archive/Review"),
            move_to=ActionTarget(mailbox="Archive/Done"),
            mark_read=True,
            add_flags=("\\Flagged",),
            remove_flags=("$Junk",),
        ),
        create_missing_mailboxes=True,
        dry_run=False,
    )

    assert result.expunge_needed is True
    assert session.connection.created_mailboxes == {"Archive/Review", "Archive/Done"}
    assert session.connection.append_calls[0][0] == "Archive/Review"
    assert ("copy", ("42", "Archive/Done")) in session.connection.uid_calls
    assert ("store", ("42", "+FLAGS.SILENT", "\\Deleted")) in session.connection.uid_calls
    assert ("store", ("42", "+FLAGS.SILENT", "\\Seen")) in session.connection.uid_calls
    assert ("store", ("42", "+FLAGS.SILENT", "\\Flagged")) in session.connection.uid_calls
    assert ("store", ("42", "-FLAGS.SILENT", "$Junk")) in session.connection.uid_calls


def test_copy_to_is_idempotent_without_stamping_headers() -> None:
    session = _build_session()
    message = _build_message(message_id=None)

    session.apply_actions(
        message,
        Actions(copy_to=ActionTarget(mailbox="Archive/Review")),
        create_missing_mailboxes=True,
        dry_run=False,
    )
    session.apply_actions(
        message,
        Actions(copy_to=ActionTarget(mailbox="Archive/Review")),
        create_missing_mailboxes=True,
        dry_run=False,
    )

    assert len(session.connection.append_calls) == 1
    copied_message = BytesParser(policy=policy.default).parsebytes(session.connection.append_calls[0][3])
    assert COPY_ID_HEADER not in copied_message


def test_copy_to_does_not_treat_same_body_with_different_attachment_as_duplicate() -> None:
    session = _build_session()
    existing_message = _build_message(message_id=None, attachment=b"first")
    session.connection.add_message("Archive/Review", existing_message.raw_message)
    message = _build_message(message_id=None, attachment=b"second")

    session.apply_actions(
        message,
        Actions(copy_to=ActionTarget(mailbox="Archive/Review")),
        create_missing_mailboxes=True,
        dry_run=False,
    )

    assert len(session.connection.append_calls) == 1


def test_move_to_deletes_source_when_duplicate_exists_in_destination() -> None:
    session = _build_session()
    message = _build_message()
    session.connection.add_message("Archive/Done", message.raw_message)

    result = session.apply_actions(
        message,
        Actions(move_to=ActionTarget(mailbox="Archive/Done")),
        create_missing_mailboxes=True,
        dry_run=False,
    )

    assert result.expunge_needed is True
    assert ("copy", ("42", "Archive/Done")) not in session.connection.uid_calls
    assert ("move", ("42", "Archive/Done")) not in session.connection.uid_calls
    assert ("store", ("42", "+FLAGS.SILENT", "\\Deleted")) in session.connection.uid_calls


def test_copy_to_other_account_appends_message_on_target_session() -> None:
    source_session = _build_session("source")
    target_session = _build_session("review")
    message = _build_message()

    source_session.apply_actions(
        message,
        Actions(copy_to=ActionTarget(mailbox="Review/Spam", account="review")),
        create_missing_mailboxes=True,
        dry_run=False,
        copy_session=target_session,
        copy_create_missing_mailboxes=True,
    )

    assert target_session.connection.created_mailboxes == {"Review/Spam"}
    assert target_session.connection.append_calls[0][0] == "Review/Spam"
    assert not source_session.connection.uid_calls


def test_move_to_other_account_copies_then_deletes_source() -> None:
    source_session = _build_session("source")
    target_session = _build_session("archive")
    message = _build_message()

    result = source_session.apply_actions(
        message,
        Actions(move_to=ActionTarget(mailbox="Archive/Done", account="archive")),
        create_missing_mailboxes=True,
        dry_run=False,
        move_session=target_session,
        move_create_missing_mailboxes=True,
    )

    assert result.expunge_needed is True
    assert target_session.connection.created_mailboxes == {"Archive/Done"}
    assert target_session.connection.append_calls[0][0] == "Archive/Done"
    assert ("store", ("42", "+FLAGS.SILENT", "\\Deleted")) in source_session.connection.uid_calls
    assert ("copy", ("42", "Archive/Done")) not in source_session.connection.uid_calls
