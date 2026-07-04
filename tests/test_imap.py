from __future__ import annotations

from datetime import datetime, timezone
import unittest

from mail_custodian.imap_client import IMAPSession
from mail_custodian.models import AccountConfig, Actions, Criteria


class FakeConnection:
    def __init__(self) -> None:
        self.created_mailboxes: set[str] = set()
        self.uid_calls: list[tuple[str, tuple[str, ...]]] = []

    def uid(self, command: str, *args: str):
        self.uid_calls.append((command.lower(), tuple(args)))
        return "OK", [b""]

    def list(self, _reference: str, mailbox: str):
        if mailbox in self.created_mailboxes:
            return "OK", [f'() "/" "{mailbox}"'.encode()]
        return "OK", [None]

    def create(self, mailbox: str):
        self.created_mailboxes.add(mailbox)
        return "OK", [b"created"]


class ImapSessionTests(unittest.TestCase):
    def _build_session(self) -> IMAPSession:
        session = IMAPSession.__new__(IMAPSession)
        session.account = AccountConfig(
            name="test",
            host="imap.example.com",
            username="user",
            password="secret",
            rules=(),
        )
        session.connection = FakeConnection()
        session.capabilities = {"IMAP4REV1"}
        session.current_mailbox = "INBOX"
        return session

    def test_search_terms_include_flag_and_age_filters(self) -> None:
        session = self._build_session()
        criteria = Criteria(seen=False, flagged=True, older_than_days=5, younger_than_days=2)
        terms = session._build_search_terms(criteria)

        self.assertEqual("UNDELETED", terms[0])
        self.assertIn("UNSEEN", terms)
        self.assertIn("FLAGGED", terms)
        self.assertIn("BEFORE", terms)
        self.assertIn("SINCE", terms)

    def test_apply_actions_creates_and_updates_mailboxes(self) -> None:
        session = self._build_session()
        result = session.apply_actions(
            "42",
            Actions(
                copy_to="Archive/Review",
                move_to="Archive/Done",
                mark_read=True,
                add_flags=("\\Flagged",),
                remove_flags=("$Junk",),
            ),
            create_missing_mailboxes=True,
            dry_run=False,
        )

        self.assertTrue(result.expunge_needed)
        self.assertEqual({"Archive/Review", "Archive/Done"}, session.connection.created_mailboxes)
        self.assertIn(("copy", ("42", "Archive/Review")), session.connection.uid_calls)
        self.assertIn(("copy", ("42", "Archive/Done")), session.connection.uid_calls)
        self.assertIn(("store", ("42", "+FLAGS.SILENT", "\\Deleted")), session.connection.uid_calls)
        self.assertIn(("store", ("42", "+FLAGS.SILENT", "\\Seen")), session.connection.uid_calls)
        self.assertIn(("store", ("42", "+FLAGS.SILENT", "\\Flagged")), session.connection.uid_calls)
        self.assertIn(("store", ("42", "-FLAGS.SILENT", "$Junk")), session.connection.uid_calls)


if __name__ == "__main__":
    unittest.main()
