# CFKit — Cloudflare Tunnel Manager (Design)

Date: 2026-10-08

## 1. Goal

One web UI to list, create, edit, run/stop, and delete cloudflared tunnels on a single Linux machine.
It replaces per-project copies of `a_tunnel_linux.sh` / `b_tunnel_linux.sh`.

Success: edit a tunnel in a form, click Apply, and the systemd service runs with the new config —
without changing the tunnel UUID or its DNS records.

### Decisions (agreed)

| Topic | Decision |
|---|---|
| Scope | One machine; web and tunnels on the same host |
| Platform | Linux / systemd only (macOS launchd dropped) |
| Privilege | Web runs as root systemd service, binds `127.0.0.1`, single admin password |
| Source of truth | Web's own SQLite DB; YAML/unit files are rendered output |
| Stack | Python + FastAPI + SQLite + plain HTML/JS (no build step) |
| Apply mechanism | Shell out to `cloudflared` CLI and `systemctl`; render YAML/unit in Python |
| Edit semantics | Rewrite YAML + restart; never delete/recreate the tunnel |

Non-goals (v1): multi-host management, Cloudflare HTTP API/token, macOS, WARP/private-network
routes, `originRequest` options beyond `httpHostHeader` and `noTLSVerify`, tunnel templates.

## 2. Data model (SQLite)

- `tunnels`: `id`, `name` (unique; = cloudflared tunnel name), `tunnel_uuid` (nullable until
  created), `user_name` (service user, e.g. `toannc` / `root`), `project` (free-text tag),
  `note`, `created_at`, `updated_at`.
- `ingress_rules`: `id`, `tunnel_id`, `position` (order matters), `hostname`, `path` (nullable),
  `service` (e.g. `http://127.0.0.1:5500`), `http_host_header` (nullable),
  `no_tls_verify` (bool).
- `config_backups`: `tunnel_id`, `yaml_text`, `created_at`. One row written before every overwrite.

The catch-all `- service: http_status:404` is not stored; the renderer always appends it.
Runtime status (active/inactive/failed) is read from `systemctl`, not stored.

Derived paths (same as existing scripts):
- `{home(user_name)}/.cloudflared/config-<name>.yaml`
- `/etc/systemd/system/cloudflared-<name>.service`
- credentials: `{home}/.cloudflared/<uuid>.json`

DNS hostnames to route = distinct `hostname` values across a tunnel's rules.

## 3. Import

On first run and via an "Import existing" button:
1. Scan `{home}/.cloudflared/config-*.yaml` for each configured user.
2. Parse `tunnel:`, `credentials-file:`, `ingress:` into rows.
3. Match `cloudflared-<name>.service` and read `User=`.
4. Show a preview (already-known names greyed out); write to DB only after confirm.

Import never modifies files or restarts services.

## 4. Backend modules

| Module | Responsibility |
|---|---|
| `db.py` | SQLite access, migrations |
| `render.py` | Pure: tunnel + rules → YAML text; tunnel → systemd unit text |
| `importer.py` | Parse existing YAML/units into the data model; no writes outside DB |
| `runner.py` | Injectable command runner (argument lists, never shell strings) |
| `cf.py` | Wrapper over `cloudflared` (`tunnel create/delete/route dns/ingress validate`); UUID extracted with the same regex as the scripts |
| `svc.py` | Wrapper over `systemctl` and `journalctl` |
| `auth.py` | Login, session, CSRF, rate limit |
| `api.py` / `main.py` | Routes, static files |

## 5. Apply flows

All run as root, serialized per tunnel by a lock. Each returns a step list
(`ok` / `failed` + stderr tail); raw exceptions never reach the UI.

- **Create:** validate → `cf.create` → store UUID → `route dns` per hostname → write YAML + unit
  → `daemon-reload` → `enable` + `start`. On failure at any step: stop/disable, remove files,
  `tunnel delete`, report failed step.
- **Edit:** render new YAML → validate (`cloudflared tunnel ingress validate`) → show diff vs
  current file → on confirm: back up old YAML, atomic write new YAML, `route dns` only for
  newly added hostnames, `restart`. If restart fails: restore backup YAML, restart again, report.
  Hostnames removed from a tunnel keep their DNS record; the UI warns which to remove manually.
