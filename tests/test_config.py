from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from email_organizer.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_merges_includes_and_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "shared.yaml").write_text(
                textwrap.dedent(
                    """
                    accounts:
                      - name: shared
                        host: imap.shared.test
                        username: shared-user
                        password_env: SHARED_IMAP_PASSWORD
                        rules:
                          - name: shared rule
                            criteria:
                              from: shared@example.com
                            actions:
                              mark_read: true
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "base.yaml").write_text(
                textwrap.dedent(
                    """
                    includes:
                      - shared.yaml
                    log_level: INFO
                    accounts:
                      - name: base
                        host: imap.base.test
                        username: base-user
                        password: base-secret
                        rules:
                          - name: base rule
                            criteria:
                              subject_contains: hello
                            actions:
                              move_to: Archive
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "override.yaml").write_text(
                textwrap.dedent(
                    """
                    log_level: DEBUG
                    accounts:
                      - name: override
                        host: imap.override.test
                        username: override-user
                        password: override-secret
                        create_missing_mailboxes: true
                        rules:
                          - name: override rule
                            mailbox: Alerts
                            criteria:
                              seen: false
                            actions:
                              add_flags:
                                - \\Flagged
                    """
                ).strip(),
                encoding="utf-8",
            )

            os.environ["SHARED_IMAP_PASSWORD"] = "env-secret"
            config = load_config([str(root / "base.yaml"), str(root / "override.yaml")])

        self.assertEqual("DEBUG", config.log_level)
        self.assertEqual(["shared", "base", "override"], [account.name for account in config.accounts])
        self.assertEqual("env-secret", config.accounts[0].password)
        self.assertTrue(config.accounts[2].create_missing_mailboxes)
        self.assertEqual("Alerts", config.accounts[2].rules[0].mailbox)


if __name__ == "__main__":
    unittest.main()
