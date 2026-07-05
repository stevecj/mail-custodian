from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, find_config_warnings, load_config
from .engine import FilterEngine
from .gmail_oauth import GmailOAuthError, authorize_account
from .state import GmailOAuthStore
from .state import StateError

DEFAULT_CONFIG_PATH = str(Path("~/.config/mail-custodian.yaml").expanduser())


def main() -> int:
    args = _parse_args()
    try:
        config = load_config(args.config, require_rules=not bool(args.authorize_gmail))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.authorize_gmail:
        try:
            account = _find_account(config, args.authorize_gmail)
            authorize_account(account, token_store=GmailOAuthStore())
            print(f"Stored Gmail refresh token for account '{account.name}'.")
            return 0
        except (GmailOAuthError, StateError) as exc:
            print(f"Gmail authorization error: {exc}", file=sys.stderr)
            return 2

    log_level = "DEBUG" if args.verbose else config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    for warning in find_config_warnings(config):
        logger.warning("%s", warning)

    try:
        engine = FilterEngine(config, dry_run=args.dry_run)
        return engine.run()
    except StateError as exc:
        print(f"State error: {exc}", file=sys.stderr)
        return 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        add_help=False,
        prog="mail-custodian",
        description="Apply YAML-defined IMAP filtering rules from cron or other schedulers.",
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show this help message and exit.",
    )
    parser.add_argument(
        "--authorize-gmail",
        metavar="ACCOUNT",
        help="Authorize a Gmail account and store its refresh token, then exit.",
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[DEFAULT_CONFIG_PATH],
        help=(
            "Path to a YAML config file. Repeat to merge multiple files. "
            f"Defaults to {DEFAULT_CONFIG_PATH}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log matches and planned actions without changing the IMAP server.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--version",
        action="version",
        help="Show the program version and exit.",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args()
    if args.config and args.config[0] == DEFAULT_CONFIG_PATH and len(args.config) > 1:
        args.config = args.config[1:]
    return args


def _find_account(config, name: str):
    for account in config.accounts:
        if account.name == name:
            return account
    raise ConfigError(f"unknown account: {name}")


if __name__ == "__main__":
    raise SystemExit(main())
