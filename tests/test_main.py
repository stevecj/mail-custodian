from __future__ import annotations

import pytest

from mail_custodian import __version__
import mail_custodian.__main__
from mail_custodian.__main__ import DEFAULT_CONFIG_PATH, _parse_args, main
from mail_custodian.models import AccountConfig, AppConfig, GmailOAuthConfig


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
