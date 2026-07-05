from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .models import AccountConfig, Actions, ActionTarget, AppConfig, Criteria, Rule


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class _SharedRuleSpec:
    accounts: tuple[str, ...]
    name: str
    mailbox: str | None
    criteria: Criteria
    actions: Actions


@dataclass(frozen=True)
class _SharedRuleGroupSpec:
    accounts: tuple[str, ...]
    name: str
    mailbox: str | None
    criteria_data: dict[str, Any]
    rules_data: tuple[dict[str, Any], ...]


def load_config(paths: list[str]) -> AppConfig:
    if not paths:
        raise ConfigError("at least one --config path is required")

    merged: dict[str, Any] = {}
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        document = _load_document(path, seen=set())
        merged = _merge_dicts(merged, document)

    return _build_app_config(merged)


def find_config_warnings(config: AppConfig) -> list[str]:
    warnings: list[str] = []
    for account in config.accounts:
        name_counts = Counter(rule.name for rule in account.rules)
        for rule_name in sorted(name for name, count in name_counts.items() if count > 1):
            warnings.append(
                f"account '{account.name}' has duplicate rule name '{rule_name}'"
            )

        for rule in account.rules:
            if _is_likely_slow_rule(rule.criteria):
                warnings.append(
                    f"account '{account.name}' rule '{rule.name}' in mailbox '{rule.mailbox}' "
                    "is likely to be slow because it has no server-side narrowing and may scan every undeleted message"
                )
    return warnings


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
    shared_rules_data = data.get("shared_rules", [])
    if shared_rules_data and not isinstance(shared_rules_data, list):
        raise ConfigError("'shared_rules' must be a list")
    shared_rule_groups_data = data.get("shared_rule_groups", [])
    if shared_rule_groups_data and not isinstance(shared_rule_groups_data, list):
        raise ConfigError("'shared_rule_groups' must be a list")

    log_level = data.get("log_level", "INFO")
    if not isinstance(log_level, str):
        raise ConfigError("'log_level' must be a string")

    accounts = tuple(_build_account(index, raw) for index, raw in enumerate(accounts_data, start=1))
    shared_rules = tuple(
        _build_shared_rule(index, raw_rule)
        for index, raw_rule in enumerate(shared_rules_data, start=1)
    )
    shared_rule_groups = tuple(
        _build_shared_rule_group(index, raw_group)
        for index, raw_group in enumerate(shared_rule_groups_data, start=1)
    )
    accounts = _apply_shared_rules(accounts, shared_rules)
    accounts = _apply_shared_rule_groups(accounts, shared_rule_groups)
    _validate_accounts(accounts)
    if not any(account.rules for account in accounts):
        raise ConfigError("config must define at least one rule across 'accounts', 'shared_rules', and 'shared_rule_groups'")
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

    rules_data = mapping.get("rules", [])
    if not isinstance(rules_data, list):
        raise ConfigError(f"{context}.rules must be a list")
    groups_data = mapping.get("groups", [])
    if not isinstance(groups_data, list):
        raise ConfigError(f"{context}.groups must be a list")

    default_mailbox = mapping.get("default_mailbox", "INBOX")
    if not isinstance(default_mailbox, str):
        raise ConfigError(f"{context}.default_mailbox must be a string")

    rules = [
        *_build_rule_list(rules_data, default_mailbox, f"{context}.rules"),
        *_build_group_rule_list(groups_data, default_mailbox, f"{context}.groups"),
    ]

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
        rules=tuple(rules),
    )


def _build_rule_list(rules_data: list[Any], default_mailbox: str, context: str) -> tuple[Rule, ...]:
    return tuple(
        _build_rule(rule_index, raw_rule, default_mailbox, context=context)
        for rule_index, raw_rule in enumerate(rules_data, start=1)
    )


def _build_rule(index: int, data: Any, default_mailbox: str, *, context: str = "rule") -> Rule:
    context = f"{context}[{index}]"
    mapping = _ensure_mapping(data, context)
    return Rule(
        name=_require_string(mapping, "name", context),
        mailbox=_string_or_default(mapping.get("mailbox"), default_mailbox, f"{context}.mailbox"),
        criteria=_build_criteria(_ensure_mapping(mapping.get("criteria", {}), f"{context}.criteria")),
        actions=_build_actions(_ensure_mapping(mapping.get("actions", {}), f"{context}.actions"), context),
    )


def _build_group_rule_list(groups_data: list[Any], default_mailbox: str, context: str) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for group_index, raw_group in enumerate(groups_data, start=1):
        group_context = f"{context}[{group_index}]"
        group_mapping = _ensure_mapping(raw_group, group_context)
        _require_string(group_mapping, "name", group_context)
        group_mailbox = _optional_string(group_mapping.get("mailbox"), f"{group_context}.mailbox")
        group_criteria = _ensure_mapping(group_mapping.get("criteria", {}), f"{group_context}.criteria")
        rules_data = group_mapping.get("rules")
        if not isinstance(rules_data, list) or not rules_data:
            raise ConfigError(f"{group_context}.rules must be a non-empty list")

        rules.extend(
            _build_group_member_rules(
                rules_data,
                group_name=_require_string(group_mapping, "name", group_context),
                default_mailbox=group_mailbox or default_mailbox,
                inherited_criteria=group_criteria,
                context=f"{group_context}.rules",
            )
        )
    return tuple(rules)


