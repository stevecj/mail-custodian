#!/usr/bin/env bash
set -euo pipefail

VPS_USER="${VPS_USER:-myuser}"
VPS_HOST="${VPS_HOST:-vps.example.com}"
LOCAL_PORT="${LOCAL_PORT:-3389}"
REMOTE_HOST="${REMOTE_HOST:-127.0.0.1}"
REMOTE_PORT="${REMOTE_PORT:-3389}"
RDP_USER="${RDP_USER:-myuser}"

if ! command -v remmina >/dev/null 2>&1; then
    echo "remmina is required (Linux example)." >&2
    exit 1
fi

if ! pgrep -f "ssh -N -L ${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT} ${VPS_USER}@${VPS_HOST}" >/dev/null 2>&1; then
    ssh -fN -L "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" "${VPS_USER}@${VPS_HOST}"
fi

remmina -c "rdp://${RDP_USER}@127.0.0.1:${LOCAL_PORT}"
