from __future__ import annotations

import shutil
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .models import MessageData


def forward_message(account_username: str, recipients: tuple[str, ...], message: MessageData) -> None:
    sendmail_path = shutil.which("sendmail")
    if not sendmail_path:
        raise RuntimeError("forwarding requires a sendmail-compatible binary in PATH")

    forward = EmailMessage()
    forward["From"] = account_username
    forward["To"] = ", ".join(recipients)
    forward["Subject"] = f"Fwd: {message.subject}" if message.subject else "Fwd:"
    forward["Date"] = formatdate(localtime=True)
    forward["Message-ID"] = make_msgid()
    forward.set_content(
        "Forwarded by Mail Custodian.\n\n"
        f"Original mailbox: {message.mailbox}\n"
        f"Original UID: {message.uid}\n"
    )
    forward.add_attachment(
        message.raw_message,
        maintype="message",
        subtype="rfc822",
        filename="forwarded-message.eml",
    )

    result = subprocess.run(
        [sendmail_path, "-oi", "-t"],
        input=forward.as_bytes(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"sendmail failed with exit code {result.returncode}: {stderr or 'no error output'}")
