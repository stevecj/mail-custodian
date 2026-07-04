from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import AccountConfig, Actions, ActionTarget, AppConfig, Criteria, Rule


class ConfigError(ValueError):
    pass


def load_config(paths: list[str]) -> AppConfig:
    if not paths:
        raise ConfigError("at least one --config path is required")

    merged: dict[str, Any] = {}
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        document = _load_document(path, seen=set())
        merged = _merge_dicts(merged, document)

    return _build_app_config(merged)


def _load_document(path: Path, seen: set[Path]) -> dict[str, Any]:
    if path in seen:
        raise ConfigError(f"config include cycle detected at {path}")
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    seen = set(seen)
    seen.add(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a YAML mapping: {path}")

    includes = data.pop("includes", [])
    if includes and not isinstance(includes, list):
        raise ConfigError(f"'includes' must be a list in {path}")

    merged: dict[str, Any] = {}
    for include_name in includes:
        if not isinstance(include_name, str):
            raise ConfigError(f"include entries must be strings in {path}")
        include_path = (path.parent / include_name).resolve()
        merged = _merge_dicts(merged, _load_document(include_path, seen))

    return _merge_dicts(merged, data)


def _merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in incoming.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = [*merged[key], *value]
        else:
            merged[key] = value
    return merged


def _build_app_config(data: dict[str, Any]) -> AppConfig:
    accounts_data = data.get("accounts")
    if not isinstance(accounts_data, list) or not accounts_data:
        raise ConfigError("config must define a non-empty 'accounts' list")

    log_level = data.get("log_level", "INFO")
    if not isinstance(log_level, str):
        raise ConfigError("'log_level' must be a string")

    accounts = tuple(_build_account(index, raw) for index, raw in enumerate(accounts_data, start=1))
    _validate_accounts(accounts)
    return AppConfig(log_level=log_level.upper(), accounts=accounts)


def _build_account(index: int, data: Any) -> AccountConfig:
    context = f"accounts[{index}]"
    mapping = _ensure_mapping(data, context)

    password = mapping.get("password")
    password_env = mapping.get("password_env")
    if password is None and password_env is None:
        raise ConfigError(f"{context} must set either 'password' or 'password_env'")
    if password is not None and not isinstance(password, str):
        raise ConfigError(f"{context}.password must be a string")
    if password_env is not None and not isinstance(password_env, str):
        raise ConfigError(f"{context}.password_env must be a string")
    if password is None:
        password = os.environ.get(password_env)
        if not password:
            raise ConfigError(f"{context}.password_env points to an unset environment variable: {password_env}")

    rules_data = mapping.get("rules")
    if not isinstance(rules_data, list) or not rules_data:
        raise ConfigError(f"{context} must define a non-empty 'rules' list")

    default_mailbox = mapping.get("default_mailbox", "INBOX")
    if not isinstance(default_mailbox, str):
        raise ConfigError(f"{context}.default_mailbox must be a string")

    rules = tuple(
        _build_rule(rule_index, raw_rule, default_mailbox)
        for rule_index, raw_rule in enumerate(rules_data, start=1)
    )

    return AccountConfig(
        name=_require_string(mapping, "name", context),
        host=_require_string(mapping, "host", context),
        username=_require_string(mapping, "username", context),
        password=password,
        port=_optional_int(mapping.get("port"), f"{context}.port", default=993),
        ssl=_optional_bool(mapping.get("ssl"), f"{context}.ssl", default=True),
        timeout=_optional_int(mapping.get("timeout"), f"{context}.timeout", default=30),
        mailbox_root=_optional_string(mapping.get("mailbox_root"), f"{context}.mailbox_root", default="INBOX"),
        mailbox_delimiter=_optional_string(
            mapping.get("mailbox_delimiter"),
            f"{context}.mailbox_delimiter",
            default="/",
        ),
        default_mailbox=default_mailbox,
        create_missing_mailboxes=_optional_bool(
            mapping.get("create_missing_mailboxes"),
            f"{context}.create_missing_mailboxes",
            default=False,
        ),
        rules=rules,
    )


def _build_rule(index: int, data: Any, default_mailbox: str) -> Rule:
    context = f"rule[{index}]"
    mapping = _ensure_mapping(data, context)
    return Rule(
        name=_require_string(mapping, "name", context),
        mailbox=_string_or_default(mapping.get("mailbox"), default_mailbox, f"{context}.mailbox"),
        criteria=_build_criteria(_ensure_mapping(mapping.get("criteria", {}), f"{context}.criteria")),
        actions=_build_actions(_ensure_mapping(mapping.get("actions", {}), f"{context}.actions"), context),
    )


def _build_criteria(data: dict[str, Any]) -> Criteria:
    match_mode = data.get("match", "all")
    if match_mode not in {"all", "any"}:
        raise ConfigError("criteria.match must be 'all' or 'any'")

    raw_headers = data.get("header_contains", {})
    if not isinstance(raw_headers, dict):
        raise ConfigError("criteria.header_contains must be a mapping")
    header_contains = {
        str(header): _string_list(values, f"criteria.header_contains.{header}")
        for header, values in raw_headers.items()
    }

    return Criteria(
        match=match_mode,
        sender=_string_list(data.get("from"), "criteria.from"),
        to=_string_list(data.get("to"), "criteria.to"),
        cc=_string_list(data.get("cc"), "criteria.cc"),
        subject_contains=_string_list(data.get("subject_contains"), "criteria.subject_contains"),
        body_contains=_string_list(data.get("body_contains"), "criteria.body_contains"),
        header_contains=header_contains,
        seen=_optional_bool(data.get("seen"), "criteria.seen"),
        flagged=_optional_bool(data.get("flagged"), "criteria.flagged"),
        answered=_optional_bool(data.get("answered"), "criteria.answered"),
        has_attachments=_optional_bool(data.get("has_attachments"), "criteria.has_attachments"),
        older_than_days=_optional_int(data.get("older_than_days"), "criteria.older_than_days"),
        younger_than_days=_optional_int(data.get("younger_than_days"), "criteria.younger_than_days"),
        size_larger_than=_optional_int(data.get("size_larger_than"), "criteria.size_larger_than"),
        size_smaller_than=_optional_int(data.get("size_smaller_than"), "criteria.size_smaller_than"),
    )


def _build_actions(data: dict[str, Any], context: str) -> Actions:
    mark_read = _optional_bool(data.get("mark_read"), f"{context}.actions.mark_read", default=False)
    mark_unread = _optional_bool(data.get("mark_unread"), f"{context}.actions.mark_unread", default=False)
    if mark_read and mark_unread:
        raise ConfigError(f"{context}.actions cannot set both mark_read and mark_unread")

    actions = Actions(
        move_to=_build_action_target(data.get("move_to"), f"{context}.actions.move_to"),
        copy_to=_build_action_target(data.get("copy_to"), f"{context}.actions.copy_to"),
        mark_read=mark_read,
        mark_unread=mark_unread,
        add_flags=_string_list(data.get("add_flags"), f"{context}.actions.add_flags"),
        remove_flags=_string_list(data.get("remove_flags"), f"{context}.actions.remove_flags"),
        delete=_optional_bool(data.get("delete"), f"{context}.actions.delete", default=False),
        stop_processing=_optional_bool(
            data.get("stop_processing"),
            f"{context}.actions.stop_processing",
            default=False,
        ),
    )

    if not any(
        (
            actions.move_to,
            actions.copy_to,
            actions.mark_read,
            actions.mark_unread,
            actions.add_flags,
            actions.remove_flags,
            actions.delete,
            actions.stop_processing,
        )
    ):
        raise ConfigError(f"{context}.actions must define at least one action")

    return actions


def _validate_accounts(accounts: tuple[AccountConfig, ...]) -> None:
    account_names: set[str] = set()
    for account in accounts:
        if account.name in account_names:
            raise ConfigError(f"duplicate account name: {account.name}")
        account_names.add(account.name)

    for account_index, account in enumerate(accounts, start=1):
        for rule_index, rule in enumerate(account.rules, start=1):
            for action_name, target in (("move_to", rule.actions.move_to), ("copy_to", rule.actions.copy_to)):
                if target is None or target.account is None:
                    continue
                if target.account not in account_names:
                    raise ConfigError(
                        f"accounts[{account_index}].rules[{rule_index}].actions.{action_name}.account "
                        f"references unknown account '{target.account}'"
                    )


def _ensure_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping")
    return value


def _require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return value


def _optional_string(value: Any, context: str, default: str | None = None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context} must be a non-empty string")
    return value


def _build_action_target(value: Any, context: str) -> ActionTarget | None:
    if value is None:
        return None
    if isinstance(value, str):
        return ActionTarget(mailbox=value)
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a string or mapping")

    return ActionTarget(
        mailbox=_require_string(value, "mailbox", context),
        account=_optional_string(value.get("account"), f"{context}.account"),
    )


def _string_or_default(value: Any, default: str, context: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context} must be a non-empty string")
    return value


def _string_list(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        raise ConfigError(f"{context} must be a string or list of strings")

    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ConfigError(f"{context} entries must be non-empty strings")
        items.append(item)
    return tuple(items)


def _optional_bool(value: Any, context: str, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{context} must be a boolean")
    return value


def _optional_int(value: Any, context: str, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{context} must be an integer")
    return value
