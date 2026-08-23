#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
context_dir="${script_dir}/thunderbird_client"
image=localhost/thunderbird_client:latest
container=thunderbird_client
secret=thunderbird_client_rdp_password
data_dir="${script_dir}/data/thunderbird_client"
host_port="${THUNDERBIRD_RDP_PORT:-3389}"
bind_addr="${THUNDERBIRD_RDP_BIND_ADDR:-127.0.0.1}"
host_uid="$(id -u)"
host_gid="$(id -g)"
host_user="$(id -un)"
container_user="${THUNDERBIRD_CONTAINER_USER:-${host_user}}"
rdp_user="${THUNDERBIRD_RDP_USER:-${container_user}}"
container_home="/home/${container_user}"

if [[ -z "${THUNDERBIRD_RDP_PASSWORD:-}" ]]; then
    if [[ ! -t 0 ]]; then
        echo "Set THUNDERBIRD_RDP_PASSWORD when running non-interactively." >&2
        exit 1
    fi
    read -r -s -p "RDP password for ${rdp_user}: " THUNDERBIRD_RDP_PASSWORD
    echo
    if [[ -z "${THUNDERBIRD_RDP_PASSWORD}" ]]; then
        echo "The RDP password must not be empty." >&2
        exit 1
    fi
fi

podman build \
    --build-arg "CONTAINER_USER=${container_user}" \
    --build-arg "CONTAINER_UID=${host_uid}" \
    --build-arg "CONTAINER_GID=${host_gid}" \
    --tag "${image}" \
    "${context_dir}"

podman rm --force --ignore "${container}"
mkdir -p "${data_dir}"
podman secret rm --ignore "${secret}" >/dev/null

password_file="$(mktemp)"
trap 'rm -f "${password_file}"' EXIT
chmod 0600 "${password_file}"
printf '%s' "${THUNDERBIRD_RDP_PASSWORD}" >"${password_file}"
podman secret create "${secret}" "${password_file}" >/dev/null

podman run --detach \
    --name "${container}" \
    --hostname thunderbird-client \
    --userns "keep-id:uid=${host_uid},gid=${host_gid}" \
    --user 0 \
    --publish "${bind_addr}:${host_port}:3389" \
    --cap-add SYS_ADMIN \
    --device /dev/fuse \
    --env "CONTAINER_USER=${container_user}" \
    --env "CONTAINER_HOME=${container_home}" \
    --env "RDP_USERNAME=${rdp_user}" \
    --secret "${secret},target=rdp_password" \
    --volume "${data_dir}:${container_home}/.thunderbird:Z" \
    "${image}"

echo "Thunderbird RDP is starting on ${bind_addr}:${host_port}; log in as ${rdp_user}."
