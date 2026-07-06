from __future__ import annotations

import pytest

from mail_custodian import __version__
import mail_custodian.__main__
from mail_custodian.__main__ import DEFAULT_CONFIG_PATH, _parse_args, main
from mail_custodian.models import AccountConfig, Actions, AppConfig, Criteria, GmailOAuthConfig, Rule


def test_parse_args_uses_default_config_path(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian"])

    args = _parse_args()

    assert args.config == [DEFAULT_CONFIG_PATH]


def test_parse_args_replaces_default_config_when_explicit_config_is_given(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--config", "custom.yaml"])

    args = _parse_args()

    assert args.config == ["custom.yaml"]


def test_parse_args_collects_multiple_explicit_config_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["mail-custodian", "--config", "common.yaml", "--config", "personal.yaml"],
    )

    args = _parse_args()

    assert args.config == ["common.yaml", "personal.yaml"]


def test_parse_args_accepts_authorize_gmail_account(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--authorize-gmail", "personal-gmail"])

    args = _parse_args()

    assert args.authorize_gmail == "personal-gmail"


def test_parse_args_accepts_account_filter(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--account", "personal"])

    args = _parse_args()

    assert args.account == "personal"


def test_parse_args_accepts_auto_only(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--auto-only"])

    args = _parse_args()

    assert args.auto_only is True


def test_parse_args_collects_rule_patterns(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "receipts", "shopping-.*"])

    args = _parse_args()

    assert args.rule_patterns == ["receipts", "shopping-.*"]


def test_parse_args_version_prints_and_exits(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--version"])

    with pytest.raises(SystemExit) as excinfo:
        _parse_args()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"mail-custodian {__version__}\n"


def test_parse_args_help_uses_uppercase_option_descriptions(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        _parse_args()

    assert excinfo.value.code == 0
    help_output = capsys.readouterr().out
    assert "Show this help message and exit." in help_output
    assert "Show the program version and exit." in help_output


def test_main_authorize_gmail_loads_config_without_rules(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--authorize-gmail", "personal-gmail"])
    loaded: list[object] = []
    authorized: list[str] = []

    def fake_load_config(paths, *, require_rules: bool):
        loaded.append((paths, require_rules))
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal-gmail",
                    host="imap.gmail.com",
                    username="person@gmail.com",
                    provider="gmail",
                    gmail_oauth=GmailOAuthConfig(
                        client_id="desktop-client-id",
                        client_secret="desktop-client-secret",
                    ),
                ),
            ),
        )

    def fake_authorize_account(account, *, token_store):
        del token_store
        authorized.append(account.name)
        return "refresh-token"

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)
    monkeypatch.setattr(mail_custodian.__main__, "authorize_account", fake_authorize_account)

    assert main() == 0
    assert loaded == [([DEFAULT_CONFIG_PATH], False)]
    assert authorized == ["personal-gmail"]
    assert "Stored Gmail refresh token for account 'personal-gmail'." in capsys.readouterr().out


def test_main_filters_to_selected_account(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--account", "personal"])
    received_accounts: list[tuple[str, ...]] = []

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                ),
                AccountConfig(
                    name="work",
                    host="imap.work.test",
                    username="work-user",
                    password="work-secret",
                ),
            ),
        )

    class FakeEngine:
        def __init__(self, config, *, dry_run: bool) -> None:
            del dry_run
            received_accounts.append(tuple(account.name for account in config.accounts))

        def run(self) -> int:
            return 0

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)
    monkeypatch.setattr(mail_custodian.__main__, "FilterEngine", FakeEngine)

    assert main() == 0
    assert received_accounts == [("personal",)]


def test_main_reports_unknown_account_filter(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--account", "missing"])

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                ),
            ),
        )

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)

    assert main() == 2
    assert "Configuration error: unknown account: missing" in capsys.readouterr().err


def test_main_filters_rules_by_fullmatch_regex(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "flag .*", "move coupons"])
    received_rules: list[tuple[tuple[str, ...], ...]] = []

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                    rules=(
                        Rule(
                            name="flag receipts",
                            mailbox="INBOX",
                            auto=False,
                            criteria=Criteria(),
                            actions=Actions(mark_read=True),
                        ),
                        Rule(
                            name="move coupons",
                            mailbox="INBOX",
                            criteria=Criteria(),
                            actions=Actions(mark_read=True),
                        ),
                        Rule(
                            name="move coupons later",
                            mailbox="INBOX",
                            auto=False,
                            criteria=Criteria(),
                            actions=Actions(mark_read=True),
                        ),
                    ),
                ),
            ),
        )

    class FakeEngine:
        def __init__(self, config, *, dry_run: bool) -> None:
            del dry_run
            received_rules.append(tuple(tuple(rule.name for rule in account.rules) for account in config.accounts))

        def run(self) -> int:
            return 0

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)
    monkeypatch.setattr(mail_custodian.__main__, "FilterEngine", FakeEngine)

    assert main() == 0
    assert received_rules == [(("flag receipts", "move coupons"),)]


def test_main_runs_only_auto_rules_without_patterns(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian"])
    received_rules: list[tuple[tuple[str, ...], ...]] = []

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                    rules=(
                        Rule(
                            name="automatic rule",
                            mailbox="INBOX",
                            criteria=Criteria(),
                            actions=Actions(mark_read=True),
                        ),
                        Rule(
                            name="manual rule",
                            mailbox="INBOX",
                            auto=False,
                            criteria=Criteria(),
                            actions=Actions(mark_unread=True),
                        ),
                    ),
                ),
            ),
        )

    class FakeEngine:
        def __init__(self, config, *, dry_run: bool) -> None:
            del dry_run
            received_rules.append(tuple(tuple(rule.name for rule in account.rules) for account in config.accounts))

        def run(self) -> int:
            return 0

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)
    monkeypatch.setattr(mail_custodian.__main__, "FilterEngine", FakeEngine)

    assert main() == 0
    assert received_rules == [(("automatic rule",),)]


def test_main_auto_only_excludes_manual_rules_even_when_matched(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "--auto-only", "manual .*"])
    received_rules: list[tuple[tuple[str, ...], ...]] = []

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                    rules=(
                        Rule(
                            name="automatic rule",
                            mailbox="INBOX",
                            criteria=Criteria(),
                            actions=Actions(mark_read=True),
                        ),
                        Rule(
                            name="manual rule",
                            mailbox="INBOX",
                            auto=False,
                            criteria=Criteria(),
                            actions=Actions(mark_unread=True),
                        ),
                    ),
                ),
            ),
        )

    class FakeEngine:
        def __init__(self, config, *, dry_run: bool) -> None:
            del dry_run
            received_rules.append(tuple(tuple(rule.name for rule in account.rules) for account in config.accounts))

        def run(self) -> int:
            return 0

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)
    monkeypatch.setattr(mail_custodian.__main__, "FilterEngine", FakeEngine)

    assert main() == 0
    assert received_rules == [(("automatic rule",),)]


def test_main_reports_invalid_rule_regex(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["mail-custodian", "("])

    def fake_load_config(paths, *, require_rules: bool):
        del paths, require_rules
        return AppConfig(
            log_level="INFO",
            accounts=(
                AccountConfig(
                    name="personal",
                    host="imap.personal.test",
                    username="personal-user",
                    password="personal-secret",
                ),
            ),
        )

    monkeypatch.setattr(mail_custodian.__main__, "load_config", fake_load_config)

    assert main() == 2
    assert "Configuration error: invalid rule name regular expression '('" in capsys.readouterr().err
