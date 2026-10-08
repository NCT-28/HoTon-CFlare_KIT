# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Codex will review your output once you are done.

---

**Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.**

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## CRITICAL: Before ANY response (including questions)
- For ANY question about this codebase → call serena (or graphtr if this project has it set up) FIRST, then answer
- For external library/SDK questions → use context7 first, then serena for codebase integration
- Do NOT answer from training memory alone if the question involves code in this repo

## 0. Critical Thinking

**Actively challenge the user's reasoning — don't just execute.**

When the user makes a claim, proposes an approach, or describes a problem:
- Flag implicit assumptions they haven't stated.
- Identify logical gaps, missing context, or weak evidence.
- Name cognitive biases that might be shaping their framing.
- Surface uncomfortable truths they might be avoiding or overlooking.
- If their conclusion doesn't follow from their premises, say so directly.

This applies to technical decisions AND general reasoning. Sycophantic agreement is a failure mode.

Tone: direct, respectful, constructive. Never harsh, never dismissive.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

> **Simplicity vs Surgical tiebreaker:** If simplifying requires touching code outside the current scope, mention it to the user — don't do it silently.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

- If you notice a real bug in adjacent code (not dead code): mention it to the user — don't fix it silently.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

- If stuck after 2 attempts at the same sub-task: stop, name what's blocking, ask the user.

## 5. Sensitive Data

- Never hardcode secrets, API keys, or credentials in code.
- Never log or print credential values.
- If a task requires a secret, reference an environment variable — do not suggest inlining the value.

## 6. Verification via Sandbox (MANDATORY)

After any bug fix or logic change → invoke the **"verification" skill** before claiming done.
Never say "looks correct" without running it. PASS evidence required.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## MCP & Context Optimization

For any research, code navigation, or multi-source gathering → invoke the **"mcp-workflow" skill**.

Quick reference (full detail in skill):
- External library/SDK → `context7` first
- Internal codebase → `serena` first
- Large output / multi-command → `ctx_batch_execute`
- `graphtr-out/` exists → use graphtr before serena deep-reads

---

## graphtr

If this project has a `graphtr-out/` directory, it's a local snapshot of the hoton-graphtr
MCP server's code graph for this repo (see the `graphtr` skill for the full workflow).

Rules:
- For codebase questions, first run `python3 graphtr-out/query.py query "<keyword>"` when graphtr-out/graph.json exists. Use `graphtr-out/query.py path "<A>" "<B>"` for relationships and `graphtr-out/query.py explain "<name>"` for a node + its neighbors. These return a scoped subgraph, usually much smaller than raw grep output.
- If the script errors or the graph looks stale, fall back to `mcp__hoton-graphtr__query_code_graph` with the `user_id`/`repo_id` from `graphtr-out/manifest.json`.
- Open `graphtr-out/graphtr.html` in a browser for a visual, interactive view.
- Refreshing the graph after code changes is a re-export from hoton-graphtr, not a local rebuild — see the `graphtr` skill's Refresh flow; do not call `ingest_codebase` again (it mints a new `repo_id` and creates a duplicate graph).
- If there's no `graphtr-out/` yet, this section doesn't apply — skip it and use `serena` directly.

---

<!--
TOOLKIT NOTE: everything above this line is generic and came from
~/dotfiles/claude-toolkit. Add project-specific sections below
(Project Overview, Service Map, Key Commands, Architecture, etc.) —
those don't belong in the shared template.
-->

## Project Overview

**CFKit** (`cfkit/`) is a web UI that manages Cloudflare Tunnels on one Linux/systemd machine: list, create, edit,
run/stop, ReTunnel (new tunnel id) and delete. It replaces the per-project `*_tunnel_linux.sh` scripts; the one at the
repo root, `cfkit_tunnel_linux.sh` (tunnel for CFKit itself), is standalone — **do not edit it** unless asked.
The single user-facing doc is the root `README.md` (keep it current; there is no `cfkit/README.md`).

