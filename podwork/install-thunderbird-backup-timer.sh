#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unit_dir="${HOME}/.config/systemd/user"
service_file="${unit_dir}/thunderbird-backup.service"
timer_file="${unit_dir}/thunderbird-backup.timer"

mkdir -p "${unit_dir}"

cat >"${service_file}" <<EOF
[Unit]
Description=Thunderbird backup sync and snapshot

[Service]
Type=oneshot
WorkingDirectory=${script_dir}
ExecStart=${script_dir}/run-thunderbird-backup.sh
EOF

cat >"${timer_file}" <<'EOF'
[Unit]
Description=Run Thunderbird backup twice daily

[Timer]
OnCalendar=*-*-* 02,14:15:00
Persistent=true
RandomizedDelaySec=5m

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now thunderbird-backup.timer

echo "Installed thunderbird-backup.timer (02:15 and 14:15 local time)."
echo "Run now with: systemctl --user start thunderbird-backup.service"
