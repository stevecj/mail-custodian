from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import mail_custodian.imap_client
from mail_custodian.imap_client import COPY_ID_HEADER, IMAPSession
from mail_custodian.models import AccountConfig, Actions, ActionTarget, Criteria, GmailOAuthConfig, MessageData


class FakeConnection:
    def __init__(self) -> None:
        self.created_mailboxes: set[str] = set()
        self.mailboxes: dict[str, list[dict[str, object]]] = {"INBOX": []}
        self.uidvalidity_by_mailbox: dict[str, int] = {"INBOX": 999}
        self.selected_mailbox: str | None = None
        self.response_uidvalidity_available = True
        self.uid_calls: list[tuple[str, tuple[str, ...]]] = []
        self.append_calls: list[tuple[str, str | None, str, bytes]] = []
        self._next_uid = 1
        self.status_calls: list[tuple[str, str]] = []

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
        self.uidvalidity_by_mailbox.setdefault(mailbox, 999)
        return "OK", [b""]

    def response(self, code: str):
        if code.upper() != "UIDVALIDITY" or self.selected_mailbox is None:
            return "NO", [None]
        if not self.response_uidvalidity_available:
            return "NO", [None]
        return "OK", [str(self.uidvalidity_by_mailbox[self.selected_mailbox]).encode()]

    def status(self, mailbox: str, query: str):
        self.status_calls.append((mailbox, query))
        if query != "(UIDVALIDITY)":
            return "NO", [b"unsupported"]
        uidvalidity = self.uidvalidity_by_mailbox.get(mailbox)
        if uidvalidity is None:
            return "NO", [b"missing"]
        return "OK", [f'"{mailbox}" (UIDVALIDITY {uidvalidity})'.encode()]

    def list(self, _reference: str, mailbox: str):
        if mailbox in self.mailboxes or mailbox in self.created_mailboxes:
            return "OK", [f'() "/" "{mailbox}"'.encode()]
        return "OK", [None]

    def create(self, mailbox: str):
        self.created_mailboxes.add(mailbox)
        self.mailboxes.setdefault(mailbox, [])
        self.uidvalidity_by_mailbox.setdefault(mailbox, 999)
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
        minimum_uid = 0
        header_terms: list[tuple[str, str]] = []
        index = 0
        while index < len(criteria):
            term = criteria[index]
            if term in {"ALL", "UNDELETED", "SEEN", "UNSEEN", "FLAGGED", "UNFLAGGED", "ANSWERED", "UNANSWERED"}:
                index += 1
                continue
            if term in {"BEFORE", "SINCE"}:
                index += 2
                continue
            if term == "UID":
                range_value = criteria[index + 1]
                start_text, _, _ = range_value.partition(":")
                minimum_uid = int(start_text)
                index += 2
                continue
            if term == "HEADER":
                header_terms.append((criteria[index + 1], criteria[index + 2].casefold()))
                index += 3
                continue
            return "NO", [f"unsupported search term {term!r}".encode()]

        matches: list[str] = []
        for record in records:
            if int(str(record["uid"])) < minimum_uid:
                continue
            message = BytesParser(policy=policy.default).parsebytes(record["message"])
            if _matches_search(message, tuple(header_terms)):
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

    def login(self, username: str, password: str) -> None:
        self.login_call = (username, password)

    def authenticate(self, mechanism: str, auth_callback) -> None:
        self.authenticate_call = (mechanism, auth_callback(b""))

    def logout(self) -> None:
        self.logged_out = True


def _matches_search(message: EmailMessage, criteria: tuple[tuple[str, str], ...]) -> bool:
    if not criteria:
        return True
    for header_name, needle in criteria:
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
    session.current_uidvalidity = None
    return session


def test_imap_session_uses_gmail_xoauth2_auth(monkeypatch) -> None:
    fake_connection = FakeConnection()
    fake_connection.capabilities = [b"IMAP4rev1", b"AUTH=XOAUTH2"]
    monkeypatch.setattr(mail_custodian.imap_client.imaplib, "IMAP4_SSL", lambda *args, **kwargs: fake_connection)
    monkeypatch.setattr(mail_custodian.imap_client, "refresh_access_token", lambda account: "access-token")

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

    with IMAPSession(account) as session:
        assert session.connection is fake_connection

    assert fake_connection.authenticate_call == (
        "XOAUTH2",
        b"user=person@gmail.com\x01auth=Bearer access-token\x01\x01",
    )


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


def test_list_uids_filters_by_uid_checkpoint() -> None:
    session = _build_session()
    session.connection.add_message("INBOX", _build_message(uid="1").raw_message, uid="1")
    session.connection.add_message("INBOX", _build_message(uid="2").raw_message, uid="2")
    session.connection.add_message("INBOX", _build_message(uid="3").raw_message, uid="3")
    session.current_mailbox = None
    session.select_mailbox("INBOX")

    assert session.list_uids(since_uid=1) == ["2", "3"]


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


def test_select_mailbox_resolves_root_mailbox_token() -> None:
    session = _build_session()
    session.account = replace(
        session.account,
        mailbox_root="Mail",
        mailbox_delimiter=".",
    )

    session.select_mailbox("@root/Review/Spam")

    assert session.current_mailbox == "Mail.Review.Spam"
    assert session.connection.selected_mailbox == "Mail.Review.Spam"
    assert session.current_uidvalidity is None


def test_select_mailbox_reads_uidvalidity_only_when_requested() -> None:
    session = _build_session()

    session.select_mailbox("INBOX", need_uidvalidity=False)

    assert session.current_uidvalidity is None
    assert session.connection.status_calls == []


def test_select_mailbox_falls_back_to_status_for_uidvalidity() -> None:
    session = _build_session()
    session.connection.response_uidvalidity_available = False

    session.select_mailbox("INBOX", need_uidvalidity=True)

    assert session.current_uidvalidity == 999
    assert session.connection.status_calls == [("INBOX", "(UIDVALIDITY)")]


def test_apply_actions_forwards_message(monkeypatch) -> None:
    session = _build_session()
    message = _build_message()
    forwarded: list[tuple[str, tuple[str, ...], MessageData]] = []

    def fake_forward(account_username: str, recipients: tuple[str, ...], forwarded_message: MessageData) -> None:
        forwarded.append((account_username, recipients, forwarded_message))

    monkeypatch.setattr(mail_custodian.imap_client, "forward_message", fake_forward)

    session.apply_actions(
        message,
        Actions(forward_to=("alerts@example.net", "audit@example.net")),
        create_missing_mailboxes=True,
        dry_run=False,
    )

    assert forwarded == [("user", ("alerts@example.net", "audit@example.net"), message)]


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
    target_session.account = replace(
        target_session.account,
        mailbox_root="Mail",
        mailbox_delimiter=".",
    )
    message = _build_message()

    source_session.apply_actions(
        message,
        Actions(copy_to=ActionTarget(mailbox="@root/Review/Spam", account="review")),
        create_missing_mailboxes=True,
        dry_run=False,
        copy_session=target_session,
        copy_create_missing_mailboxes=True,
    )

    assert target_session.connection.created_mailboxes == {"Mail.Review.Spam"}
    assert target_session.connection.append_calls[0][0] == "Mail.Review.Spam"
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