- **Run / Stop / Restart:** plain `systemctl` call.
- **Delete:** confirm by typing the tunnel name → stop/disable → remove unit, YAML, credentials
  JSON → `daemon-reload` → `tunnel delete`. UI lists DNS hostnames that remain in Cloudflare.
- **Logs:** `journalctl -u cloudflared-<name> -n 200`.

Validation before any write: name/hostname allowlist regex, duplicate hostname+path check,
service URL format, port range.

File writes are atomic (temp file + rename); backup is written before overwrite.

## 6. UI

Single page, plain HTML/JS served by FastAPI.

- **List:** tunnels grouped by `project`, filter box. Columns: name, hostname chips, status badge,
  short UUID, actions (Run, Stop, Restart, Edit, Logs, Delete). Top bar: New tunnel,
  Import existing, Refresh. Status polls every 5s while the tab is visible.
- **Create/Edit (side panel):** name (locked after create), user, project, note; reorderable
  rules table (hostname, path, service URL, host header, noTLSVerify); catch-all 404 shown
  read-only. Preview tab shows rendered YAML and, on edit, a diff. Apply disabled until validation passes.
- **Apply progress:** per-step ✓/✗ shown live.
- **Logs modal:** last 200 lines, refresh, follow toggle.
- **Delete modal:** type tunnel name; lists leftover DNS hostnames.
- **Environment panel** (top of list, `GET /api/env`): shows
  - `cloudflared` installed?, binary path (`command -v`, fallback `/usr/bin/cloudflared`);
  - installed version (`cloudflared --version`);
  - latest version + "update available" badge (GitHub releases `latest`, cached 6h, fetched
    server-side; failure shows "không kiểm tra được" and never blocks other features);
  - `cert.pem` presence per distinct `user_name` among tunnels (`{home}/.cloudflared/cert.pem`);
  - "Check lại" button forces refresh.
  Actions: **Update…** (modal shows the exact commands; options "install only" or "install +
  restart all tunnels sequentially"; dpkg-based, same as the scripts) and **Fix cert.pem…**
  (modal with the headless instructions from the existing scripts; the web never runs the
  interactive `cloudflared login`). A missing binary shows the same install flow as Update.

## 7. Auth and security

- Single admin password, hash (argon2/bcrypt) in `/etc/cfkit/.env` (mode 600); never in repo/DB.
- HttpOnly + SameSite=Strict session cookie; CSRF token on all mutating routes; per-IP login
  rate limit.
- Binds `127.0.0.1:8787` by default. Remote access via `ssh -L`. If exposed through a tunnel,
  Cloudflare Access must sit in front (documented in README); password alone is not sufficient.
- All subprocess calls use argument lists; names/hostnames are allowlist-validated to prevent
  injection into `systemctl` / `cloudflared` / unit files.

## 8. Deployment

`install.sh` creates a venv under `/opt/cfkit`, writes `/etc/cfkit/.env` (prompts for password),
and installs `cfkit.service` (uvicorn as root). Same style as the existing tunnel scripts.

## 9. Testing

- **Unit (no root):** golden-file tests for `render.py` using fixtures derived from the two
  existing scripts (`HotonChat` with path rules + `httpHostHeader`; `SiloS3` with two hostnames);
  importer round-trip (import golden → render → identical); validation tests including
  injection strings.
- **Integration (fake runner):** `cf.py` / `svc.py` use an injected runner that records calls;
  assert exact sequences for create, edit, delete, and rollback-on-failure. Temp dirs replace
  `~/.cloudflared` and `/etc/systemd/system`.
- **API:** FastAPI `TestClient` for auth required, CSRF, rate limit, each flow.
- **Manual smoke (on the real server):** import real tunnels, edit one trivial field, confirm
  UUID and DNS unchanged and service returns to active.

## 10. Repo layout

```
cfkit/
  app/ {main,api,auth,db,render,importer,cf,svc,runner}.py
  static/ {index.html,app.js,style.css}
  tests/ (+ fixtures/ golden yaml)
  install.sh, requirements.txt, README.md
a_tunnel_linux.sh, b_tunnel_linux.sh   (untouched)
```

## 11. Assumptions to confirm

- Default port 8787 and install path `/opt/cfkit` (changeable).
- Removed hostnames leave their DNS record behind with a warning (cloudflared CLI cannot delete DNS).
- Tunnels created by the web use `cert.pem`-based (locally-managed) mode, same as the scripts.
