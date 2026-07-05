from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from mail_custodian.engine import FilterEngine
from mail_custodian.models import (
    AccountConfig,
    ActionResult,
    Actions,
    ActionTarget,
    AppConfig,
    Criteria,
    MailboxCheckpoint,
    MessageData,
    Rule,
)
from mail_custodian.state import MailboxStateStore


class FakeSession:
    instances: dict[str, "FakeSession"] = {}

    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.selected_mailboxes: list[str] = []
        self.list_uids_calls: list[int | None] = []
        self.search_uids_calls: list[int | None] = []
        self.apply_calls: list[dict[str, object]] = []
        FakeSession.instances[account.name] = self

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def select_mailbox(self, mailbox: str) -> None:
        self.selected_mailboxes.append(mailbox)

    def get_mailbox_uidvalidity(self) -> int:
        return 999

    def list_uids(self, *, since_uid: int | None = None) -> list[str]:
        self.list_uids_calls.append(since_uid)
        if self.account.name == "source":
            return ["7"]
        return []

    def search_uids(self, criteria: Criteria, *, since_uid: int | None = None) -> list[str]:
        del criteria
        self.search_uids_calls.append(since_uid)
        return ["7"] if self.account.name == "source" else []

    def fetch_message(self, uid: str) -> MessageData:
        del uid
        message = EmailMessage()
        message["From"] = "alerts@example.com"
        message["To"] = "user@example.com"
        message["Subject"] = "Needs review"
        message.set_content("suspicious")
        raw_message = message.as_bytes()
        return MessageData(
            uid="7",
            mailbox="INBOX",
            sender=message["From"],
            to=message["To"],
            cc="",
            subject=message["Subject"],
            body_text="suspicious\n",
            size=len(raw_message),
            flags=frozenset(),
            internal_date=datetime.now(timezone.utc),
            has_attachments=False,
            email_message=message,
            raw_message=raw_message,
        )

    def apply_actions(
        self,
        message: MessageData,
        actions: Actions,
        *,
        create_missing_mailboxes: bool,
        dry_run: bool,
        copy_session: "FakeSession | None" = None,
        copy_create_missing_mailboxes: bool | None = None,
        move_session: "FakeSession | None" = None,
        move_create_missing_mailboxes: bool | None = None,
    ) -> ActionResult:
        self.apply_calls.append(
            {
                "message": message,
                "actions": actions,
                "create_missing_mailboxes": create_missing_mailboxes,
                "dry_run": dry_run,
                "copy_session": copy_session,
                "copy_create_missing_mailboxes": copy_create_missing_mailboxes,
                "move_session": move_session,
                "move_create_missing_mailboxes": move_create_missing_mailboxes,
            }
        )
        return ActionResult()

    def expunge(self) -> None:
        return None


def test_engine_routes_cross_account_action_to_target_session(monkeypatch) -> None:
    FakeSession.instances = {}
    monkeypatch.setattr("mail_custodian.engine.IMAPSession", FakeSession)

    config = AppConfig(
        log_level="INFO",
        accounts=(
            AccountConfig(
                name="source",
                host="imap.source.test",
                username="source-user",
                password="source-secret",
                mailbox_root="INBOX",
                mailbox_delimiter=".",
                create_missing_mailboxes=False,
                rules=(
                    Rule(
                        name="copy elsewhere",
                        mailbox="@root",
                        criteria=Criteria(),
                        actions=Actions(copy_to=ActionTarget(mailbox="@root/Review/Spam", account="review")),
                    ),
                ),
            ),
            AccountConfig(
                name="review",
                host="imap.review.test",
                username="review-user",
                password="review-secret",
                mailbox_root="Mail",
                mailbox_delimiter=".",
                create_missing_mailboxes=True,
                rules=(
                    Rule(
                        name="noop",
                        mailbox="@root",
                        criteria=Criteria(sender=("nobody",)),
                        actions=Actions(mark_read=True),
                    ),
                ),
            ),
        ),
    )

    assert FilterEngine(config).run() == 0

    source_session = FakeSession.instances["source"]
    review_session = FakeSession.instances["review"]
    assert source_session.selected_mailboxes == ["INBOX"]
    assert source_session.apply_calls[0]["copy_session"] is review_session
    assert source_session.apply_calls[0]["copy_create_missing_mailboxes"] is True
    assert source_session.apply_calls[0]["actions"].copy_to == ActionTarget(mailbox="Mail.Review.Spam", account="review")


def test_engine_persists_mailbox_checkpoint(monkeypatch, tmp_path: Path) -> None:
    FakeSession.instances = {}
    monkeypatch.setattr("mail_custodian.engine.IMAPSession", FakeSession)
    store = MailboxStateStore(tmp_path / "checkpoints.json")
    store.put("source", "INBOX", MailboxCheckpoint(uidvalidity=999, last_uid=6))
    store.save()

    config = AppConfig(
        log_level="INFO",
        accounts=(
            AccountConfig(
                name="source",
                host="imap.source.test",
                username="source-user",
                password="source-secret",
                rules=(
                    Rule(
                        name="local rule",
                        mailbox="INBOX",
                        criteria=Criteria(),
                        actions=Actions(mark_read=True),
                    ),
                ),
            ),
        ),
    )

    assert FilterEngine(config, checkpoint_store=store).run() == 0

    source_session = FakeSession.instances["source"]
    assert source_session.list_uids_calls == [6]
    assert source_session.search_uids_calls == [6]
    reloaded = MailboxStateStore(tmp_path / "checkpoints.json")
    assert reloaded.get("source", "INBOX") == MailboxCheckpoint(uidvalidity=999, last_uid=7)
