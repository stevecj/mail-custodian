from __future__ import annotations

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
