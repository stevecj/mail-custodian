# The `podwork/` directory

This directory is the VPS-side working tree for the Thunderbird container.

## Thunderbird client stack

`thunderbird_client/Containerfile` builds
`localhost/thunderbird_client:latest` from `docker.io/almalinux/10-base`.
It runs Thunderbird in a headless GNOME Wayland session and serves that session
through GNOME Remote Desktop (RDP).

Start/rebuild with:

```sh
./run-thunderbird-client.sh
```

The launcher script:

- prompts for the RDP password unless `THUNDERBIRD_RDP_PASSWORD` is set
- builds the image with user/UID/GID matching the host account running Podman
- runs rootless with `--userns keep-id`
- publishes RDP to `127.0.0.1:3389` by default
- stores Thunderbird profile/mail under `data/thunderbird_client` on the host
- passes the RDP password using a Podman secret

## Personalization knobs

You can customize runtime identity/port/bind address via environment variables:

- `THUNDERBIRD_CONTAINER_USER` (default: current VPS username)
- `THUNDERBIRD_RDP_USER` (default: same as container user)
- `THUNDERBIRD_RDP_PORT` (default: `3389`)
- `THUNDERBIRD_RDP_BIND_ADDR` (default: `127.0.0.1`)
- `THUNDERBIRD_RDP_PASSWORD` (required for non-interactive runs)

Example:

```sh
THUNDERBIRD_CONTAINER_USER=mailops \
THUNDERBIRD_RDP_USER=mailops \
THUNDERBIRD_RDP_PORT=3390 \
./run-thunderbird-client.sh
```

## Host requirements

- rootless Podman working for the VPS account
- `/dev/fuse` available on host

The launcher passes `/dev/fuse` and namespaced `SYS_ADMIN` because
GNOME Remote Desktop needs FUSE for clipboard virtual filesystem support.
