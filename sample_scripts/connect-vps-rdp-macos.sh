#!/usr/bin/env bash
set -euo pipefail

VPS_USER="${VPS_USER:-myuser}"
VPS_HOST="${VPS_HOST:-vps.example.com}"
LOCAL_PORT="${LOCAL_PORT:-3389}"
REMOTE_HOST="${REMOTE_HOST:-127.0.0.1}"
REMOTE_PORT="${REMOTE_PORT:-3389}"
RDP_USER="${RDP_USER:-myuser}"

REMMINA_BIN="${REMMINA_BIN:-}"
if [[ -z "${REMMINA_BIN}" ]]; then
    if command -v remmina >/dev/null 2>&1; then
        REMMINA_BIN="$(command -v remmina)"
    elif [[ -x "/Applications/Remmina.app/Contents/MacOS/remmina" ]]; then
        REMMINA_BIN="/Applications/Remmina.app/Contents/MacOS/remmina"
    else
        echo "Remmina not found. Set REMMINA_BIN or install Remmina." >&2
        exit 1
    fi
fi

ssh -fN -L "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" "${VPS_USER}@${VPS_HOST}"

"${REMMINA_BIN}" -c "rdp://${RDP_USER}@127.0.0.1:${LOCAL_PORT}"
