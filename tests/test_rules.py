from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from mail_custodian.imap_client import _extract_body_text
from mail_custodian.models import Criteria, MessageData


def test_criteria_match_expected_message() -> None:
    message = EmailMessage()
    message["From"] = "Billing Team <billing@example.com>"
    message["To"] = "user@example.com"
    message["Cc"] = "ops@example.com"
    message["Subject"] = "Invoice for June"
    message["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=10))
    message["X-Priority"] = "High"
    message.set_content("Please see the attached invoice for your records.")
    message.add_attachment(b"pdf-data", maintype="application", subtype="pdf", filename="invoice.pdf")

    message_data = MessageData(
        uid="7",
        mailbox="INBOX",
        sender=message["From"],
        to=message["To"],
        cc=message["Cc"],
        subject=message["Subject"],
        body_text=_extract_body_text(message),
        size=4_096,
        flags=frozenset({"\\Seen"}),
        internal_date=datetime.now(timezone.utc) - timedelta(days=10),
        has_attachments=True,
        email_message=message,
    )

    criteria = Criteria(
        sender=("billing@example.com",),
        to=("user@example.com",),
        cc=("ops@",),
        subject_contains=("invoice",),
        body_contains=("attached invoice",),
        header_contains={"X-Priority": ("high",)},
        seen=True,
        has_attachments=True,
        older_than_days=7,
        size_larger_than=1000,
    )

    assert criteria.matches(message_data) is True


def test_any_match_mode_accepts_partial_match() -> None:
    message = EmailMessage()
    message["From"] = "Promo <promo@example.com>"
    message["Subject"] = "Weekly Update"
    message.set_content("Nothing suspicious here.")

    message_data = MessageData(
        uid="9",
        mailbox="INBOX",
        sender=message["From"],
        to="",
        cc="",
        subject=message["Subject"],
        body_text=_extract_body_text(message),
        size=256,
        flags=frozenset(),
        internal_date=datetime.now(timezone.utc),
        has_attachments=False,
        email_message=message,
    )

    criteria = Criteria(
        match="any",
        sender=("alerts@example.com",),
        subject_contains=("weekly",),
        body_contains=("unsubscribe",),
    )

    assert criteria.matches(message_data) is True
