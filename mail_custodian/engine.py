from __future__ import annotations

from contextlib import ExitStack
import logging
from collections import defaultdict

from .imap_client import IMAPSession
from .models import AccountConfig, ActionTarget, Actions, AppConfig, MailboxCheckpoint, Rule, resolve_mailbox_name
from .state import MailboxStateStore

LOGGER = logging.getLogger(__name__)


class FilterEngine:
    def __init__(
        self,
        config: AppConfig,
        *,
        dry_run: bool = False,
        checkpoint_store: MailboxStateStore | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.checkpoint_store = checkpoint_store or MailboxStateStore()

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
        grouped_rules = _group_rules_by_mailbox(account, account.rules)
        session = _get_session(account, sessions=sessions, exit_stack=exit_stack)
        for mailbox, rules in grouped_rules.items():
            checkpointed_rules = [rule for rule in rules if rule.criteria.new_messages_only]
            session.select_mailbox(mailbox, need_uidvalidity=bool(checkpointed_rules))
            checkpoint = self.checkpoint_store.get(account.name, mailbox) if checkpointed_rules else None
            uidvalidity = session.get_mailbox_uidvalidity() if checkpointed_rules else None
            since_uid = (
                checkpoint.last_uid
                if checkpoint and uidvalidity is not None and checkpoint.uidvalidity == uidvalidity
                else None
            )
            pending_uids = session.list_uids(since_uid=since_uid) if checkpointed_rules else []
            blocked_uids: set[str] = set()
            expunge_needed = False
            if checkpointed_rules and checkpoint and checkpoint.uidvalidity != uidvalidity:
                LOGGER.info(
                    "UIDVALIDITY changed for account=%s mailbox=%s; rescanning checkpointed rules from the beginning",
                    account.name,
                    mailbox,
                )

            for rule in rules:
                LOGGER.info("account=%s mailbox=%s rule=%s", account.name, mailbox, rule.name)
                candidate_uids = session.search_uids(
                    rule.criteria,
                    since_uid=since_uid if rule.criteria.new_messages_only else None,
                )
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
                        _resolve_actions(rule.actions, account=account, accounts_by_name=accounts_by_name),
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
            if checkpointed_rules and not self.dry_run and uidvalidity is not None:
                last_uid = max((int(uid) for uid in pending_uids), default=(since_uid or 0))
                self.checkpoint_store.put(
                    account.name,
                    mailbox,
                    MailboxCheckpoint(uidvalidity=uidvalidity, last_uid=last_uid),
                )
                self.checkpoint_store.save()


def _group_rules_by_mailbox(account: AccountConfig, rules: tuple[Rule, ...]) -> dict[str, list[Rule]]:
    grouped: dict[str, list[Rule]] = defaultdict(list)
    for rule in rules:
        grouped[resolve_mailbox_name(account, rule.mailbox)].append(rule)
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


def _resolve_actions(
    actions: Actions,
    *,
    account: AccountConfig,
    accounts_by_name: dict[str, AccountConfig],
) -> Actions:
    return Actions(
        move_to=_resolve_action_target(actions.move_to, account=account, accounts_by_name=accounts_by_name),
        copy_to=_resolve_action_target(actions.copy_to, account=account, accounts_by_name=accounts_by_name),
        mark_read=actions.mark_read,
        mark_unread=actions.mark_unread,
        add_flags=actions.add_flags,
        remove_flags=actions.remove_flags,
        delete=actions.delete,
        stop_processing=actions.stop_processing,
    )


def _resolve_action_target(
    target: ActionTarget | None,
    *,
    account: AccountConfig,
    accounts_by_name: dict[str, AccountConfig],
) -> ActionTarget | None:
    if target is None:
        return None

    target_account = account if target.account is None else accounts_by_name[target.account]
    return ActionTarget(
        mailbox=resolve_mailbox_name(target_account, target.mailbox),
        account=target.account,
    )
