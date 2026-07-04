from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_config
from .engine import FilterEngine


def main() -> int:
    args = _parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    log_level = "DEBUG" if args.verbose else config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = FilterEngine(config, dry_run=args.dry_run)
    return engine.run()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mail-custodian",
        description="Apply YAML-defined IMAP filtering rules from cron or other schedulers.",
    )
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        help="Path to a YAML config file. Repeat to merge multiple files.",
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
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
