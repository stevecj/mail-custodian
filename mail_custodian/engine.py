from __future__ import annotations

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
        for account in self.config.accounts:
            try:
                self._run_account(account)
            except Exception:
                LOGGER.exception("account '%s' failed", account.name)
                failures += 1
        return 1 if failures else 0

    def _run_account(self, account: AccountConfig) -> None:
        grouped_rules = _group_rules_by_mailbox(account.rules)

        with IMAPSession(account) as session:
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
