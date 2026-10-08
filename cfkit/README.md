# CFKit — Cloudflare Tunnel Manager

Web UI to list / create / edit / run / stop / delete cloudflared tunnels on **one Linux (systemd) machine**.
SQLite is the source of truth; CFKit renders `~/.cloudflared/config-<name>.yaml` and
`/etc/systemd/system/cloudflared-<name>.service` from it. Editing a tunnel rewrites the YAML and restarts
the service — the tunnel UUID and DNS records are never recreated.

## Install
    sudo ./install.sh          # asks for the admin password; creates /etc/cfkit/.env (mode 600)

Then, from your laptop:  `ssh -L 8787:127.0.0.1:8787 user@server`  and open http://localhost:8787

First time: click **Import existing** to load tunnels created by the old `*_tunnel_linux.sh` scripts.
After import, CFKit's DB is the truth: **hand edits to those YAML files are overwritten on the next Apply**
(a backup of the previous YAML is kept in the DB, last 20 per tunnel).

## Security (read this)
CFKit runs as **root** and can write systemd units. It binds 127.0.0.1 only. A password alone is not enough to
expose it to the internet: if you publish it through a tunnel, put **Cloudflare Access** in front, set
`CFKIT_HTTPS_ONLY=1` and `CFKIT_ALLOWED_HOSTS=<your.hostname>,127.0.0.1,localhost` in `/etc/cfkit/.env`.

## Limits
- `cert.pem` must already exist for each service user (CFKit never runs the interactive `cloudflared login`;
  the UI shows how to fix it).
- Removing a hostname or deleting a tunnel leaves its DNS record in Cloudflare (the CLI cannot delete DNS);
  the UI lists what to remove by hand.
- `originRequest` support is limited to `httpHostHeader` and `noTLSVerify`; other keys in imported files are
  dropped with a warning.
- A tunnel's service user is fixed at creation.

## Develop
    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/pytest -q

## Config (`/etc/cfkit/.env`)
`CFKIT_PASSWORD_HASH`, `CFKIT_SECRET`, `CFKIT_PORT` (8787), `CFKIT_HOST` (127.0.0.1), `CFKIT_DB`,
`CFKIT_HTTPS_ONLY` (`1` to mark the cookie Secure), `CFKIT_ALLOWED_HOSTS`.
