# Thunderbird on a VPS (rootless Podman + SSH-tunneled RDP)

This project runs Thunderbird in a rootless Podman container on a Linux VPS and
accesses it from any local OS (Windows, macOS, or Linux) through an SSH tunnel
and RDP client.

## Architecture

- Thunderbird runs in a containerized GNOME Wayland session on the VPS.
- GNOME Remote Desktop serves that session on container port `3389`.
- The host publishes RDP on loopback (`127.0.0.1`), not on public interfaces.
- Local clients connect by tunneling `localhost:3389` to VPS `127.0.0.1:3389`.

## Repository layout

- `podwork/run-thunderbird-client.sh`: build/run launcher
- `podwork/thunderbird_client/Containerfile`: image definition
- `podwork/thunderbird_client/container-entrypoint.sh`: container bootstrap
- `podwork/thunderbird_client/start-desktop.sh`: GNOME/RDP/Thunderbird startup
- `podwork/data/thunderbird_client`: persistent Thunderbird profile/mail data on VPS
  (`data/` is intentionally not tracked in git)

## 1) Install required packages on VPS

Use a non-root VPS account to run Podman.

### AlmaLinux / RHEL-family

```bash
sudo dnf install -y podman openssh-server shadow-utils passwd
sudo systemctl enable --now sshd
```

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y podman openssh-server uidmap passwd
sudo systemctl enable --now ssh
```

## 2) Enable rootless Podman prerequisites

Verify subordinate UID/GID mappings for the VPS user:

```bash
id <vps-user>
grep "^<vps-user>:" /etc/subuid /etc/subgid
```

If missing, add them:

```bash
echo "<vps-user>:524288:65536" | sudo tee -a /etc/subuid /etc/subgid
```

Validate rootless mode:

```bash
podman info | sed -n '1,120p'
```

Look for `rootless: true`.

## 3) Confirm FUSE is available

This setup requires `/dev/fuse` on the VPS host.

```bash
ls -l /dev/fuse
```

## 4) Copy project files to VPS

From your local machine, copy `podwork/` to the VPS user home:

```bash
scp -r podwork <vps-user>@<vps-host>:~/
ssh <vps-user>@<vps-host> "chmod +x ~/podwork/run-thunderbird-client.sh"
ssh <vps-user>@<vps-host> "mkdir -p ~/podwork/data/thunderbird_client"
```

## 5) Personalize runtime settings

Set variables on the VPS before launch (or in shell profile/systemd env file):

```bash
export THUNDERBIRD_CONTAINER_USER=<container-unix-user>
export THUNDERBIRD_RDP_USER=<rdp-login-username>
export THUNDERBIRD_RDP_PORT=3389
export THUNDERBIRD_RDP_BIND_ADDR=127.0.0.1
```

Notes:

- `THUNDERBIRD_CONTAINER_USER` defaults to the current VPS username.
- `THUNDERBIRD_RDP_USER` defaults to `THUNDERBIRD_CONTAINER_USER`.
- Keep `THUNDERBIRD_RDP_BIND_ADDR=127.0.0.1` unless you intentionally want public RDP.

For non-interactive startup, also set:

```bash
export THUNDERBIRD_RDP_PASSWORD='<strong-password>'
```

## 6) Build and start Thunderbird container

Run on VPS:

```bash
cd ~/podwork
./run-thunderbird-client.sh
```

The launcher builds `localhost/thunderbird_client:latest`, recreates container
`thunderbird_client`, mounts persistent data at `~/.thunderbird`, and configures
RDP credentials from a Podman secret.

## 7) Verify service state on VPS

```bash
podman ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'
ss -ltn | grep ':3389'
```

Expected: RDP published on `127.0.0.1:<port>`.

## 8) Connect from local machine (Windows/macOS/Linux)

Create SSH tunnel from local machine:

```bash
ssh -L 3389:127.0.0.1:3389 <vps-user>@<vps-host>
```

Then open any RDP client to:

```text
127.0.0.1:3389
```

Log in using `THUNDERBIRD_RDP_USER` and the configured password.

## 9) Optional Windows helper files

- `mail-custodian-connect.ps1` launches SSH tunnel + `mstsc`
- `mail-custodian-via-ssh-tunnel.rdp` targets `127.0.0.1`

Edit host/user/port values in those files to match your VPS.

## 10) Thunderbird close-button protection

To reduce accidental closes while keeping menu Quit available:

1. In Thunderbird: **Settings -> General -> Config Editor**
2. Set `toolkit.legacyUserProfileCustomizations.stylesheets = true`
3. Edit `~/.thunderbird/<profile>/chrome/userChrome.css`
4. Add:

```css
.titlebar-close {
  display: none !important;
}
```

Restart Thunderbird.
