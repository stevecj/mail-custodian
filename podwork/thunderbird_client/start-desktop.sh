#!/bin/bash
set -euo pipefail

container_user="${CONTAINER_USER:?CONTAINER_USER must be set}"
container_home="${CONTAINER_HOME:-/home/${container_user}}"
rdp_username="${RDP_USERNAME:-${container_user}}"

export HOME="${container_home}"
export USER="${container_user}"
export LOGNAME="${container_user}"
export RDP_USERNAME="${rdp_username}"
export XDG_RUNTIME_DIR=/run/user/"$(id -u)"
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=GNOME
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe

install -d -m 0700 \
    "${HOME}/.local/share/gnome-remote-desktop" \
    "${XDG_RUNTIME_DIR}"

tls_dir="${HOME}/.local/share/gnome-remote-desktop"
if [[ ! -s "${tls_dir}/tls.key" || ! -s "${tls_dir}/tls.crt" ]]; then
    openssl req -new -newkey rsa:3072 -days 730 -nodes -x509 \
        -subj "/CN=thunderbird-client" \
        -keyout "${tls_dir}/tls.key" \
        -out "${tls_dir}/tls.crt"
    chmod 0600 "${tls_dir}/tls.key"
fi

dbus-run-session -- bash -euo pipefail -c '
    eval "$(printf "%s" "${RDP_PASSWORD}" | gnome-keyring-daemon --login)"
    eval "$(gnome-keyring-daemon --start --components=secrets)"
    pipewire &
    pipewire_pid=$!
    for _ in {1..50}; do
        [[ -S "${XDG_RUNTIME_DIR}/pipewire-0" ]] && break
        sleep 0.1
    done
    [[ -S "${XDG_RUNTIME_DIR}/pipewire-0" ]]
    wireplumber &
    wireplumber_pid=$!

    mutter \
        --headless \
        --wayland \
        --no-x11 \
        --virtual-monitor=1920x1080 \
        --wayland-display=wayland-0 &
    mutter_pid=$!

    for _ in {1..100}; do
        if gdbus introspect --session \
            --dest org.gnome.Mutter.RemoteDesktop \
            --object-path /org/gnome/Mutter/RemoteDesktop >/dev/null 2>&1; then
            break
        fi
        if ! kill -0 "${mutter_pid}" 2>/dev/null; then
            echo "Mutter exited before its remote desktop service became ready." >&2
            wait "${mutter_pid}"
        fi
        sleep 0.1
    done
    gdbus introspect --session \
        --dest org.gnome.Mutter.RemoteDesktop \
        --object-path /org/gnome/Mutter/RemoteDesktop >/dev/null

    grdctl rdp set-port 3389
    grdctl rdp set-tls-key "${HOME}/.local/share/gnome-remote-desktop/tls.key"
    grdctl rdp set-tls-cert "${HOME}/.local/share/gnome-remote-desktop/tls.crt"
    grdctl rdp set-credentials "${RDP_USERNAME}" "${RDP_PASSWORD}"
    grdctl rdp disable-view-only
    grdctl rdp disable-port-negotiation
    # `grdctl rdp enable` also starts a user systemd unit, which containers do not use.
    gsettings set org.gnome.desktop.remote-desktop.rdp enable true
    gsettings set org.gnome.desktop.remote-desktop.rdp screen-share-mode "mirror-primary"

    export WAYLAND_DISPLAY=wayland-0
    export MOZ_ENABLE_WAYLAND=1

    # Watchdog: restart gnome-remote-desktop-daemon whenever it exits, until mutter dies.
    (
        trap '\''kill "${_grd_pid:-}" 2>/dev/null; exit'\'' TERM INT
        while kill -0 "${mutter_pid}" 2>/dev/null; do
            /usr/libexec/gnome-remote-desktop-daemon &
            _grd_pid=$!
            wait "${_grd_pid}" || true
            kill -0 "${mutter_pid}" 2>/dev/null || break
            echo "gnome-remote-desktop-daemon exited, restarting..." >&2
            sleep 1
        done
    ) &
    grd_watchdog_pid=$!

    thunderbird &
    thunderbird_pid=$!

    set +e
    wait -n "${mutter_pid}" "${thunderbird_pid}"
    status=$?
    kill "${mutter_pid}" "${thunderbird_pid}" "${grd_watchdog_pid}" "${pipewire_pid}" "${wireplumber_pid}" 2>/dev/null || true
    wait || true
    exit "${status}"
'