- Stack: Python 3.10+, FastAPI + uvicorn, SQLite (stdlib), PyYAML, argon2-cffi; plain HTML/CSS/JS front end (no build step).
- Runs as **root** (writes `/etc/systemd/system`, runs `systemctl`), binds `127.0.0.1:8787`, single admin password.
- SQLite is the source of truth; `~/.cloudflared/config-<name>.yaml` and `cloudflared-<name>.service` are rendered from it.
- Docs: spec `docs/superpowers/specs/2026-10-08-cfkit-tunnel-manager-design.md`, plan `docs/superpowers/plans/`, UI mockup
  `docs/superpowers/mockups/`. User-facing docs: root `README.md`.

## Key Commands (run from `cfkit/`)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # once
.venv/bin/pytest -q                                                        # full suite (~200 tests, no root/network needed)
node --check static/app.js                                                 # JS syntax check
sudo ./install.sh                                                          # install/upgrade to /opt/cfkit + systemd unit (keeps /etc/cfkit/.env)
```

Dev server without root (UI only — **never click Apply/Run/Delete**, it would call the real `cloudflared`):
`CFKIT_PASSWORD_HASH=… CFKIT_SECRET=… CFKIT_DB=/tmp/x.db .venv/bin/python -m app.main`

## Architecture (`cfkit/app/`)

| Module | Role |
|---|---|
| `models.py`, `config.py` | dataclasses (`Tunnel`, `Rule`, `Step`); `Settings` (env-driven, injectable lookups for tests) |
| `validate.py` | regex allowlists (always `fullmatch`) + `validate_tunnel()`; runs before any file write or command |
| `db.py` | SQLite CRUD, rule order, config backups (last 20) |
| `render.py` | pure: tunnel → YAML / systemd unit text (golden-tested) |
| `importer.py` | parse existing YAML/units; warns instead of silently dropping content |
| `runner.py` | `Runner` protocol, `SubprocessRunner`, `CommandError`; **argument lists only, never shell strings** |
| `cf.py`, `svc.py` | thin wrappers over `cloudflared` and `systemctl`/`journalctl` |
| `safeio.py` | symlink-safe read/write (root writes into user-owned dirs) |
| `flows.py` | `Manager`: create / edit / delete / retunnel / control / update; per-tunnel locks, rollback/restore |
| `env.py` | cloudflared version, GitHub latest (cached), cert.pem per user |
| `auth.py`, `api.py`, `main.py` | argon2 login + rate limit, session + CSRF, routes, uvicorn entry |
| `static/` | `index.html`, `app.js`, `hostutil.js` (default-domain helpers, node-tested), `style.css` |

## Project Conventions & Gotchas

- **TDD**: failing test first, then code. Flows are tested with `tests/fakes.py::FakeRunner` and temp dirs — no real systemd/Cloudflare.
- **Edit never recreates the tunnel** (UUID and DNS stay). Only `retunnel` changes the id (and overwrites DNS with `--overwrite-dns`).
- **Address Cloudflare tunnels by UUID, not name** for delete/route-dns: duplicate names make name-based calls fail.
- The final `- service: http_status:404` ingress rule is never stored; `render.py` always appends it. Rule order matters.
- `Type=simple` reports success on fork, so `flows.py::_ensure_running` requires `active` through a 3 s settle window after start/restart.
- Tunnel `name` and `user_name` are locked after create. DNS records are **never deleted** (cloudflared CLI cannot); the UI lists leftovers.
- Front end is served under a strict CSP: **no inline `<script>`/`style=`/`setAttribute("style")`, no `innerHTML`** — build DOM with `h()`/`textContent`.
- `install.sh` copies `app/` and `static/` into `/opt/cfkit`: after UI/code changes the server needs `sudo ./install.sh` and a hard refresh (Ctrl+Shift+R).
- Behind a tunnel/proxy, set `CFKIT_ALLOWED_HOSTS` (and `CFKIT_HTTPS_ONLY=1`) in `/etc/cfkit/.env`, otherwise requests fail with *Invalid host header*.
- Not verifiable by unit tests (needs a real server): `cloudflared tunnel --config … ingress validate` flag order, `tunnel delete` of a
  stopped tunnel, and cloudflared message texts matched in `cf.py` ("already exists", "not found", "already been deleted").
- Git: work happens on `develop`; do not push or commit unless the user asks.
