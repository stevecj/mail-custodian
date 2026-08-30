# Thunderbird mailbox backups (separate profile + twice-daily snapshots)

Use this workflow to back up Thunderbird mailboxes with automation that is
isolated from the interactive Thunderbird RDP session.

## Why this is separate

- Interactive use continues to use `podwork/data/thunderbird_client/`.
- Backups use a different profile at
  `podwork/data/thunderbird_backup_profile/`.
- This avoids profile lock contention and reduces risk of corruption from
  concurrent access.

## What the backup run does

`podwork/run-thunderbird-backup.sh` performs two steps:

1. Start a short-lived, separate container and run Thunderbird in headless
   mode against the backup profile for a sync window (default: 300 seconds).
2. Create a snapshot of that backup profile at
   `podwork/data/thunderbird_backup_snapshots/<timestamp>/`.

Snapshots are deduplicated with `rsync --link-dest`, so unchanged files are
hard-linked instead of copied repeatedly.

## Duplicate minimization (simple approach)

This setup keeps duplication low with two simple rules:

- **Download minimization:** the backup profile persists across runs, so
  Thunderbird reuses what it already synced and typically downloads only new
  data.
- **Export/storage minimization:** snapshots hard-link unchanged files, so each
  run stores mostly changed data only.

This is intentionally simple and may still include some duplication that a
mailbox-aware export pipeline could eliminate.

## Prerequisites on VPS

- Rootless Podman (already required by this project)
- `rsync` installed (required for deduplicated snapshots)
- Existing `localhost/thunderbird_client:latest` image, or allow the script to
  build it automatically

## One-time setup

On the VPS, from `~/podwork`:

```bash
chmod +x run-thunderbird-backup.sh sync-thunderbird-backup-profile.sh install-thunderbird-backup-timer.sh
./install-thunderbird-backup-timer.sh
```

The installer creates:

- `~/.config/systemd/user/thunderbird-backup.service`
- `~/.config/systemd/user/thunderbird-backup.timer`

The timer schedule is:

- `02:15` local time
- `14:15` local time

You can trigger an immediate run with:

```bash
systemctl --user start thunderbird-backup.service
```

## Sync account configuration from the interactive profile

The backup profile is separate from the interactive RDP profile, so account
configuration is not updated automatically. To refresh the backup profile from
the interactive Thunderbird profile:

```bash
cd ~/podwork
./sync-thunderbird-backup-profile.sh
```

The sync script copies `data/thunderbird_client/` to
`data/thunderbird_backup_profile/`, excluding transient Thunderbird lock files.
If the interactive Thunderbird service/container is running, the script stops it
before copying and starts it again afterward. It also uses the same lock as
scheduled backups. If a backup is already running, the sync fails; if a
scheduled backup starts while the sync is running, that backup exits without
touching the profile.

## Useful environment variables

Set these before running the backup script/service if needed:

| Variable | Default | Purpose |
|-------------------------------------|----------------------|------------------------------------------------|
| `THUNDERBIRD_BACKUP_CONTAINER_USER` | current VPS username | Container user for backup sync runs            |
| `THUNDERBIRD_BACKUP_SYNC_SECONDS`   | `300`                | How long headless Thunderbird stays up to sync |
| `THUNDERBIRD_BACKUP_RETENTION`      | `60`                 | Number of snapshots to keep                    |
| `THUNDERBIRD_BACKUP_SKIP_SYNC`      | `0`                  | Set `1` to snapshot current backup profile without starting Thunderbird |

## Backup automation files

- `podwork/run-thunderbird-backup.sh`
- `podwork/sync-thunderbird-backup-profile.sh`
- `podwork/install-thunderbird-backup-timer.sh`

## Browse and retrieve backed-up messages

Use a snapshot as a Thunderbird profile copy, so the backup snapshot remains
unchanged.

1. On the VPS, list available snapshots:

```bash
cd ~/podwork/data/thunderbird_backup_snapshots
ls -1
```

2. Copy one snapshot into a restore profile:

```bash
mkdir -p ~/podwork/data/thunderbird_restore_profile
rsync -a --delete \
  ~/podwork/data/thunderbird_backup_snapshots/<timestamp>/ \
  ~/podwork/data/thunderbird_restore_profile/
```

3. Browse messages in Thunderbird:
- Recommended: copy `~/podwork/data/thunderbird_restore_profile/` to a local
  machine, then open it with Thunderbird Profile Manager.
- Read, search, or export messages from that profile as needed.

You can also retrieve raw mailbox files directly from a snapshot:
- IMAP data: `ImapMail/<server>/...`
- Local folders: `Mail/Local Folders/...`
- Message stores are typically mbox-style files (usually no extension), with
  `.msf` files as index metadata.
