from __future__ import annotations

import textwrap
from pathlib import Path

from mail_custodian.config import find_config_warnings, load_config


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
                mailbox_root: Mail
                mailbox_delimiter: .
                create_missing_mailboxes: true
                rules:
                  - name: override rule
                    mailbox: "@root/Alerts"
                    criteria:
                      seen: false
                    actions:
                      copy_to:
                        account: shared
                        mailbox: "@root/Shared/Alerts"
                      forward_to:
                        - notify@example.net
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
    assert config.accounts[2].mailbox_root == "Mail"
    assert config.accounts[2].mailbox_delimiter == "."
    assert config.accounts[2].rules[0].mailbox == "@root/Alerts"
    assert config.accounts[2].rules[0].actions.copy_to is not None
    assert config.accounts[2].rules[0].actions.copy_to.account == "shared"
    assert config.accounts[2].rules[0].actions.copy_to.mailbox == "@root/Shared/Alerts"
    assert config.accounts[2].rules[0].actions.forward_to == ("notify@example.net",)


def test_load_config_expands_shared_rules_into_target_accounts(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            shared_rules:
              - name: shared spam quarantine
                accounts:
                  - personal
                  - work
                mailbox: "@root"
                criteria:
                  from: spammer@example.com
                actions:
                  copy_to:
                    mailbox: "@root/Spam"
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
                mailbox_root: INBOX
                mailbox_delimiter: .
                rules:
                  - name: local cleanup
                    criteria:
                      new_messages_only: true
                    actions:
                      mark_read: true
              - name: work
                host: imap.work.test
                username: work-user
                password: work-secret
                mailbox_root: Mail
                mailbox_delimiter: /
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config([str(tmp_path / "config.yaml")])

    assert [rule.name for rule in config.accounts[0].rules] == ["local cleanup", "shared spam quarantine"]
    assert config.accounts[0].rules[0].criteria.new_messages_only is True
    assert config.accounts[0].rules[1].mailbox == "@root"
    assert config.accounts[0].rules[1].actions.copy_to is not None
    assert config.accounts[0].rules[1].actions.copy_to.mailbox == "@root/Spam"
    assert [rule.name for rule in config.accounts[1].rules] == ["shared spam quarantine"]


def test_load_config_parses_rule_auto_flag(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
                rules:
                  - name: automatic rule
                    actions:
                      mark_read: true
                  - name: manual rule
                    auto: false
                    actions:
                      mark_unread: true
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config([str(tmp_path / "config.yaml")])

    assert config.accounts[0].rules[0].auto is True
    assert config.accounts[0].rules[1].auto is False


def test_load_config_expands_account_groups_with_merged_criteria(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
                groups:
                  - name: shopping
                    mailbox: "@root"
                    criteria:
                      from:
                        - store@example.com
                      new_messages_only: true
                    rules:
                      - name: move coupons
                        criteria:
                          subject_contains:
                            - coupon
                        actions:
                          move_to: "@root/Coupons"
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config([str(tmp_path / "config.yaml")])

    assert [rule.name for rule in config.accounts[0].rules] == ["move coupons (shopping)"]
    rule = config.accounts[0].rules[0]
    assert rule.mailbox == "@root"
    assert rule.criteria.new_messages_only is True
    assert rule.criteria.sender == ("store@example.com",)
    assert rule.criteria.subject_contains == ("coupon",)


def test_load_config_expands_shared_rule_groups_into_target_accounts(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            shared_rule_groups:
              - name: shopping
                accounts:
                  - personal
                  - work
                mailbox: "@root"
                criteria:
                  from:
                    - store@example.com
                rules:
                  - name: flag receipts
                    criteria:
                      subject_contains:
                        - receipt
                    actions:
                      add_flags:
                        - \\Flagged
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
              - name: work
                host: imap.work.test
                username: work-user
                password: work-secret
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config([str(tmp_path / "config.yaml")])

    for account in config.accounts:
        assert [rule.name for rule in account.rules] == ["flag receipts (shopping)"]
        rule = account.rules[0]
        assert rule.mailbox == "@root"
        assert rule.criteria.sender == ("store@example.com",)
        assert rule.criteria.subject_contains == ("receipt",)
        assert rule.actions.add_flags == ("\\Flagged",)


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


def test_load_config_rejects_unknown_shared_rule_account(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            shared_rules:
              - name: shared rule
                accounts:
                  - missing
                actions:
                  mark_read: true
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
            """
        ).strip(),
        encoding="utf-8",
    )

    try:
        load_config([str(tmp_path / "config.yaml")])
    except ValueError as exc:
        assert "shared_rules[1].accounts references unknown account 'missing'" in str(exc)
    else:
        raise AssertionError("expected unknown shared rule account to raise an error")


def test_load_config_rejects_unknown_shared_rule_group_account(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            shared_rule_groups:
              - name: shopping
                accounts:
                  - missing
                rules:
                  - name: flag receipts
                    actions:
                      mark_read: true
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
            """
        ).strip(),
        encoding="utf-8",
    )

    try:
        load_config([str(tmp_path / "config.yaml")])
    except ValueError as exc:
        assert "shared_rule_groups[1].accounts references unknown account 'missing'" in str(exc)
    else:
        raise AssertionError("expected unknown shared rule group account to raise an error")


def test_find_config_warnings_reports_duplicate_rule_names_and_likely_slow_rules(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: personal
                host: imap.personal.test
                username: personal-user
                password: personal-secret
                rules:
                  - name: duplicate name
                    criteria:
                      from: sender@example.com
                    actions:
                      mark_read: true
                  - name: duplicate name
                    criteria:
                      to: user@example.com
                    actions:
                      mark_unread: true
                groups:
                  - name: shopping
                    rules:
                      - name: duplicate name
                        criteria:
                          subject_contains:
                            - coupon
                        actions:
                          move_to: "@root/Coupons"
            """
        ).strip(),
        encoding="utf-8",
    )

    config = load_config([str(tmp_path / "config.yaml")])

    assert find_config_warnings(config) == [
        "account 'personal' has duplicate rule name 'duplicate name'",
        "account 'personal' rule 'duplicate name' in mailbox 'INBOX' is likely to be slow because it has no server-side narrowing and may scan every undeleted message",
        "account 'personal' rule 'duplicate name' in mailbox 'INBOX' is likely to be slow because it has no server-side narrowing and may scan every undeleted message",
        "account 'personal' rule 'duplicate name (shopping)' in mailbox 'INBOX' is likely to be slow because it has no server-side narrowing and may scan every undeleted message",
    ]


def test_load_config_builds_gmail_account_with_oauth(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: gmail
                provider: gmail
                username: person@gmail.com
                gmail_oauth:
                  client_id: desktop-client-id
                  client_secret_env: GMAIL_CLIENT_SECRET
                rules:
                  - name: mark receipts
                    criteria:
                      subject_contains:
                        - receipt
                    actions:
                      mark_read: true
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "desktop-client-secret")

    config = load_config([str(tmp_path / "config.yaml")])

    account = config.accounts[0]
    assert account.provider == "gmail"
    assert account.host == "imap.gmail.com"
    assert account.password is None
    assert account.gmail_oauth is not None
    assert account.gmail_oauth.client_secret == "desktop-client-secret"
    assert account.gmail_oauth.scope == "https://mail.google.com/"


def test_load_config_allows_gmail_authorization_config_without_rules(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: gmail
                provider: gmail
                username: person@gmail.com
                gmail_oauth:
                  client_id: desktop-client-id
                  client_secret_env: GMAIL_CLIENT_SECRET
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "desktop-client-secret")

    config = load_config([str(tmp_path / "config.yaml")], require_rules=False)

    assert config.accounts[0].rules == ()


def test_load_config_rejects_password_auth_for_gmail(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent(
            """
            accounts:
              - name: gmail
                provider: gmail
                username: person@gmail.com
                password: app-password
                gmail_oauth:
                  client_id: desktop-client-id
                  client_secret: desktop-client-secret
                rules:
                  - name: noop
                    actions:
                      mark_read: true
            """
        ).strip(),
        encoding="utf-8",
    )

    try:
        load_config([str(tmp_path / "config.yaml")])
    except ValueError as exc:
        assert "must not set 'password' or 'password_env' when provider is 'gmail'" in str(exc)
    else:
        raise AssertionError("expected Gmail password auth to raise an error")
