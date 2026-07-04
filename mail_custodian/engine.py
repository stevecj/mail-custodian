from __future__ import annotations

from contextlib import ExitStack
import logging
from collections import defaultdict

from .imap_client import IMAPSession
from .models import AccountConfig, AppConfig, Rule

LOGGER = logging.getLogger(__name__)


class FilterEngine:
    def __init__(self, config: AppConfig, *, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run

    def run(self) -> int:
        failures = 0
        accounts_by_name = {account.name: account for account in self.config.accounts}
        with ExitStack() as exit_stack:
            sessions: dict[str, IMAPSession] = {}
            for account in self.config.accounts:
                try:
                    self._run_account(account, accounts_by_name=accounts_by_name, sessions=sessions, exit_stack=exit_stack)
                except Exception:
                    LOGGER.exception("account '%s' failed", account.name)
                    failures += 1
        return 1 if failures else 0

    def _run_account(
        self,
        account: AccountConfig,
        *,
        accounts_by_name: dict[str, AccountConfig],
        sessions: dict[str, IMAPSession],
        exit_stack: ExitStack,
    ) -> None:
        grouped_rules = _group_rules_by_mailbox(account.rules)
        session = _get_session(account, sessions=sessions, exit_stack=exit_stack)
        for mailbox, rules in grouped_rules.items():
            session.select_mailbox(mailbox)
            blocked_uids: set[str] = set()
            expunge_needed = False

            for rule in rules:
                LOGGER.info("account=%s mailbox=%s rule=%s", account.name, mailbox, rule.name)
                candidate_uids = session.search_uids(rule.criteria)
                for uid in candidate_uids:
                    if uid in blocked_uids:
                        continue

                    message = session.fetch_message(uid)
                    if not rule.criteria.matches(message):
                        continue

                    LOGGER.info(
                        "matched UID %s in %s for rule %s (%s)",
                        uid,
                        mailbox,
                        rule.name,
                        message.subject,
                    )
                    result = session.apply_actions(
                        message,
                        rule.actions,
                        create_missing_mailboxes=account.create_missing_mailboxes,
                        dry_run=self.dry_run,
                        copy_session=_resolve_target_session(
                            account=account,
                            target_account_name=rule.actions.copy_to.account if rule.actions.copy_to else None,
                            accounts_by_name=accounts_by_name,
                            sessions=sessions,
                            exit_stack=exit_stack,
                        ),
                        copy_create_missing_mailboxes=_resolve_target_create_missing_mailboxes(
                            account=account,
                            target_account_name=rule.actions.copy_to.account if rule.actions.copy_to else None,
                            accounts_by_name=accounts_by_name,
                        ),
                        move_session=_resolve_target_session(
                            account=account,
                            target_account_name=rule.actions.move_to.account if rule.actions.move_to else None,
                            accounts_by_name=accounts_by_name,
                            sessions=sessions,
                            exit_stack=exit_stack,
                        ),
                        move_create_missing_mailboxes=_resolve_target_create_missing_mailboxes(
                            account=account,
                            target_account_name=rule.actions.move_to.account if rule.actions.move_to else None,
                            accounts_by_name=accounts_by_name,
                        ),
                    )
                    expunge_needed = expunge_needed or result.expunge_needed
                    if result.block_further_rules:
                        blocked_uids.add(uid)

            if expunge_needed and not self.dry_run:
                session.expunge()


def _group_rules_by_mailbox(rules: tuple[Rule, ...]) -> dict[str, list[Rule]]:
    grouped: dict[str, list[Rule]] = defaultdict(list)
    for rule in rules:
        grouped[rule.mailbox].append(rule)
    return dict(grouped)


def _get_session(
    account: AccountConfig,
    *,
    sessions: dict[str, IMAPSession],
    exit_stack: ExitStack,
) -> IMAPSession:
    session = sessions.get(account.name)
    if session is None:
        session = exit_stack.enter_context(IMAPSession(account))
        sessions[account.name] = session
    return session


def _resolve_target_session(
    *,
    account: AccountConfig,
    target_account_name: str | None,
    accounts_by_name: dict[str, AccountConfig],
    sessions: dict[str, IMAPSession],
    exit_stack: ExitStack,
) -> IMAPSession | None:
    if target_account_name is None or target_account_name == account.name:
        return None
    return _get_session(accounts_by_name[target_account_name], sessions=sessions, exit_stack=exit_stack)


def _resolve_target_create_missing_mailboxes(
    *,
    account: AccountConfig,
    target_account_name: str | None,
    accounts_by_name: dict[str, AccountConfig],
) -> bool | None:
    if target_account_name is None or target_account_name == account.name:
        return None
    return accounts_by_name[target_account_name].create_missing_mailboxes
