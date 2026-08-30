#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context_dir="${script_dir}/thunderbird_client"
image="localhost/thunderbird_client:latest"
container="thunderbird_backup_sync"
data_root="${script_dir}/data"
profile_dir="${data_root}/thunderbird_backup_profile"
snapshots_dir="${data_root}/thunderbird_backup_snapshots"
lock_file="${data_root}/.thunderbird-backup.lock"

host_uid="$(id -u)"
host_gid="$(id -g)"
host_user="$(id -un)"

container_user="${THUNDERBIRD_BACKUP_CONTAINER_USER:-${THUNDERBIRD_CONTAINER_USER:-${host_user}}}"
container_home="/home/${container_user}"
sync_seconds="${THUNDERBIRD_BACKUP_SYNC_SECONDS:-300}"
retention="${THUNDERBIRD_BACKUP_RETENTION:-60}"
skip_sync="${THUNDERBIRD_BACKUP_SKIP_SYNC:-0}"

if [[ ! "${sync_seconds}" =~ ^[0-9]+$ ]]; then
    echo "THUNDERBIRD_BACKUP_SYNC_SECONDS must be an integer number of seconds." >&2
    exit 1
fi
if [[ ! "${retention}" =~ ^[0-9]+$ || "${retention}" -lt 1 ]]; then
    echo "THUNDERBIRD_BACKUP_RETENTION must be a positive integer." >&2
    exit 1
fi
if [[ "${skip_sync}" != "0" && "${skip_sync}" != "1" ]]; then
    echo "THUNDERBIRD_BACKUP_SKIP_SYNC must be 0 or 1." >&2
    exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required for deduplicated snapshots. Install rsync and retry." >&2
    exit 1
fi

mkdir -p "${profile_dir}" "${snapshots_dir}" "${data_root}"
exec 9>"${lock_file}"
if ! flock -n 9; then
    echo "Another backup or profile sync run is already in progress; skipping this backup run." >&2
    exit 0
fi

if ! podman image exists "${image}"; then
    podman build \
        --build-arg "CONTAINER_USER=${container_user}" \
        --build-arg "CONTAINER_UID=${host_uid}" \
        --build-arg "CONTAINER_GID=${host_gid}" \
        --tag "${image}" \
        "${context_dir}"
fi

if [[ "${skip_sync}" == "0" ]]; then
    podman rm --force --ignore "${container}" >/dev/null

    podman run --rm \
        --name "${container}" \
        --hostname thunderbird-backup-sync \
        --userns "keep-id:uid=${host_uid},gid=${host_gid}" \
        --user 0 \
        --cap-add SYS_ADMIN \
        --device /dev/fuse \
        --env "CONTAINER_USER=${container_user}" \
        --env "THUNDERBIRD_BACKUP_SYNC_SECONDS=${sync_seconds}" \
        --volume "${profile_dir}:${container_home}/.thunderbird:Z" \
        --entrypoint /bin/bash \
        "${image}" \
        -euo pipefail -c '
            container_user="${CONTAINER_USER:?CONTAINER_USER must be set}"
            sync_seconds="${THUNDERBIRD_BACKUP_SYNC_SECONDS:?THUNDERBIRD_BACKUP_SYNC_SECONDS must be set}"

            runuser --user "${container_user}" -- bash -euo pipefail -c "
                export MOZ_HEADLESS=1
                thunderbird --headless &
                thunderbird_pid=\$!
                sleep \"${sync_seconds}\"

                if kill -0 \"\${thunderbird_pid}\" 2>/dev/null; then
                    kill \"\${thunderbird_pid}\"
                    set +e
                    wait \"\${thunderbird_pid}\"
                    status=\$?
                    set -e
                    if [[ \"\${status}\" -ne 0 && \"\${status}\" -ne 143 ]]; then
                        exit \"\${status}\"
                    fi
                fi
            "
        '
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
snapshot_dir="${snapshots_dir}/${timestamp}"
latest_link="${snapshots_dir}/latest"
link_dest=""
if [[ -L "${latest_link}" ]]; then
    previous_rel="$(readlink "${latest_link}")"
    previous_abs="$(realpath -m "${snapshots_dir}/${previous_rel}")"
    if [[ -d "${previous_abs}" ]]; then
        link_dest="${previous_abs}"
    fi
fi

mkdir -p "${snapshot_dir}"
rsync_args=(-a --delete)
if [[ -n "${link_dest}" ]]; then
    rsync_args+=("--link-dest=${link_dest}")
fi
rsync "${rsync_args[@]}" "${profile_dir}/" "${snapshot_dir}/"
ln -sfn "${timestamp}" "${latest_link}"

mapfile -t snapshot_names < <(find "${snapshots_dir}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
snapshot_count="${#snapshot_names[@]}"
if (( snapshot_count > retention )); then
    remove_count=$((snapshot_count - retention))
    for old_snapshot in "${snapshot_names[@]:0:remove_count}"; do
        rm -rf -- "${snapshots_dir}/${old_snapshot}"
    done
fi

echo "Backup snapshot created at ${snapshot_dir}"
