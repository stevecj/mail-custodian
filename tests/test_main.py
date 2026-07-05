from __future__ import annotations

import pytest

from mail_custodian import __version__
from mail_custodian.__main__ import DEFAULT_CONFIG_PATH, _parse_args


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
