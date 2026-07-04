from __future__ import annotations

import textwrap
from pathlib import Path

from mail_custodian.config import load_config


def test_load_config_merges_includes_and_multiple_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "shared.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: shared
                host: imap.shared.test
                username: shared-user
                password_env: SHARED_IMAP_PASSWORD
                rules:
                  - name: shared rule
                    criteria:
                      from: shared@example.com
                    actions:
                      mark_read: true
            """
        ).strip(),
        encoding="utf-8",
    )
    (tmp_path / "base.yaml").write_text(
        textwrap.dedent(
            """
            includes:
              - shared.yaml
            log_level: INFO
            accounts:
              - name: base
                host: imap.base.test
                username: base-user
                password: base-secret
                rules:
                  - name: base rule
                    criteria:
                      subject_contains: hello
                    actions:
                      move_to: Archive
            """
        ).strip(),
        encoding="utf-8",
    )
    (tmp_path / "override.yaml").write_text(
        textwrap.dedent(
            """
            log_level: DEBUG
            accounts:
              - name: override
                host: imap.override.test
                username: override-user
                password: override-secret
                create_missing_mailboxes: true
                rules:
                  - name: override rule
                    mailbox: Alerts
                    criteria:
                      seen: false
                    actions:
                      copy_to:
                        account: shared
                        mailbox: Shared/Alerts
            """
        ).strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("SHARED_IMAP_PASSWORD", "env-secret")
    config = load_config([str(tmp_path / "base.yaml"), str(tmp_path / "override.yaml")])

    assert config.log_level == "DEBUG"
    assert [account.name for account in config.accounts] == ["shared", "base", "override"]
    assert config.accounts[0].password == "env-secret"
    assert config.accounts[2].create_missing_mailboxes is True
    assert config.accounts[2].rules[0].mailbox == "Alerts"
    assert config.accounts[2].rules[0].actions.copy_to is not None
    assert config.accounts[2].rules[0].actions.copy_to.account == "shared"
    assert config.accounts[2].rules[0].actions.copy_to.mailbox == "Shared/Alerts"


def test_load_config_rejects_unknown_cross_account_target(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
                rules:
                  - name: move elsewhere
                    actions:
                      move_to:
                        account: archive
                        mailbox: Archive/Inbox
            """
        ).strip(),
        encoding="utf-8",
    )

    try:
        load_config([str(tmp_path / "config.yaml")])
    except ValueError as exc:
        assert "references unknown account 'archive'" in str(exc)
    else:
        raise AssertionError("expected unknown target account to raise an error")
