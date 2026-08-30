#!/usr/bin/env bash
set -euo pipefail

VPS_USER="${VPS_USER:-myuser}"
VPS_HOST="${VPS_HOST:-vps.example.com}"
LOCAL_PORT="${LOCAL_PORT:-3389}"
REMOTE_HOST="${REMOTE_HOST:-127.0.0.1}"
REMOTE_PORT="${REMOTE_PORT:-3389}"
RDP_USER="${RDP_USER:-myuser}"

run_tunnel=false
run_rdp=false

while (($#)); do
    case "$1" in
        -t|--tunnel) run_tunnel=true ;;
        -r|--rdp) run_rdp=true ;;
        -h|--help)
            echo "Usage: $0 [--tunnel] [--rdp]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--tunnel] [--rdp]" >&2
            exit 2
            ;;
    esac
    shift
done

if ! "$run_tunnel" && ! "$run_rdp"; then
    run_tunnel=true
    run_rdp=true
fi

if "$run_tunnel"; then
    ssh -fN -L "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}" "${VPS_USER}@${VPS_HOST}"
fi

if "$run_rdp"; then
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

    "${REMMINA_BIN}" -c "rdp://${RDP_USER}@127.0.0.1:${LOCAL_PORT}"
fi
