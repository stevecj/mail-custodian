#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_root="${script_dir}/data"
source_profile_dir="${data_root}/thunderbird_client"
backup_profile_dir="${data_root}/thunderbird_backup_profile"
lock_file="${data_root}/.thunderbird-backup.lock"
client_container="thunderbird_client"
client_service="thunderbird-client.service"
restart_client=0
restart_method=""

container_is_running() {
    if ! command -v podman >/dev/null 2>&1; then
        return 1
    fi

    [[ "$(podman inspect --format '{{.State.Running}}' "${client_container}" 2>/dev/null || true)" == "true" ]]
}

client_service_is_active() {
    if ! command -v systemctl >/dev/null 2>&1; then
        return 1
    fi

    systemctl --user is-active --quiet "${client_service}" 2>/dev/null
}

restart_client_if_needed() {
    status=$?
    trap - EXIT

    if [[ "${restart_client}" == "1" ]]; then
        if [[ "${restart_method}" == "systemd" ]]; then
            echo "Restarting ${client_service}."
            systemctl --user start "${client_service}" || status=$?
        elif [[ "${restart_method}" == "podman" ]]; then
            echo "Restarting container ${client_container}."
            podman start "${client_container}" >/dev/null || status=$?
        fi
    fi

    exit "${status}"
}

trap restart_client_if_needed EXIT

if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required to sync the Thunderbird backup profile. Install rsync and retry." >&2
    exit 1
fi

mkdir -p "${data_root}"
exec 9>"${lock_file}"
if ! flock -n 9; then
    echo "A Thunderbird backup run is already in progress; not syncing the backup profile." >&2
    exit 1
fi

if [[ ! -d "${source_profile_dir}" ]]; then
    echo "Source Thunderbird profile directory does not exist: ${source_profile_dir}" >&2
    exit 1
fi

if [[ -z "$(find "${source_profile_dir}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Source Thunderbird profile directory is empty: ${source_profile_dir}" >&2
    exit 1
fi

if client_service_is_active; then
    restart_client=1
    restart_method="systemd"
    echo "Stopping ${client_service} before syncing the profile."
    systemctl --user stop "${client_service}"
elif container_is_running; then
    restart_client=1
    restart_method="podman"
    echo "Stopping container ${client_container} before syncing the profile."
    podman stop --time 30 "${client_container}" >/dev/null
fi

if container_is_running; then
    echo "Container ${client_container} is still running; not copying its profile." >&2
    exit 1
fi

mkdir -p "${backup_profile_dir}"
rsync -a --delete --delete-excluded \
    --exclude='.parentlock' \
    --exclude='lock' \
    --exclude='msgFilterRules.dat' \
    --exclude='rules.dat' \
    "${source_profile_dir}/" \
    "${backup_profile_dir}/"

echo "Backup Thunderbird profile synced from ${source_profile_dir} to ${backup_profile_dir}."
