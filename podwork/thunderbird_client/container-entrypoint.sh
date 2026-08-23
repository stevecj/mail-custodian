#!/bin/bash
set -euo pipefail

if [[ -r /run/secrets/rdp_password ]]; then
    RDP_PASSWORD="$(</run/secrets/rdp_password)"
fi
: "${RDP_PASSWORD:?An RDP password must be supplied as a Podman secret}"
export RDP_PASSWORD

container_user="${CONTAINER_USER:?CONTAINER_USER must be set}"
container_home="${CONTAINER_HOME:-/home/${container_user}}"
container_uid="$(id -u "${container_user}")"
container_gid="$(id -g "${container_user}")"

install -d -m 0700 -o "${container_uid}" -g "${container_gid}" /run/user/"${container_uid}"
chown -R "${container_uid}:${container_gid}" "${container_home}"

exec runuser --user "${container_user}" -- /usr/local/bin/start-desktop
