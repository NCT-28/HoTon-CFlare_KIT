# HoTon-CFlare_KIT

**CFKit** — a web UI to list / create / edit / run / stop / ReTunnel / delete Cloudflare Tunnels (`cloudflared`) on
**one Linux (systemd) machine**. It replaces the hand-edited `*_tunnel_linux.sh` scripts scattered across projects.

SQLite is the source of truth; CFKit renders `~/.cloudflared/config-<name>.yaml` and
`/etc/systemd/system/cloudflared-<name>.service` from it. Editing a tunnel rewrites the YAML and restarts the service —
the tunnel UUID and DNS records are not recreated (use **ReTunnel** when you want a new id).
Stack: FastAPI + SQLite + plain HTML/JS (no build step).

## Features
- **Tunnel list** grouped by project: status badge, hostnames as clickable links (`https://<host>`), UUID, user,
  Run / Stop / Restart / Edit / Logs / ReTunnel / Delete.
- **New / Edit popup** (80% of the screen): per-rule hostname, path, service port, host header, noTLSVerify; reorder
  with ↑/↓; tabs for rules, rendered YAML preview and a diff against the file on disk. Apply is blocked while there are errors.
- **Service = port only**: the cell shows a fixed `http://127.0.0.1:` and takes just the port. Rules with other
  services (e.g. `https://…`) keep a full URL input.
- **Default domain** (field in the cloudflared card, remembered in your browser): hostnames are typed as sub-domains —
  `cfkit` → `cfkit.f1p.info.vn`, `@` → the domain itself. Empty domain = type full hostnames.
- **Environment card**: cloudflared path/version, "new version available" (GitHub, cached 6 h), `cert.pem` per service
  user, Update / Fix cert.pem helpers.
- **Import existing**: loads tunnels created by the old `*_tunnel_linux.sh` scripts (warns about comments, unsupported
  keys and non-default `credentials-file`).
- **ReTunnel**: stop → delete the current tunnel id → create a new tunnel with the same name → overwrite DNS →
  rewrite config → start. Hostnames, rules and user stay; only the id changes.
- **Logs** modal (last 200 journal lines, follow mode).

## Install
```bash
sudo ./cfkit/install.sh        # asks for the admin password; creates /etc/cfkit/.env (mode 600); installs /opt/cfkit + cfkit.service
```
Then, from your laptop: `ssh -L 8787:127.0.0.1:8787 user@server` and open http://localhost:8787

Re-run `sudo ./cfkit/install.sh` to upgrade (keeps `/etc/cfkit/.env` and the database), then hard-refresh the page
(Ctrl+Shift+R). Status: `systemctl status cfkit` · logs: `journalctl -u cfkit -f`.

First time: click **Import existing**. After import, CFKit's DB is the truth: **hand edits to those YAML files are
overwritten on the next Apply** (the previous YAML is kept in the DB, last 20 per tunnel).

## What Edit does
Apply validates the rendered YAML with `cloudflared tunnel ingress validate`, backs up the old file, writes the new one,
creates DNS (`route dns`) only for **new** hostnames, then restarts the service and checks it stays `active` for ~3 s.
If anything fails after the file was replaced, the previous YAML and DB rows are restored and the service restarted.
A stopped tunnel is **not** started by an edit. Removed hostnames keep their DNS record (see Limits).

## Config (`/etc/cfkit/.env`)
| Variable | Default | Meaning |
|---|---|---|
| `CFKIT_PASSWORD_HASH` | — | argon2 hash of the admin password (set by `install.sh`) |
| `CFKIT_SECRET` | — | session signing key (set by `install.sh`) |
| `CFKIT_PORT` / `CFKIT_HOST` | `8787` / `127.0.0.1` | listen address |
| `CFKIT_DB` | `/var/lib/cfkit/cfkit.db` | SQLite file |
| `CFKIT_ALLOWED_HOSTS` | `127.0.0.1,localhost` | accepted `Host` headers (add your public hostname) |
| `CFKIT_HTTPS_ONLY` | off | `1` marks the session cookie Secure (use when served over HTTPS) |

## Security (read this)
CFKit runs as **root** and can write systemd units; it binds 127.0.0.1 only. A password alone is not enough to expose it
to the internet. If you publish it through a tunnel (e.g. `cfkit.f1p.info.vn`, see `cfkit_tunnel_linux.sh`), put
**Cloudflare Access** in front and set `CFKIT_ALLOWED_HOSTS=<your.hostname>,127.0.0.1,localhost` and `CFKIT_HTTPS_ONLY=1`,
then `systemctl restart cfkit`. Without `CFKIT_ALLOWED_HOSTS` the app answers **"Invalid host header"**.
Login attempts are rate-limited per client IP — behind a tunnel every client looks like `127.0.0.1`, so Access is the real gate.

## Limits
- `cert.pem` must already exist for each service user (CFKit never runs the interactive `cloudflared login`; the UI shows how).
- **DNS records are never deleted** (the cloudflared CLI cannot). After removing a hostname or deleting a tunnel, remove the
  CNAME by hand in the Cloudflare dashboard; the result lists which ones.
- Edit does not overwrite an existing DNS record for a new hostname (it warns "already exists"); ReTunnel does overwrite.
- Cloudflare tunnels are addressed by UUID; if your account has several tunnels with the same name, clean the extra ones by
  hand (`cloudflared tunnel list` / `cloudflared tunnel delete <id>`).
- `originRequest` support is limited to `httpHostHeader` and `noTLSVerify`; other keys in imported files are dropped (with a warning).
- A tunnel's name and service user are fixed after creation. Linux/systemd only.

## Repository layout
| Path | What |
|---|---|
| `cfkit/app/` | backend modules (validation, rendering, flows, API, auth) |
| `cfkit/static/` | web UI (`index.html`, `app.js`, `hostutil.js`, `style.css`) |
| `cfkit/tests/` | pytest suite + `fixtures/` golden YAML/unit files |
| `cfkit/install.sh` | installer / upgrader |
| `cfkit_tunnel_linux.sh` | standalone script that creates the CFKit tunnel (`cfkit.f1p.info.vn` → `:8787`) |
| `docs/superpowers/` | design spec, implementation plan, UI mockup |
| `CLAUDE.md` | guidance for Claude Code in this repo |

## Develop
```bash
cd cfkit
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q            # ~200 tests, fake command runner, no root or network needed
node --check static/app.js     # JS syntax (hostutil.js tests need node on PATH, skipped otherwise)
```

## License
See [`LICENSE`](LICENSE).