def _build_shared_rule(index: int, data: Any) -> _SharedRuleSpec:
    context = f"shared_rules[{index}]"
    mapping = _ensure_mapping(data, context)
    accounts = _string_list(mapping.get("accounts"), f"{context}.accounts")
    if not accounts:
        raise ConfigError(f"{context}.accounts must define at least one account name")
    if len(set(accounts)) != len(accounts):
        raise ConfigError(f"{context}.accounts must not contain duplicates")

    return _SharedRuleSpec(
        accounts=accounts,
        name=_require_string(mapping, "name", context),
        mailbox=_optional_string(mapping.get("mailbox"), f"{context}.mailbox"),
        criteria=_build_criteria(_ensure_mapping(mapping.get("criteria", {}), f"{context}.criteria")),
        actions=_build_actions(_ensure_mapping(mapping.get("actions", {}), f"{context}.actions"), context),
    )


def _build_shared_rule_group(index: int, data: Any) -> _SharedRuleGroupSpec:
    context = f"shared_rule_groups[{index}]"
    mapping = _ensure_mapping(data, context)
    accounts = _string_list(mapping.get("accounts"), f"{context}.accounts")
    if not accounts:
        raise ConfigError(f"{context}.accounts must define at least one account name")
    if len(set(accounts)) != len(accounts):
        raise ConfigError(f"{context}.accounts must not contain duplicates")

    _require_string(mapping, "name", context)
    group_mailbox = _optional_string(mapping.get("mailbox"), f"{context}.mailbox")
    group_criteria = _ensure_mapping(mapping.get("criteria", {}), f"{context}.criteria")
    rules_data = mapping.get("rules")
    if not isinstance(rules_data, list) or not rules_data:
        raise ConfigError(f"{context}.rules must be a non-empty list")

    validated_rules = tuple(
        _ensure_mapping(raw_rule, f"{context}.rules[{rule_index}]")
        for rule_index, raw_rule in enumerate(rules_data, start=1)
    )

    return _SharedRuleGroupSpec(
        accounts=accounts,
        name=_require_string(mapping, "name", context),
        mailbox=group_mailbox,
        criteria_data=group_criteria,
        rules_data=validated_rules,
    )


def _apply_shared_rules(
    accounts: tuple[AccountConfig, ...],
    shared_rules: tuple[_SharedRuleSpec, ...],
) -> tuple[AccountConfig, ...]:
    if not shared_rules:
        return accounts

    accounts_by_name = {account.name: account for account in accounts}
    expanded_rules: dict[str, list[Rule]] = {account.name: list(account.rules) for account in accounts}

    for index, shared_rule in enumerate(shared_rules, start=1):
        for account_name in shared_rule.accounts:
            account = accounts_by_name.get(account_name)
            if account is None:
                raise ConfigError(
                    f"shared_rules[{index}].accounts references unknown account '{account_name}'"
                )
            expanded_rules[account_name].append(
                Rule(
                    name=shared_rule.name,
                    mailbox=shared_rule.mailbox or account.default_mailbox,
                    criteria=shared_rule.criteria,
                    actions=shared_rule.actions,
                )
            )

    return tuple(replace(account, rules=tuple(expanded_rules[account.name])) for account in accounts)


def _apply_shared_rule_groups(
    accounts: tuple[AccountConfig, ...],
    shared_rule_groups: tuple[_SharedRuleGroupSpec, ...],
) -> tuple[AccountConfig, ...]:
    if not shared_rule_groups:
        return accounts

    accounts_by_name = {account.name: account for account in accounts}
    expanded_rules: dict[str, list[Rule]] = {account.name: list(account.rules) for account in accounts}

    for index, shared_group in enumerate(shared_rule_groups, start=1):
        for account_name in shared_group.accounts:
            account = accounts_by_name.get(account_name)
            if account is None:
                raise ConfigError(
                    f"shared_rule_groups[{index}].accounts references unknown account '{account_name}'"
                )
            expanded_rules[account_name].extend(
                _build_group_member_rules(
                    list(shared_group.rules_data),
                    group_name=shared_group.name,
                    default_mailbox=shared_group.mailbox or account.default_mailbox,
                    inherited_criteria=shared_group.criteria_data,
                    context=f"shared_rule_groups[{index}].rules",
                )
            )

    return tuple(replace(account, rules=tuple(expanded_rules[account.name])) for account in accounts)


def _build_group_member_rules(
    rules_data: list[Any],
    *,
    group_name: str,
    default_mailbox: str,
    inherited_criteria: dict[str, Any],
    context: str,
) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for rule_index, raw_rule in enumerate(rules_data, start=1):
        rule_context = f"{context}[{rule_index}]"
        rule_mapping = _ensure_mapping(raw_rule, rule_context)
        merged_criteria = _merge_dicts(inherited_criteria, _ensure_mapping(rule_mapping.get("criteria", {}), f"{rule_context}.criteria"))
        rules.append(
            Rule(
                name=f"{_require_string(rule_mapping, 'name', rule_context)} ({group_name})",
                mailbox=_string_or_default(rule_mapping.get("mailbox"), default_mailbox, f"{rule_context}.mailbox"),
                criteria=_build_criteria(merged_criteria),
                actions=_build_actions(_ensure_mapping(rule_mapping.get("actions", {}), f"{rule_context}.actions"), rule_context),
            )
        )
    return tuple(rules)


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
        new_messages_only=_optional_bool(data.get("new_messages_only"), "criteria.new_messages_only", default=False) or False,
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
        forward_to=_string_list(data.get("forward_to"), f"{context}.actions.forward_to"),
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
            actions.forward_to,
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


def _is_likely_slow_rule(criteria: Criteria) -> bool:
    if criteria.new_messages_only:
        return False
    if criteria.seen is not None or criteria.flagged is not None or criteria.answered is not None:
        return False
    if criteria.older_than_days is not None or criteria.younger_than_days is not None:
        return False
    return True
