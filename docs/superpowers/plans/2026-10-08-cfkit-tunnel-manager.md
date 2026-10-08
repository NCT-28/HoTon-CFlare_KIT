# CFKit Tunnel Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A localhost web UI (FastAPI + SQLite + plain HTML/JS) that lists, creates, edits, runs/stops and deletes cloudflared tunnels on one Linux machine, replacing the per-project `a_tunnel_linux.sh` / `b_tunnel_linux.sh` copies.

**Architecture:** SQLite is the source of truth. Pure `render.py` turns DB rows into the `config-<name>.yaml` and systemd unit text; `flows.py` orchestrates create/edit/delete/run through two thin CLI wrappers (`cf.py` for `cloudflared`, `svc.py` for `systemctl`/`journalctl`) that share one injectable `Runner`, so every flow is testable with a fake runner. Edit rewrites YAML + restarts; it never recreates the tunnel (UUID and DNS stay).

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, PyYAML, argon2-cffi, itsdangerous (session cookie), SQLite (stdlib), pytest + httpx (dev), plain HTML/CSS/JS front end.

**Spec:** `docs/superpowers/specs/2026-10-08-cfkit-tunnel-manager-design.md`
**UI reference:** `docs/superpowers/mockups/cfkit-ui-mockup.html`

## Global Constraints

- Linux + systemd only; macOS/launchd is out of scope. Python 3.10+.
- Web server runs as root, binds `127.0.0.1:8787` by default, single admin password. Install path `/opt/cfkit`, env file `/etc/cfkit/.env` (mode 600), DB `/var/lib/cfkit/cfkit.db`.
- Every subprocess call uses an argument list (never a shell string). Names/hostnames/users/paths/services pass the allowlist regexes in `validate.py` **before** any file write or command. Regexes are applied with `fullmatch` (a trailing `\n` must not pass).
- The catch-all `- service: http_status:404` is never stored; `render.py` always appends it. Ingress rule order is preserved (cloudflared matches top to bottom).
- Edit never runs `tunnel create`/`tunnel delete`; UUID and existing DNS records stay. Removed hostnames keep their DNS record; the result reports them.
- Secrets (password hash, session secret) only in `/etc/cfkit/.env`; never in repo, DB, or logs.
- Credentials/`cert.pem` handling: the web never runs the interactive `cloudflared login`.
- Per repo `CLAUDE.md` §6: invoke the **verification** skill before claiming any task done; show PASS evidence.
- Existing `a_tunnel_linux.sh` / `b_tunnel_linux.sh` are left untouched.

## Plan notes (deviations / additions vs. spec)

- Extra modules not in the spec's table: `models.py`, `config.py`, `validate.py`, `flows.py`, `env.py` (spec §6 environment panel).
- `user_name` is **locked after create** (like `name`): changing it would orphan credentials owned by the old user. The edit form disables it.
- Rule reordering uses ↑/↓ buttons instead of drag-and-drop (no JS dependency).
- Description line of the generated unit is `Cloudflare Tunnel <name>` (no hostnames), so editing hostnames never makes the unit stale.
- Extra security: `TrustedHostMiddleware` (`CFKIT_ALLOWED_HOSTS`, default `127.0.0.1,localhost`) and a CSP header.

## Review Focus

Failure modes the spec implies but a task's happy-path tests would miss (each has an owning test):

1. Name/hostname/path/service/user containing newlines, `;`, `$()`, spaces, or a trailing `\n` → rejected before any command or file write (Task 2, Task 8 create-rejects test).
2. Editing an **inactive** tunnel must not start it (Task 8).
3. Edit whose restart fails → previous YAML restored, DB rows unchanged, result not ok (Task 8).
4. Importing a hand-written YAML with comments, unsupported `originRequest` keys, or hostname-less rules → warnings, never silent loss (Task 5).
5. Foreign `Host` header, missing CSRF token, 6th wrong password, GitHub unreachable → rejected / degraded without breaking other endpoints (Tasks 7, 9).

---

## File Structure

```
cfkit/
  app/
    __init__.py
    models.py      # Rule, Tunnel, Step, hostnames()
    config.py      # Settings, load_settings(), default system lookups
    runner.py      # Result, Runner, SubprocessRunner, CommandError, tail()
    validate.py    # regex allowlists, validate_tunnel()
    db.py          # Database (SQLite CRUD + backups)
    render.py      # render_yaml(), render_unit()  (pure)
    importer.py    # parse existing YAML/unit, scan, import
    cf.py          # Cloudflared wrapper
    svc.py         # Systemd wrapper
    env.py         # version / latest / cert.pem info
    flows.py       # Manager: create/edit/delete/control/preview/update
    auth.py        # password hashing, rate limiter
    api.py         # create_app()
    main.py        # build_app(), uvicorn entry
  static/ {index.html, app.js, style.css}
  tests/
    __init__.py, conftest.py, fakes.py, samples.py
    fixtures/ {hotonchat.yaml, hotonchat_script.yaml, silos3.yaml, hotonchat.service}
    test_*.py
  install.sh, requirements.txt, requirements-dev.txt, pytest.ini, .gitignore, README.md
```

All paths below are relative to the repo root `HoTon-CFlare_KIT/`. Run tests with `cd cfkit && .venv/bin/pytest -q`.

---

### Task 1: Skeleton, models, settings, runner

**Files:**
- Create: `cfkit/requirements.txt`, `cfkit/requirements-dev.txt`, `cfkit/pytest.ini`, `cfkit/.gitignore`
- Create: `cfkit/app/__init__.py` (empty), `cfkit/app/models.py`, `cfkit/app/config.py`, `cfkit/app/runner.py`
- Create: `cfkit/tests/__init__.py` (empty), `cfkit/tests/fakes.py`, `cfkit/tests/test_runner.py`, `cfkit/tests/test_config.py`

**Interfaces:**
- Produces: `Rule(hostname, service, path=None, http_host_header=None, no_tls_verify=False)`, `Tunnel(name, user_name, rules=[], project="", note="", uuid=None)`, `Step(name, ok, detail="")`, `hostnames(t) -> list[str]` (distinct, ordered).
- Produces: `Result(code, out="", err="")` with `.ok`; `Runner.run(args, *, user=None, timeout=60) -> Result`; `SubprocessRunner`; `build_command(args, user, current_user)`; `CommandError`; `tail(result, n=400)`.
- Produces: `Settings` dataclass (fields below), `load_settings(env) -> Settings`, `find_cloudflared() -> str | None`.
- Produces (tests): `FakeRunner` with `.calls`, `.when_has(*tokens, result=)`, `.argv()`, `.has_call(*tokens)`.

- [ ] **Step 1: Create project files**

`cfkit/requirements.txt`:
```
fastapi>=0.110
uvicorn>=0.29
pyyaml>=6.0
argon2-cffi>=23.1
itsdangerous>=2.1
```
`cfkit/requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
httpx>=0.27
```
`cfkit/pytest.ini`:
```
[pytest]
pythonpath = .
testpaths = tests
```
`cfkit/.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
```
Then:
```bash
cd cfkit && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
```

- [ ] **Step 2: Write the failing tests**

`cfkit/tests/fakes.py`:
```python
from __future__ import annotations

from app.runner import Result


class FakeRunner:
    """Records every command; later `when_has` rules win over earlier ones."""

    def __init__(self):
        self.calls: list[tuple[list[str], str | None]] = []
        self._rules: list[tuple[tuple[str, ...], object]] = []

    def when_has(self, *tokens: str, result):
        self._rules.append((tokens, result))

    def run(self, args, *, user=None, timeout=60):
        self.calls.append((list(args), user))
        for tokens, res in reversed(self._rules):
            if all(t in args for t in tokens):
                return res(args) if callable(res) else res
        return Result(0, "", "")

    def argv(self) -> list[list[str]]:
        return [c[0] for c in self.calls]

    def has_call(self, *tokens: str) -> bool:
        return any(all(t in a for t in tokens) for a in self.argv())
```

`cfkit/tests/test_runner.py`:
```python
from app.runner import Result, SubprocessRunner, build_command, tail
from tests.fakes import FakeRunner


def test_build_command_no_sudo_for_same_or_no_user():
    assert build_command(["echo", "x"], None, "root") == ["echo", "x"]
    assert build_command(["echo", "x"], "root", "root") == ["echo", "x"]


def test_build_command_wraps_other_user():
    assert build_command(["cloudflared", "tunnel", "list"], "toannc", "root") == [
        "sudo", "-n", "-H", "-u", "toannc", "--", "cloudflared", "tunnel", "list"
    ]


def test_subprocess_runner_captures_output():
    r = SubprocessRunner().run(["echo", "hi"])
    assert (r.code, r.out) == (0, "hi\n") and r.ok


def test_subprocess_runner_missing_binary_is_127():
    r = SubprocessRunner().run(["definitely-not-a-binary-xyz"])
    assert r.code == 127 and "not found" in r.err


def test_subprocess_runner_timeout_is_124():
    r = SubprocessRunner().run(["sleep", "5"], timeout=1)
    assert r.code == 124


def test_tail_prefers_stderr_and_truncates():
    assert tail(Result(1, "out", "boom")) == "boom"
    assert tail(Result(1, "only-out", "")) == "only-out"
    assert tail(Result(7, "", "")) == "exit code 7"
    assert len(tail(Result(1, "", "x" * 1000))) == 400


def test_fake_runner_last_rule_wins_and_records():
    f = FakeRunner()
    f.when_has("a", result=Result(1, "", "first"))
    f.when_has("a", "b", result=Result(2, "", "second"))
    assert f.run(["a", "b"]).code == 2
    assert f.run(["a"]).code == 1
    assert f.run(["zzz"]).code == 0
    assert f.has_call("a", "b") and not f.has_call("nope")
```

`cfkit/tests/test_config.py`:
```python
from pathlib import Path

from app.config import load_settings


def test_load_settings_defaults():
    s = load_settings({})
    assert s.port == 8787 and s.host == "127.0.0.1"
    assert s.db_path == Path("/var/lib/cfkit/cfkit.db")
    assert s.allowed_hosts == ["127.0.0.1", "localhost"]
    assert s.password_hash == "" and s.https_only is False


def test_load_settings_from_env():
    s = load_settings({
        "CFKIT_PORT": "9000", "CFKIT_HOST": "0.0.0.0", "CFKIT_DB": "/tmp/x.db",
        "CFKIT_PASSWORD_HASH": "h", "CFKIT_SECRET": "s", "CFKIT_HTTPS_ONLY": "1",
        "CFKIT_ALLOWED_HOSTS": "a.example.com, localhost",
    })
    assert (s.port, s.host, s.db_path) == (9000, "0.0.0.0", Path("/tmp/x.db"))
    assert (s.password_hash, s.secret_key, s.https_only) == ("h", "s", True)
    assert s.allowed_hosts == ["a.example.com", "localhost"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd cfkit && .venv/bin/pytest -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'app.runner'`.

- [ ] **Step 4: Implement**

`cfkit/app/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rule:
    hostname: str
    service: str
    path: str | None = None
    http_host_header: str | None = None
    no_tls_verify: bool = False


@dataclass
class Tunnel:
    name: str
    user_name: str
    rules: list[Rule] = field(default_factory=list)
    project: str = ""
    note: str = ""
    uuid: str | None = None


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def hostnames(t: Tunnel) -> list[str]:
    """Distinct hostnames in rule order."""
    seen: list[str] = []
    for r in t.rules:
        if r.hostname not in seen:
            seen.append(r.hostname)
    return seen
```

`cfkit/app/runner.py`:
```python
from __future__ import annotations

import os
import pwd
import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence


class CommandError(Exception):
    """A wrapped external command failed."""


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


def tail(r: Result, n: int = 400) -> str:
    text = r.err.strip() or r.out.strip()
    return text[-n:] if text else f"exit code {r.code}"


class Runner(Protocol):
    def run(self, args: Sequence[str], *, user: str | None = None, timeout: int = 60) -> Result: ...


def build_command(args: Sequence[str], user: str | None, current_user: str) -> list[str]:
    cmd = list(args)
    if user and user != current_user:
        return ["sudo", "-n", "-H", "-u", user, "--", *cmd]
    return cmd


class SubprocessRunner:
    def __init__(self) -> None:
        self._current = pwd.getpwuid(os.geteuid()).pw_name

    def run(self, args: Sequence[str], *, user: str | None = None, timeout: int = 60) -> Result:
        cmd = build_command(args, user, self._current)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timeout after {timeout}s: {args[0]}")
        except FileNotFoundError:
            return Result(127, "", f"command not found: {cmd[0]}")
        return Result(p.returncode, p.stdout, p.stderr)
```

`cfkit/app/config.py`:
```python
from __future__ import annotations

import grp
import pwd
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


def default_home_of(user: str) -> Path:
    return Path(pwd.getpwnam(user).pw_dir)


def default_group_of(user: str) -> str:
    return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name


def default_user_exists(user: str) -> bool:
    try:
        pwd.getpwnam(user)
        return True
    except (KeyError, ValueError):
        return False


def default_discover_homes() -> dict[str, Path]:
    """Users whose home contains a .cloudflared directory."""
    homes: dict[str, Path] = {}
    for p in pwd.getpwall():
        if (Path(p.pw_dir) / ".cloudflared").is_dir():
            homes[p.pw_name] = Path(p.pw_dir)
    return homes


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    return "/usr/bin/cloudflared" if Path("/usr/bin/cloudflared").exists() else None


@dataclass
class Settings:
    db_path: Path
    systemd_dir: Path = Path("/etc/systemd/system")
    home_of: Callable[[str], Path] = default_home_of
    group_of: Callable[[str], str] = default_group_of
    user_exists: Callable[[str], bool] = default_user_exists
    discover_homes: Callable[[], dict[str, Path]] = default_discover_homes
    cloudflared_bin: Callable[[], str | None] = find_cloudflared
    chown: bool = True
    password_hash: str = ""
    secret_key: str = ""
    https_only: bool = False
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    host: str = "127.0.0.1"
    port: int = 8787


def load_settings(env: Mapping[str, str]) -> Settings:
    hosts = [h.strip() for h in env.get("CFKIT_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
    return Settings(
        db_path=Path(env.get("CFKIT_DB", "/var/lib/cfkit/cfkit.db")),
        password_hash=env.get("CFKIT_PASSWORD_HASH", ""),
        secret_key=env.get("CFKIT_SECRET", ""),
        https_only=env.get("CFKIT_HTTPS_ONLY", "") == "1",
        allowed_hosts=hosts,
        host=env.get("CFKIT_HOST", "127.0.0.1"),
        port=int(env.get("CFKIT_PORT", "8787")),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd cfkit && .venv/bin/pytest -q`
Expected: all pass (9 tests).

- [ ] **Step 6: Commit**

```bash
git add cfkit/
git commit -m "feat(cfkit): project skeleton, models, settings, command runner"
```

---

### Task 2: Input validation

**Files:**
- Create: `cfkit/app/validate.py`, `cfkit/tests/test_validate.py`

**Interfaces:**
- Consumes: `Rule`, `Tunnel` from `app.models`.
- Produces: `NAME_RE`, `USER_RE`, `HOST_RE` (compiled patterns, use `.fullmatch`), `validate_tunnel(t: Tunnel) -> list[str]` (empty list = valid). Error strings for rules start with `rule N:`; name errors start with `name:`, user with `user:`.

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_validate.py`:
```python
import pytest

from app.models import Rule, Tunnel
from app.validate import validate_tunnel


def rule(**kw):
    base = dict(hostname="app.example.com", service="http://127.0.0.1:5500")
    base.update(kw)
    return Rule(**base)


def tunnel(**kw):
    base = dict(name="HotonChat", user_name="toannc", rules=[rule()])
    base.update(kw)
    return Tunnel(**base)


def errs(t):
    return validate_tunnel(t)


def test_valid_tunnel_has_no_errors():
    assert errs(tunnel()) == []


@pytest.mark.parametrize("name", ["", "1abc", "a b", "a;rm -rf /", "name\n", "a/b", "x" * 64, "$(id)", "a.b"])
def test_bad_names_rejected(name):
    assert any(e.startswith("name:") for e in errs(tunnel(name=name)))


@pytest.mark.parametrize("user", ["", "Root", "a b", "root\n", "-x", "a;b"])
def test_bad_users_rejected(user):
    assert any(e.startswith("user:") for e in errs(tunnel(user_name=user)))


@pytest.mark.parametrize("host", [
    "", "UPPER.example.com", "no_dot", "a b.com", "a.com\n", "-a.example.com",
    "a.example.com;ls", "*.example.com", "a..com",
])
def test_bad_hostnames_rejected(host):
    assert any("hostname" in e for e in errs(tunnel(rules=[rule(hostname=host)])))


@pytest.mark.parametrize("service", [
    "", "ftp://127.0.0.1:1", "http://127.0.0.1", "http://127.0.0.1:0",
    "http://127.0.0.1:70000", "http://a b:80", "http://127.0.0.1:80\n", "http://x:80/path",
])
def test_bad_services_rejected(service):
    assert any("service" in e for e in errs(tunnel(rules=[rule(service=service)])))


@pytest.mark.parametrize("path", ["api", "/a b", "/a#b", "/a\nb", "/x;y", "/a\n"])
def test_bad_paths_rejected(path):
    assert any("path" in e for e in errs(tunnel(rules=[rule(path=path)])))


@pytest.mark.parametrize("header", ["a b", 'x"y', "h\n", "h:99999x"])
def test_bad_host_headers_rejected(header):
    assert any("host header" in e for e in errs(tunnel(rules=[rule(http_host_header=header)])))


def test_good_optional_fields_accepted():
    r = rule(path="/hoton-chat*", http_host_header="localhost:5507", no_tls_verify=True)
    assert errs(tunnel(rules=[r])) == []


def test_empty_string_path_and_header_mean_unset():
    assert errs(tunnel(rules=[rule(path="", http_host_header="")])) == []


def test_at_least_one_rule():
    assert any("at least one rule" in e for e in errs(tunnel(rules=[])))


def test_duplicate_hostname_and_path():
    rules = [rule(path="/a"), rule(path="/a")]
    assert any("duplicate" in e for e in errs(tunnel(rules=rules)))


def test_rule_after_pathless_rule_for_same_host_is_unreachable():
    rules = [rule(), rule(path="/x")]
    assert any("rule 2" in e and "unreachable" in e for e in errs(tunnel(rules=rules)))


def test_pathless_rule_last_is_fine():
    assert errs(tunnel(rules=[rule(path="/x"), rule()])) == []


def test_other_host_after_pathless_rule_is_fine():
    assert errs(tunnel(rules=[rule(), rule(hostname="b.example.com", path="/x")])) == []


@pytest.mark.parametrize("field", ["project", "note"])
def test_project_and_note_no_newline_and_length(field):
    assert any(e.startswith(field) for e in errs(tunnel(**{field: "a\nb"})))
    assert any(e.startswith(field) for e in errs(tunnel(**{field: "x" * 201})))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_validate.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.validate'`.

- [ ] **Step 3: Implement**

`cfkit/app/validate.py`:
```python
from __future__ import annotations

import re

from app.models import Rule, Tunnel

NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
HOST_RE = re.compile(r"(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
PATH_RE = re.compile(r"/[A-Za-z0-9._~\-/*%:@=+&]*")
SERVICE_RE = re.compile(r"https?://([A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]):(\d{1,5})")
HEADER_RE = re.compile(r"[A-Za-z0-9.\-]+(:\d{1,5})?")


def _validate_rule(r: Rule) -> list[str]:
    errs: list[str] = []
    if not HOST_RE.fullmatch(r.hostname or ""):
        errs.append("hostname: must be a lowercase domain like app.example.com")
    if r.path and not PATH_RE.fullmatch(r.path):
        errs.append("path: must start with / and contain only URL-safe characters")
    m = SERVICE_RE.fullmatch(r.service or "")
    if not m or not (1 <= int(m.group(2)) <= 65535):
        errs.append("service: must be http(s)://host:port with port 1-65535")
    if r.http_host_header and not HEADER_RE.fullmatch(r.http_host_header):
        errs.append("host header: must be host or host:port")
    return errs


def validate_tunnel(t: Tunnel) -> list[str]:
    errs: list[str] = []
    if not NAME_RE.fullmatch(t.name):
        errs.append("name: must start with a letter; letters, digits, '_' and '-' only (max 63)")
    if not USER_RE.fullmatch(t.user_name):
        errs.append("user: invalid system user name")
    for label, val in (("project", t.project), ("note", t.note)):
        if len(val) > 200 or "\n" in val or "\r" in val:
            errs.append(f"{label}: max 200 characters, single line")
    if not t.rules:
        errs.append("at least one rule is required")

    seen: set[tuple[str, str]] = set()
    pathless_hosts: set[str] = set()
    for i, r in enumerate(t.rules, 1):
        errs += [f"rule {i}: {e}" for e in _validate_rule(r)]
        key = (r.hostname, r.path or "")
        if key in seen:
            errs.append(f"rule {i}: duplicate hostname+path")
        elif r.hostname in pathless_hosts:
            errs.append(f"rule {i}: unreachable, an earlier rule for {r.hostname} has no path and matches first")
        seen.add(key)
        if not r.path:
            pathless_hosts.add(r.hostname)
    return errs
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_validate.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cfkit/app/validate.py cfkit/tests/test_validate.py
git commit -m "feat(cfkit): input validation allowlists"
```

---

### Task 3: SQLite persistence

**Files:**
- Create: `cfkit/app/db.py`, `cfkit/tests/test_db.py`

**Interfaces:**
- Consumes: `Rule`, `Tunnel`.
- Produces: `Database(path)` with `save_tunnel(t) -> None` (upsert by name, replaces rules, keeps rule order), `get_tunnel(name) -> Tunnel | None`, `list_tunnels() -> list[Tunnel]` (ordered by project then name), `delete_tunnel(name) -> None`, `add_backup(name, yaml_text) -> None` (keeps newest 20), `list_backups(name) -> list[str]` (newest first).

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_db.py`:
```python
import sqlite3

from app.db import Database
from app.models import Rule, Tunnel

U = "3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6"


def make(name="A", **kw):
    base = dict(
        name=name, user_name="root", project="P", note="n", uuid=U,
        rules=[
            Rule("a.example.com", "http://127.0.0.1:1", path="/x", http_host_header="h:1", no_tls_verify=True),
            Rule("b.example.com", "http://127.0.0.1:2"),
        ],
    )
    base.update(kw)
    return Tunnel(**base)


def test_roundtrip_preserves_everything(tmp_path):
    db = Database(tmp_path / "t.db")
    t = make()
    db.save_tunnel(t)
    assert db.get_tunnel("A") == t


def test_get_missing_returns_none(tmp_path):
    assert Database(tmp_path / "t.db").get_tunnel("nope") is None


def test_upsert_replaces_rules_and_order(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    db.save_tunnel(make(rules=[Rule("z.example.com", "http://127.0.0.1:9"), Rule("y.example.com", "http://127.0.0.1:8")], note="new"))
    got = db.get_tunnel("A")
    assert [r.hostname for r in got.rules] == ["z.example.com", "y.example.com"]
    assert got.note == "new"
    assert len(db.list_tunnels()) == 1


def test_list_sorted_by_project_then_name(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make("B", project="Z"))
    db.save_tunnel(make("C", project="A"))
    db.save_tunnel(make("A", project="A"))
    assert [t.name for t in db.list_tunnels()] == ["A", "C", "B"]


def test_delete_cascades_rules_and_backups(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    db.add_backup("A", "yaml")
    db.delete_tunnel("A")
    assert db.get_tunnel("A") is None
    c = sqlite3.connect(tmp_path / "t.db")
    assert c.execute("SELECT COUNT(*) FROM ingress_rules").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM config_backups").fetchone()[0] == 0


def test_backups_newest_first_and_pruned_to_20(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    for i in range(25):
        db.add_backup("A", f"v{i}")
    backups = db.list_backups("A")
    assert len(backups) == 20 and backups[0] == "v24" and backups[-1] == "v5"


def test_backup_for_unknown_tunnel_is_ignored(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_backup("ghost", "x")
    assert db.list_backups("ghost") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_db.py -q`
Expected: FAIL, `No module named 'app.db'`.

- [ ] **Step 3: Implement**

`cfkit/app/db.py`:
```python
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.models import Rule, Tunnel

SCHEMA = """
CREATE TABLE IF NOT EXISTS tunnels(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  tunnel_uuid TEXT,
  user_name TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingress_rules(
  id INTEGER PRIMARY KEY,
  tunnel_id INTEGER NOT NULL REFERENCES tunnels(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  hostname TEXT NOT NULL,
  path TEXT,
  service TEXT NOT NULL,
  http_host_header TEXT,
  no_tls_verify INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS config_backups(
  id INTEGER PRIMARY KEY,
  tunnel_id INTEGER NOT NULL REFERENCES tunnels(id) ON DELETE CASCADE,
  yaml_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

KEEP_BACKUPS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _load(c: sqlite3.Connection, row: sqlite3.Row) -> Tunnel:
        rules = [
            Rule(
                hostname=r["hostname"], service=r["service"], path=r["path"],
                http_host_header=r["http_host_header"], no_tls_verify=bool(r["no_tls_verify"]),
            )
            for r in c.execute("SELECT * FROM ingress_rules WHERE tunnel_id=? ORDER BY position", (row["id"],))
        ]
        return Tunnel(
            name=row["name"], user_name=row["user_name"], rules=rules,
            project=row["project"], note=row["note"], uuid=row["tunnel_uuid"],
        )

    def save_tunnel(self, t: Tunnel) -> None:
        now = _now()
        with self._conn() as c:
            row = c.execute("SELECT id FROM tunnels WHERE name=?", (t.name,)).fetchone()
            if row is None:
                tid = c.execute(
                    "INSERT INTO tunnels(name,tunnel_uuid,user_name,project,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (t.name, t.uuid, t.user_name, t.project, t.note, now, now),
                ).lastrowid
            else:
                tid = row["id"]
                c.execute(
                    "UPDATE tunnels SET tunnel_uuid=?, user_name=?, project=?, note=?, updated_at=? WHERE id=?",
                    (t.uuid, t.user_name, t.project, t.note, now, tid),
                )
                c.execute("DELETE FROM ingress_rules WHERE tunnel_id=?", (tid,))
            c.executemany(
                "INSERT INTO ingress_rules(tunnel_id,position,hostname,path,service,http_host_header,no_tls_verify) VALUES(?,?,?,?,?,?,?)",
                [(tid, i, r.hostname, r.path, r.service, r.http_host_header, int(r.no_tls_verify)) for i, r in enumerate(t.rules)],
            )

    def get_tunnel(self, name: str) -> Tunnel | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tunnels WHERE name=?", (name,)).fetchone()
            return self._load(c, row) if row else None

    def list_tunnels(self) -> list[Tunnel]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM tunnels ORDER BY project, name").fetchall()
            return [self._load(c, r) for r in rows]

    def delete_tunnel(self, name: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM tunnels WHERE name=?", (name,))

    def add_backup(self, name: str, yaml_text: str) -> None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM tunnels WHERE name=?", (name,)).fetchone()
            if row is None:
                return
            c.execute(
                "INSERT INTO config_backups(tunnel_id,yaml_text,created_at) VALUES(?,?,?)",
                (row["id"], yaml_text, _now()),
            )
            c.execute(
                "DELETE FROM config_backups WHERE tunnel_id=? AND id NOT IN "
                "(SELECT id FROM config_backups WHERE tunnel_id=? ORDER BY id DESC LIMIT ?)",
                (row["id"], row["id"], KEEP_BACKUPS),
            )

    def list_backups(self, name: str) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT b.yaml_text FROM config_backups b JOIN tunnels t ON t.id=b.tunnel_id "
                "WHERE t.name=? ORDER BY b.id DESC",
                (name,),
            ).fetchall()
            return [r["yaml_text"] for r in rows]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_db.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cfkit/app/db.py cfkit/tests/test_db.py
git commit -m "feat(cfkit): SQLite persistence for tunnels, rules, backups"
```

---

### Task 4: Renderers + golden fixtures

**Files:**
- Create: `cfkit/app/render.py`, `cfkit/tests/samples.py`, `cfkit/tests/test_render.py`
- Create: `cfkit/tests/fixtures/hotonchat.yaml`, `hotonchat_script.yaml`, `silos3.yaml`, `hotonchat.service`

**Interfaces:**
- Consumes: `Rule`.
- Produces: `render_yaml(uuid: str, credentials_file: str, rules: list[Rule]) -> str`; `render_unit(name, cloudflared_bin, config_file, user, group, home) -> str`.
- Produces (tests): `tests/samples.py` with `HOTON_UUID`, `SILO_UUID`, `hoton_rules()`, `silo_rules()`, `fixture(name) -> str`.

- [ ] **Step 1: Create the fixtures**

`cfkit/tests/fixtures/hotonchat.yaml` (what `render_yaml` must output for HotonChat):
```
tunnel: 3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6
credentials-file: /home/toannc/.cloudflared/3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6.json

ingress:
  - hostname: f1p.info.vn
    path: /api*
    service: http://127.0.0.1:5500
  - hostname: f1p.info.vn
    path: /rsocket*
    service: http://127.0.0.1:5500
    originRequest:
      noTLSVerify: true
  - hostname: f1p.info.vn
    path: /ws*
    service: http://127.0.0.1:5500
    originRequest:
      noTLSVerify: true
  - hostname: f1p.info.vn
    path: /hoton-avatars*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    path: /hoton-chat*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    path: /hoton-blog*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    path: /hoton-feeds*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    service: http://127.0.0.1:5501
  - service: http_status:404
```

`cfkit/tests/fixtures/hotonchat_script.yaml` (verbatim shape of what `a_tunnel_linux.sh` writes, with its comments):
```
# File cấu hình được tạo tự động cho tunnel HotonChat
# Lưu ý: tunnel ID và credentials-file khác với tunnel n8n
tunnel: 3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6
credentials-file: /home/toannc/.cloudflared/3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6.json

ingress:
  - hostname: f1p.info.vn
    path: /api*
    service: http://127.0.0.1:5500
  - hostname: f1p.info.vn
    path: /rsocket*
    service: http://127.0.0.1:5500
    originRequest:
      noTLSVerify: true
  - hostname: f1p.info.vn
    path: /ws*
    service: http://127.0.0.1:5500
    originRequest:
      noTLSVerify: true
  - hostname: f1p.info.vn
    path: /hoton-avatars*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"   # ← localhost vì cloudflared chạy trực tiếp trên host
  - hostname: f1p.info.vn
    path: /hoton-chat*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    path: /hoton-blog*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    path: /hoton-feeds*
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: f1p.info.vn
    service: http://127.0.0.1:5501
  - service: http_status:404
```

`cfkit/tests/fixtures/silos3.yaml`:
```
tunnel: b71e04d8-5a6f-4c7d-9e8f-0123456789ab
credentials-file: /root/.cloudflared/b71e04d8-5a6f-4c7d-9e8f-0123456789ab.json

ingress:
  - hostname: s3.f1p.info.vn
    service: http://127.0.0.1:5507
    originRequest:
      httpHostHeader: "localhost:5507"
  - hostname: silo-api.f1p.info.vn
    service: http://127.0.0.1:5508
  - service: http_status:404
```

`cfkit/tests/fixtures/hotonchat.service`:
```
[Unit]
Description=Cloudflare Tunnel HotonChat
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared --config /home/toannc/.cloudflared/config-HotonChat.yaml tunnel run
Restart=on-failure
RestartSec=5
User=toannc
Group=toannc
Environment=HOME=/home/toannc
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cloudflared-HotonChat

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write samples + failing tests**

`cfkit/tests/samples.py`:
```python
from __future__ import annotations

from pathlib import Path

from app.models import Rule

FIX = Path(__file__).parent / "fixtures"
HOTON_UUID = "3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6"
SILO_UUID = "b71e04d8-5a6f-4c7d-9e8f-0123456789ab"


def fixture(name: str) -> str:
    return (FIX / name).read_text()


def hoton_rules() -> list[Rule]:
    h, api, s3, hdr = "f1p.info.vn", "http://127.0.0.1:5500", "http://127.0.0.1:5507", "localhost:5507"
    return (
        [Rule(h, api, path="/api*"),
         Rule(h, api, path="/rsocket*", no_tls_verify=True),
         Rule(h, api, path="/ws*", no_tls_verify=True)]
        + [Rule(h, s3, path=p, http_host_header=hdr)
           for p in ("/hoton-avatars*", "/hoton-chat*", "/hoton-blog*", "/hoton-feeds*")]
        + [Rule(h, "http://127.0.0.1:5501")]
    )


def silo_rules() -> list[Rule]:
    return [
        Rule("s3.f1p.info.vn", "http://127.0.0.1:5507", http_host_header="localhost:5507"),
        Rule("silo-api.f1p.info.vn", "http://127.0.0.1:5508"),
    ]
```

`cfkit/tests/test_render.py`:
```python
from app.models import Tunnel
from app.render import render_unit, render_yaml
from app.validate import validate_tunnel
from tests.samples import HOTON_UUID, SILO_UUID, fixture, hoton_rules, silo_rules


def test_hotonchat_yaml_matches_golden():
    out = render_yaml(HOTON_UUID, f"/home/toannc/.cloudflared/{HOTON_UUID}.json", hoton_rules())
    assert out == fixture("hotonchat.yaml")


def test_silos3_yaml_matches_golden():
    out = render_yaml(SILO_UUID, f"/root/.cloudflared/{SILO_UUID}.json", silo_rules())
    assert out == fixture("silos3.yaml")


def test_catch_all_is_always_last_and_unique():
    out = render_yaml(HOTON_UUID, "/c.json", silo_rules())
    assert out.rstrip().endswith("  - service: http_status:404")
    assert out.count("http_status:404") == 1


def test_golden_samples_pass_validation():
    assert validate_tunnel(Tunnel("HotonChat", "toannc", hoton_rules())) == []
    assert validate_tunnel(Tunnel("SiloS3", "root", silo_rules())) == []


def test_unit_matches_golden():
    out = render_unit(
        name="HotonChat", cloudflared_bin="/usr/bin/cloudflared",
        config_file="/home/toannc/.cloudflared/config-HotonChat.yaml",
        user="toannc", group="toannc", home="/home/toannc",
    )
    assert out == fixture("hotonchat.service")
```

- [ ] **Step 3: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_render.py -q`
Expected: FAIL, `No module named 'app.render'`.

- [ ] **Step 4: Implement**

`cfkit/app/render.py`:
```python
from __future__ import annotations

from app.models import Rule


def render_yaml(uuid: str, credentials_file: str, rules: list[Rule]) -> str:
    """Values must already be validated (validate.py); they are written unquoted."""
    lines = [f"tunnel: {uuid}", f"credentials-file: {credentials_file}", "", "ingress:"]
    for r in rules:
        lines.append(f"  - hostname: {r.hostname}")
        if r.path:
            lines.append(f"    path: {r.path}")
        lines.append(f"    service: {r.service}")
        opts: list[str] = []
        if r.http_host_header:
            opts.append(f'      httpHostHeader: "{r.http_host_header}"')
        if r.no_tls_verify:
            opts.append("      noTLSVerify: true")
        if opts:
            lines.append("    originRequest:")
            lines += opts
    lines.append("  - service: http_status:404")
    return "\n".join(lines) + "\n"


def render_unit(name: str, cloudflared_bin: str, config_file: str, user: str, group: str, home: str) -> str:
    return (
        "[Unit]\n"
        f"Description=Cloudflare Tunnel {name}\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={cloudflared_bin} --config {config_file} tunnel run\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"User={user}\n"
        f"Group={group}\n"
        f"Environment=HOME={home}\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        f"SyslogIdentifier=cloudflared-{name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_render.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add cfkit/app/render.py cfkit/tests/
git commit -m "feat(cfkit): YAML/unit renderers with golden fixtures from existing scripts"
```

---

### Task 5: Importer

**Files:**
- Create: `cfkit/app/importer.py`, `cfkit/tests/test_importer.py`

**Interfaces:**
- Consumes: `Rule`, `Tunnel`, `Database`, `validate_tunnel`, `NAME_RE`.
- Produces: `Candidate(name, user_name, uuid, rules, warnings, known, error)` with property `importable`; `parse_config(text) -> (uuid | None, rules, warnings)`; `parse_unit_user(text) -> str | None`; `scan(homes: dict[str, Path], systemd_dir: Path, known: set[str]) -> list[Candidate]`; `import_candidates(db, candidates) -> list[str]` (names saved; only importable and not known).

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_importer.py`:
```python
from app.db import Database
from app.importer import import_candidates, parse_config, parse_unit_user, scan
from app.render import render_yaml
from tests.samples import HOTON_UUID, fixture, hoton_rules


def test_parse_script_output_with_comments():
    uuid, rules, warnings = parse_config(fixture("hotonchat_script.yaml"))
    assert uuid == HOTON_UUID
    assert rules == hoton_rules()
    assert warnings == []


def test_roundtrip_golden_is_identical():
    text = fixture("hotonchat.yaml")
    uuid, rules, _ = parse_config(text)
    assert render_yaml(uuid, f"/home/toannc/.cloudflared/{uuid}.json", rules) == text


def test_unsupported_origin_request_keys_warn_but_rule_kept():
    text = (
        f"tunnel: {HOTON_UUID}\ncredentials-file: /c.json\ningress:\n"
        "  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
        "    originRequest:\n      connectTimeout: 5s\n      noTLSVerify: true\n"
        "  - service: http_status:404\n"
    )
    _, rules, warnings = parse_config(text)
    assert len(rules) == 1 and rules[0].no_tls_verify is True
    assert any("connectTimeout" in w for w in warnings)


def test_rule_without_hostname_warned_and_404_catch_all_silent():
    text = (
        f"tunnel: {HOTON_UUID}\ncredentials-file: /c.json\ningress:\n"
        "  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
        "  - service: http://127.0.0.1:9\n"
    )
    _, rules, warnings = parse_config(text)
    assert [r.hostname for r in rules] == ["a.example.com"]
    assert len(warnings) == 1 and "http://127.0.0.1:9" in warnings[0]


def test_hostnames_are_lowercased():
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /c\ningress:\n  - hostname: A.Example.COM\n    service: http://127.0.0.1:1\n"
    assert parse_config(text)[1][0].hostname == "a.example.com"


def test_parse_unit_user():
    assert parse_unit_user(fixture("hotonchat.service")) == "toannc"
    assert parse_unit_user("[Service]\nExecStart=x\n") is None


def _seed(tmp_path, name="HotonChat", text=None, user="toannc", unit_user=None):
    home = tmp_path / "home" / user
    (home / ".cloudflared").mkdir(parents=True, exist_ok=True)
    (home / ".cloudflared" / f"config-{name}.yaml").write_text(text if text is not None else fixture("hotonchat.yaml"))
    sysd = tmp_path / "systemd"
    sysd.mkdir(exist_ok=True)
    if unit_user:
        (sysd / f"cloudflared-{name}.service").write_text(f"[Service]\nUser={unit_user}\n")
    return {user: home}, sysd


def test_scan_finds_config_and_unit_user(tmp_path):
    homes, sysd = _seed(tmp_path, unit_user="toannc")
    [c] = scan(homes, sysd, known=set())
    assert (c.name, c.user_name, c.uuid, c.known) == ("HotonChat", "toannc", HOTON_UUID, False)
    assert c.importable and c.rules == hoton_rules()


def test_scan_prefers_unit_user_over_directory_owner(tmp_path):
    homes, sysd = _seed(tmp_path, user="root", unit_user="toannc")
    assert scan(homes, sysd, known=set())[0].user_name == "toannc"


def test_scan_marks_known(tmp_path):
    homes, sysd = _seed(tmp_path)
    assert scan(homes, sysd, known={"HotonChat"})[0].known is True


def test_scan_invalid_yaml_not_importable(tmp_path):
    homes, sysd = _seed(tmp_path, text="tunnel: [unclosed\n")
    [c] = scan(homes, sysd, known=set())
    assert not c.importable and c.error and "YAML" in c.error


def test_scan_bad_name_not_importable(tmp_path):
    homes, sysd = _seed(tmp_path, name="bad name")
    [c] = scan(homes, sysd, known=set())
    assert not c.importable and "name" in c.error


def test_validation_problems_become_warnings_not_blockers(tmp_path):
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /c\ningress:\n  - hostname: a.example.com\n    service: http://localhost\n"
    homes, sysd = _seed(tmp_path, text=text)
    [c] = scan(homes, sysd, known=set())
    assert c.importable and any("service" in w for w in c.warnings)


def test_import_candidates_saves_only_new_importable(tmp_path):
    homes, sysd = _seed(tmp_path)
    cands = scan(homes, sysd, known=set())
    db = Database(tmp_path / "t.db")
    assert import_candidates(db, cands) == ["HotonChat"]
    saved = db.get_tunnel("HotonChat")
    assert saved.uuid == HOTON_UUID and saved.rules == hoton_rules() and saved.user_name == "toannc"
    cands2 = scan(homes, sysd, known={"HotonChat"})
    assert import_candidates(db, cands2) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_importer.py -q`
Expected: FAIL, `No module named 'app.importer'`.

- [ ] **Step 3: Implement**

`cfkit/app/importer.py`:
```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.db import Database
from app.models import Rule, Tunnel
from app.validate import NAME_RE, validate_tunnel

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
KNOWN_ORIGIN_KEYS = {"httpHostHeader", "noTLSVerify"}


@dataclass
class Candidate:
    name: str
    user_name: str
    uuid: str | None = None
    rules: list[Rule] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    known: bool = False
    error: str | None = None

    @property
    def importable(self) -> bool:
        return self.error is None and bool(self.uuid) and bool(self.rules)


def parse_config(text: str) -> tuple[str | None, list[Rule], list[str]]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise yaml.YAMLError("top level is not a mapping")
    uuid = str(data["tunnel"]) if data.get("tunnel") else None
    rules: list[Rule] = []
    warnings: list[str] = []
    for i, item in enumerate(data.get("ingress") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"ingress #{i}: not a mapping, skipped")
            continue
        service = str(item.get("service", ""))
        host = item.get("hostname")
        if not host:
            if service != "http_status:404":
                warnings.append(f"ingress #{i}: rule without hostname skipped ({service})")
            continue
        opts = item.get("originRequest") or {}
        extra = sorted(set(opts) - KNOWN_ORIGIN_KEYS)
        if extra:
            warnings.append(f"{host}{item.get('path') or ''}: unsupported originRequest keys dropped: {extra}")
        rules.append(Rule(
            hostname=str(host).lower(), service=service, path=item.get("path"),
            http_host_header=opts.get("httpHostHeader"), no_tls_verify=bool(opts.get("noTLSVerify", False)),
        ))
    return uuid, rules, warnings


def parse_unit_user(text: str) -> str | None:
    m = re.search(r"^User=(\S+)\s*$", text, re.MULTILINE)
    return m.group(1) if m else None


def scan(homes: dict[str, Path], systemd_dir: Path, known: set[str]) -> list[Candidate]:
    out: list[Candidate] = []
    for dir_user, home in homes.items():
        for p in sorted((home / ".cloudflared").glob("config-*.yaml")):
            name = p.name[len("config-"):-len(".yaml")]
            unit = systemd_dir / f"cloudflared-{name}.service"
            user = (parse_unit_user(unit.read_text()) if unit.exists() else None) or dir_user
            cand = Candidate(name=name, user_name=user, known=name in known)
            if not NAME_RE.fullmatch(name):
                cand.error = "name: not a valid tunnel name"
                out.append(cand)
                continue
            try:
                cand.uuid, cand.rules, cand.warnings = parse_config(p.read_text())
            except yaml.YAMLError as e:
                cand.error = f"invalid YAML: {e}".splitlines()[0]
                out.append(cand)
                continue
            if not cand.uuid or not UUID_RE.fullmatch(cand.uuid):
                cand.error = "missing or invalid tunnel id"
            elif not cand.rules:
                cand.error = "no usable ingress rules"
            else:
                cand.warnings += validate_tunnel(Tunnel(name, user, cand.rules))
            out.append(cand)
    return out


def import_candidates(db: Database, candidates: list[Candidate]) -> list[str]:
    saved: list[str] = []
    for c in candidates:
        if c.known or not c.importable or c.name in saved:
            continue
        db.save_tunnel(Tunnel(name=c.name, user_name=c.user_name, rules=c.rules, uuid=c.uuid))
        saved.append(c.name)
    return saved
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_importer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cfkit/app/importer.py cfkit/tests/test_importer.py
git commit -m "feat(cfkit): import existing cloudflared configs with warnings"
```

---

### Task 6: cloudflared + systemctl wrappers

**Files:**
- Create: `cfkit/app/cf.py`, `cfkit/app/svc.py`, `cfkit/tests/test_cf_svc.py`

**Interfaces:**
- Consumes: `Runner`, `Result`, `CommandError`, `tail` from `app.runner`.
- Produces: `Cloudflared(runner, bin_path: Callable[[], str | None])` with `create(name, user) -> str` (UUID), `delete(name, user) -> None`, `route_dns(name, hostname, user) -> str` (`""` or a warning), `validate(config_path: Path, user) -> None`, `version() -> str | None`.
- Produces: `unit_name(name)`, `Systemd(runner)` with `daemon_reload()`, `enable(name)`, `disable(name)`, `start(name)`, `stop(name)`, `restart(name)`, `is_active(name) -> str`, `logs(name, lines=200) -> str`. All raise `CommandError` on failure except `is_active`/`logs`.

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_cf_svc.py`:
```python
from pathlib import Path

import pytest

from app.cf import Cloudflared
from app.runner import CommandError, Result
from app.svc import Systemd, unit_name
from tests.fakes import FakeRunner

UUID = "11111111-2222-4333-8444-555555555555"
BIN = "/usr/bin/cloudflared"


def cf(fake, bin_path=BIN):
    return Cloudflared(fake, lambda: bin_path)


def test_create_parses_uuid_from_stderr_and_runs_as_user():
    f = FakeRunner()
    f.when_has("tunnel", "create", result=Result(0, "", f"Tunnel credentials written to /h/.cloudflared/{UUID}.json\nCreated tunnel T with id {UUID}"))
    assert cf(f).create("T", "toannc") == UUID
    assert f.calls[0] == ([BIN, "tunnel", "create", "T"], "toannc")


def test_create_failure_raises_with_stderr_tail():
    f = FakeRunner()
    f.when_has("create", result=Result(1, "", "Cannot determine default origin certificate path"))
    with pytest.raises(CommandError, match="origin certificate"):
        cf(f).create("T", "root")


def test_create_without_uuid_in_output_raises():
    f = FakeRunner()
    f.when_has("create", result=Result(0, "ok but no id", ""))
    with pytest.raises(CommandError, match="tunnel id"):
        cf(f).create("T", "root")


def test_not_installed_raises():
    with pytest.raises(CommandError, match="not installed"):
        cf(FakeRunner(), bin_path=None).create("T", "root")


def test_route_dns_ok_and_already_exists_warns_other_errors_raise():
    f = FakeRunner()
    assert cf(f).route_dns("T", "a.example.com", "root") == ""
    assert f.calls[0][0] == [BIN, "tunnel", "route", "dns", "T", "a.example.com"]
    f.when_has("route", result=Result(1, "", "An A, AAAA, or CNAME record with that host already exists."))
    assert "already exists" in cf(f).route_dns("T", "a.example.com", "root")
    f.when_has("route", result=Result(1, "", "authentication error"))
    with pytest.raises(CommandError, match="authentication"):
        cf(f).route_dns("T", "a.example.com", "root")


def test_validate_runs_ingress_validate_on_that_file_as_user():
    f = FakeRunner()
    cf(f).validate(Path("/h/c.yaml"), "toannc")
    assert f.calls[0] == ([BIN, "tunnel", "--config", "/h/c.yaml", "ingress", "validate"], "toannc")
    f.when_has("validate", result=Result(1, "", "bad ingress"))
    with pytest.raises(CommandError, match="bad ingress"):
        cf(f).validate(Path("/h/c.yaml"), "toannc")


def test_delete_uses_force_and_tolerates_missing_tunnel():
    f = FakeRunner()
    cf(f).delete("T", "root")
    assert f.calls[0][0] == [BIN, "tunnel", "delete", "-f", "T"]
    f.when_has("delete", result=Result(1, "", "Tunnel T not found"))
    cf(f).delete("T", "root")
    f.when_has("delete", result=Result(1, "", "connection refused"))
    with pytest.raises(CommandError):
        cf(f).delete("T", "root")


def test_version_parsing():
    f = FakeRunner()
    f.when_has("--version", result=Result(0, "cloudflared version 2025.8.1 (built 2025-08-20-1200 UTC)\n", ""))
    assert cf(f).version() == "2025.8.1"
    assert cf(f, bin_path=None).version() is None


def test_systemd_commands_and_failures():
    f = FakeRunner()
    s = Systemd(f)
    s.daemon_reload(); s.enable("T"); s.start("T"); s.restart("T"); s.stop("T"); s.disable("T")
    assert f.argv() == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "cloudflared-T.service"],
        ["systemctl", "start", "cloudflared-T.service"],
        ["systemctl", "restart", "cloudflared-T.service"],
        ["systemctl", "stop", "cloudflared-T.service"],
        ["systemctl", "disable", "cloudflared-T.service"],
    ]
    f.when_has("start", result=Result(1, "", "Unit not found"))
    with pytest.raises(CommandError, match="Unit not found"):
        s.start("T")


def test_is_active_reads_stdout_even_on_nonzero_exit():
    f = FakeRunner()
    f.when_has("is-active", result=Result(3, "inactive\n", ""))
    assert Systemd(f).is_active("T") == "inactive"
    f.when_has("is-active", result=Result(1, "", ""))
    assert Systemd(f).is_active("T") == "unknown"


def test_logs_command():
    f = FakeRunner()
    f.when_has("journalctl", result=Result(0, "line1\nline2\n", ""))
    assert Systemd(f).logs("T", 50) == "line1\nline2\n"
    assert f.calls[0][0] == ["journalctl", "-u", "cloudflared-T.service", "-n", "50", "--no-pager"]


def test_unit_name():
    assert unit_name("HotonChat") == "cloudflared-HotonChat.service"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_cf_svc.py -q`
Expected: FAIL, `No module named 'app.cf'`.

- [ ] **Step 3: Implement**

`cfkit/app/cf.py`:
```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.runner import CommandError, Runner, tail

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Cloudflared:
    def __init__(self, runner: Runner, bin_path: Callable[[], str | None]) -> None:
        self._r = runner
        self._bin_path = bin_path

    def _bin(self) -> str:
        b = self._bin_path()
        if not b:
            raise CommandError("cloudflared is not installed")
        return b

    def create(self, name: str, user: str) -> str:
        r = self._r.run([self._bin(), "tunnel", "create", name], user=user)
        if not r.ok:
            raise CommandError(tail(r))
        m = UUID_RE.search(r.out + "\n" + r.err)  # cloudflared logs to stderr
        if not m:
            raise CommandError("could not read tunnel id from cloudflared output")
        return m.group(0)

    def delete(self, name: str, user: str) -> None:
        r = self._r.run([self._bin(), "tunnel", "delete", "-f", name], user=user)
        if not r.ok and "not found" not in (r.out + r.err).lower():
            raise CommandError(tail(r))

    def route_dns(self, name: str, hostname: str, user: str) -> str:
        r = self._r.run([self._bin(), "tunnel", "route", "dns", name, hostname], user=user)
        if r.ok:
            return ""
        if "already exists" in (r.out + r.err).lower():
            return "DNS record already exists - verify it points to this tunnel"
        raise CommandError(tail(r))

    def validate(self, config_path: Path, user: str) -> None:
        r = self._r.run([self._bin(), "tunnel", "--config", str(config_path), "ingress", "validate"], user=user)
        if not r.ok:
            raise CommandError(tail(r))

    def version(self) -> str | None:
        b = self._bin_path()
        if not b:
            return None
        r = self._r.run([b, "--version"])
        m = re.search(r"version\s+(\S+)", r.out + r.err)
        return m.group(1) if m else None
```

`cfkit/app/svc.py`:
```python
from __future__ import annotations

from app.runner import CommandError, Runner, tail


def unit_name(name: str) -> str:
    return f"cloudflared-{name}.service"


class Systemd:
    def __init__(self, runner: Runner) -> None:
        self._r = runner

    def _ctl(self, *args: str) -> None:
        r = self._r.run(["systemctl", *args])
        if not r.ok:
            raise CommandError(tail(r))

    def daemon_reload(self) -> None:
        self._ctl("daemon-reload")

    def enable(self, name: str) -> None:
        self._ctl("enable", unit_name(name))

    def disable(self, name: str) -> None:
        self._ctl("disable", unit_name(name))

    def start(self, name: str) -> None:
        self._ctl("start", unit_name(name))

    def stop(self, name: str) -> None:
        self._ctl("stop", unit_name(name))

    def restart(self, name: str) -> None:
        self._ctl("restart", unit_name(name))

    def is_active(self, name: str) -> str:
        r = self._r.run(["systemctl", "is-active", unit_name(name)])
        return r.out.strip() or "unknown"

    def logs(self, name: str, lines: int = 200) -> str:
        r = self._r.run(["journalctl", "-u", unit_name(name), "-n", str(lines), "--no-pager"], timeout=15)
        return r.out or r.err
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_cf_svc.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cfkit/app/cf.py cfkit/app/svc.py cfkit/tests/test_cf_svc.py
git commit -m "feat(cfkit): cloudflared and systemctl wrappers"
```

---

### Task 7: Environment info (version, update check, cert.pem)

**Files:**
- Create: `cfkit/app/env.py`, `cfkit/tests/test_env.py`

**Interfaces:**
- Consumes: `Cloudflared.version()`, `Settings.cloudflared_bin`, `Settings.home_of`.
- Produces: `parse_version(s) -> tuple[int, ...]`; `LatestCache(fetch=fetch_latest_from_github, ttl=21600, clock=time.monotonic)` with `get(force=False) -> (version | None, error | None)`; `EnvService(cf, settings, cache)` with `collect(users: list[str], force=False) -> dict` with keys `installed, path, version, latest, update_available, latest_error, certs` (`certs`: `{user: bool}`).

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_env.py`:
```python
from pathlib import Path

from app.cf import Cloudflared
from app.config import Settings
from app.env import EnvService, LatestCache, parse_version
from app.runner import Result
from tests.fakes import FakeRunner


def test_parse_version_compares_numerically():
    assert parse_version("2025.8.1") == (2025, 8, 1)
    assert parse_version("2025.10.0") > parse_version("2025.9.5")
    assert parse_version("") == ()


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_cache_respects_ttl_and_force():
    calls = []
    clock = Clock()
    cache = LatestCache(fetch=lambda: calls.append(1) or "2025.9.0", ttl=100, clock=clock)
    assert cache.get() == ("2025.9.0", None)
    clock.t = 50
    cache.get()
    assert len(calls) == 1
    clock.t = 101
    cache.get()
    assert len(calls) == 2
    cache.get(force=True)
    assert len(calls) == 3


def test_cache_failure_reports_error_keeps_last_value_and_retries_sooner():
    clock = Clock()
    state = {"fail": False, "n": 0}

    def fetch():
        state["n"] += 1
        if state["fail"]:
            raise OSError("network down")
        return "2025.9.0"

    cache = LatestCache(fetch=fetch, ttl=1000, clock=clock)
    cache.get()
    state["fail"] = True
    clock.t = 1001
    ver, err = cache.get()
    assert ver == "2025.9.0" and "network down" in err
    clock.t = 1001 + 301  # error ttl is 300s
    cache.get()
    assert state["n"] == 3


def service(tmp_path, installed="2025.8.1", latest="2025.9.0", bin_path="/usr/bin/cloudflared", fail_latest=False):
    f = FakeRunner()
    f.when_has("--version", result=Result(0, f"cloudflared version {installed} (built x)\n", ""))
    settings = Settings(db_path=tmp_path / "d.db", home_of=lambda u: tmp_path / u, cloudflared_bin=lambda: bin_path)

    def fetch():
        if fail_latest:
            raise OSError("no route to github")
        return latest

    return EnvService(Cloudflared(f, settings.cloudflared_bin), settings, LatestCache(fetch=fetch))


def test_collect_update_available(tmp_path):
    (tmp_path / "toannc" / ".cloudflared").mkdir(parents=True)
    (tmp_path / "toannc" / ".cloudflared" / "cert.pem").write_text("x")
    info = service(tmp_path).collect(["toannc", "root"])
    assert info["installed"] and info["path"] == "/usr/bin/cloudflared"
    assert (info["version"], info["latest"], info["update_available"]) == ("2025.8.1", "2025.9.0", True)
    assert info["certs"] == {"toannc": True, "root": False}
    assert info["latest_error"] is None


def test_collect_up_to_date(tmp_path):
    assert service(tmp_path, installed="2025.9.0").collect([])["update_available"] is False


def test_collect_latest_unreachable_does_not_break(tmp_path):
    info = service(tmp_path, fail_latest=True).collect([])
    assert info["version"] == "2025.8.1" and info["latest"] is None
    assert info["update_available"] is False and "no route" in info["latest_error"]


def test_collect_not_installed(tmp_path):
    info = service(tmp_path, bin_path=None).collect(["root"])
    assert info["installed"] is False and info["version"] is None and info["update_available"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_env.py -q`
Expected: FAIL, `No module named 'app.env'`.

- [ ] **Step 3: Implement**

`cfkit/app/env.py`:
```python
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Callable

from app.cf import Cloudflared
from app.config import Settings

GITHUB_LATEST = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
ERROR_TTL = 300


def parse_version(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s))


def fetch_latest_from_github() -> str:
    req = urllib.request.Request(
        GITHUB_LATEST, headers={"User-Agent": "cfkit", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)["tag_name"].lstrip("v")


class LatestCache:
    def __init__(self, fetch: Callable[[], str] = fetch_latest_from_github, ttl: float = 6 * 3600,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch, self._ttl, self._clock = fetch, ttl, clock
        self._value: str | None = None
        self._error: str | None = None
        self._at: float | None = None

    def get(self, force: bool = False) -> tuple[str | None, str | None]:
        now = self._clock()
        ttl = self._ttl if self._error is None else ERROR_TTL
        if not force and self._at is not None and now - self._at < ttl:
            return self._value, self._error
        try:
            self._value, self._error = self._fetch(), None
        except Exception as e:  # network, JSON, missing key: never break the page
            self._error = str(e) or e.__class__.__name__
        self._at = now
        return self._value, self._error


class EnvService:
    def __init__(self, cf: Cloudflared, settings: Settings, cache: LatestCache) -> None:
        self._cf, self._s, self._cache = cf, settings, cache

    def collect(self, users: list[str], force: bool = False) -> dict:
        path = self._s.cloudflared_bin()
        installed = path is not None
        version = self._cf.version() if installed else None
        latest, err = self._cache.get(force)
        update = bool(version and latest and parse_version(latest) > parse_version(version))
        certs: dict[str, bool] = {}
        for u in users:
            try:
                certs[u] = (self._s.home_of(u) / ".cloudflared" / "cert.pem").is_file()
            except KeyError:
                certs[u] = False
        return {
            "installed": installed, "path": path, "version": version, "latest": latest,
            "update_available": update, "latest_error": err, "certs": certs,
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_env.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cfkit/app/env.py cfkit/tests/test_env.py
git commit -m "feat(cfkit): environment info with cached GitHub update check"
```

---

### Task 8: Flows (create / edit / delete / control / preview / update)

**Files:**
- Create: `cfkit/app/flows.py`, `cfkit/tests/conftest.py`, `cfkit/tests/test_flows.py`

**Interfaces:**
- Consumes: `Database`, `Cloudflared`, `Systemd`, `Runner`, `Settings`, `render_yaml`, `render_unit`, `validate_tunnel`, `USER_RE`, `hostnames`, `Step`, `CommandError`, `tail`.
- Produces: `FlowResult(steps: list[Step], data: dict)` with `.ok`; `Manager(db, cf, svc, runner, settings)` with
  - `config_path(user, name) -> Path`, `unit_path(name) -> Path`
  - `preview(name: str | None, t: Tunnel) -> dict` keys `yaml, diff, errors, added_hostnames, removed_hostnames`
  - `create(t) -> FlowResult`, `edit(name, t) -> FlowResult` (`data["removed_hostnames"]`), `delete(name, confirm) -> FlowResult` (`data["leftover_dns"]`), `control(name, action) -> FlowResult` (`action` in start/stop/restart), `update_cloudflared(version, restart_all) -> FlowResult`.

- [ ] **Step 1: Write conftest and the failing tests**

`cfkit/tests/conftest.py`:
```python
import pytest

from app.cf import Cloudflared
from app.config import Settings
from app.db import Database
from app.flows import Manager
from app.models import Rule, Tunnel
from app.runner import Result
from app.svc import Systemd
from tests.fakes import FakeRunner

UUID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def fake():
    f = FakeRunner()
    f.when_has("is-active", result=Result(0, "active\n", ""))
    f.when_has("tunnel", "create", result=Result(0, "", f"Created tunnel T with id {UUID}"))
    return f


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "db.sqlite",
        systemd_dir=tmp_path / "systemd",
        home_of=lambda u: tmp_path / "home" / u,
        group_of=lambda u: u,
        user_exists=lambda u: u in {"toannc", "root"},
        discover_homes=lambda: {u: tmp_path / "home" / u for u in ("toannc", "root") if (tmp_path / "home" / u).exists()},
        cloudflared_bin=lambda: "/usr/bin/cloudflared",
        chown=False,
        secret_key="test-secret",
        allowed_hosts=["testserver"],
    )


@pytest.fixture
def manager(settings, fake):
    return Manager(Database(settings.db_path), Cloudflared(fake, settings.cloudflared_bin), Systemd(fake), fake, settings)


def make_tunnel(name="Demo", hosts=("a.example.com",), user="toannc", uuid=None):
    return Tunnel(
        name=name, user_name=user, project="P", uuid=uuid,
        rules=[Rule(h, "http://127.0.0.1:5500") for h in hosts],
    )


def seed(manager, t):
    """Put a tunnel into DB + config file + unit file as if it was created earlier."""
    t.uuid = t.uuid or UUID
    manager.db.save_tunnel(t)
    manager._write_atomic(manager.config_path(t.user_name, t.name), manager._render_config(t), None)
    manager._write_atomic(manager.unit_path(t.name), "[Unit]\n", None)
    return t
```

`cfkit/tests/test_flows.py`:
```python
import threading

from app.models import Rule
from app.render import render_yaml
from app.runner import Result
from tests.conftest import UUID, make_tunnel, seed


def names(res):
    return [s.name for s in res.steps]


# ---------- create ----------

def test_create_happy_path_order_and_files(manager, fake, settings):
    t = make_tunnel(hosts=("a.example.com", "b.example.com"))
    res = manager.create(t)
    assert res.ok, res.steps

    argv = fake.argv()
    idx = lambda *tok: next(i for i, a in enumerate(argv) if all(x in a for x in tok))
    assert idx("tunnel", "create") < idx("route", "a.example.com") < idx("route", "b.example.com") \
        < idx("ingress", "validate") < idx("daemon-reload") < idx("enable") < idx("start")

    cfg = manager.config_path("toannc", "Demo")
    cred = settings.home_of("toannc") / ".cloudflared" / f"{UUID}.json"
    assert cfg.read_text() == render_yaml(UUID, str(cred), t.rules)
    unit = manager.unit_path("Demo").read_text()
    assert f"ExecStart=/usr/bin/cloudflared --config {cfg} tunnel run" in unit and "User=toannc" in unit
    assert manager.db.get_tunnel("Demo").uuid == UUID


def test_create_runs_cloudflared_as_the_service_user(manager, fake):
    manager.create(make_tunnel(user="root"))
    create_call = next(c for c in fake.calls if "create" in c[0] and "tunnel" in c[0])
    assert create_call[1] == "root"


def test_create_rejects_invalid_input_before_any_command(manager, fake):
    bad = make_tunnel(name="x\nrm -rf /")
    res = manager.create(bad)
    assert not res.ok and fake.calls == []
    assert manager.db.list_tunnels() == []


def test_create_rejects_unknown_system_user_and_duplicate_name(manager, fake):
    assert not manager.create(make_tunnel(user="ghost")).ok
    assert fake.calls == []
    manager.create(make_tunnel())
    fake.calls.clear()
    dup = manager.create(make_tunnel())
    assert not dup.ok and "already exists" in dup.steps[0].detail and fake.calls == []


def test_create_failure_rolls_back_everything(manager, fake, settings):
    fake.when_has("systemctl", "start", result=Result(1, "", "boom"))
    res = manager.create(make_tunnel())
    assert not res.ok
    failed = next(s for s in res.steps if not s.ok)
    assert failed.name == "start" and "boom" in failed.detail
    assert res.steps[-1].name == "rollback"
    assert not manager.config_path("toannc", "Demo").exists()
    assert not manager.unit_path("Demo").exists()
    assert manager.db.get_tunnel("Demo") is None
    assert fake.has_call("tunnel", "delete", "Demo")


def test_create_failure_before_tunnel_exists_skips_cloudflare_delete(manager, fake):
    fake.when_has("tunnel", "create", result=Result(1, "", "no cert"))
    res = manager.create(make_tunnel())
    assert not res.ok and not fake.has_call("tunnel", "delete")
    assert manager.db.get_tunnel("Demo") is None


def test_create_dns_already_exists_is_success_with_warning(manager, fake):
    fake.when_has("route", result=Result(1, "", "record already exists"))
    res = manager.create(make_tunnel())
    assert res.ok
    dns = next(s for s in res.steps if s.name.startswith("dns "))
    assert "verify" in dns.detail


# ---------- edit ----------

def test_edit_adds_rule_only_routes_new_hostname_and_keeps_uuid(manager, fake):
    seed(manager, make_tunnel(hosts=("a.example.com",)))
    fake.calls.clear()
    new = make_tunnel(hosts=("a.example.com", "c.example.com"))
    res = manager.edit("Demo", new)
    assert res.ok, res.steps
    assert not fake.has_call("tunnel", "create") and not fake.has_call("tunnel", "delete")
    routes = [a for a in fake.argv() if "route" in a]
    assert len(routes) == 1 and "c.example.com" in routes[0]
    assert fake.has_call("systemctl", "restart", "cloudflared-Demo.service")
    saved = manager.db.get_tunnel("Demo")
    assert saved.uuid == UUID and [r.hostname for r in saved.rules] == ["a.example.com", "c.example.com"]
    assert "c.example.com" in manager.config_path("toannc", "Demo").read_text()
    assert len(manager.db.list_backups("Demo")) == 1


def test_edit_reports_removed_hostnames(manager):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com",)))
    assert res.ok and res.data["removed_hostnames"] == ["b.example.com"]


def test_edit_ignores_name_and_user_changes(manager):
    seed(manager, make_tunnel(user="toannc"))
    changed = make_tunnel(name="Other", user="root")
    res = manager.edit("Demo", changed)
    assert res.ok
    saved = manager.db.get_tunnel("Demo")
    assert saved.user_name == "toannc" and manager.db.get_tunnel("Other") is None


def test_edit_inactive_tunnel_is_not_started(manager, fake):
    seed(manager, make_tunnel())
    fake.when_has("is-active", result=Result(3, "inactive\n", ""))
    fake.calls.clear()
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert res.ok
    assert not fake.has_call("systemctl", "restart") and not fake.has_call("systemctl", "start")


def test_edit_restart_failure_restores_previous_yaml_and_db(manager, fake):
    old = seed(manager, make_tunnel(hosts=("a.example.com",)))
    cfg = manager.config_path("toannc", "Demo")
    before = cfg.read_text()
    fake.when_has("systemctl", "restart", result=Result(1, "", "restart failed"))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok
    assert res.steps[-1].name == "restore previous config"
    assert cfg.read_text() == before
    assert [r.hostname for r in manager.db.get_tunnel("Demo").rules] == ["a.example.com"]


def test_edit_validate_failure_leaves_everything_untouched(manager, fake):
    seed(manager, make_tunnel())
    cfg = manager.config_path("toannc", "Demo")
    before = cfg.read_text()
    fake.when_has("ingress", "validate", result=Result(1, "", "invalid ingress"))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok and cfg.read_text() == before
    assert not cfg.with_name(cfg.name + ".new").exists()
    assert not fake.has_call("systemctl", "restart")


def test_edit_invalid_input_and_unknown_tunnel(manager, fake):
    assert not manager.edit("Ghost", make_tunnel()).ok
    seed(manager, make_tunnel())
    fake.calls.clear()
    bad = make_tunnel()
    bad.rules = [Rule("A B", "http://127.0.0.1:1")]
    assert not manager.edit("Demo", bad).ok and fake.calls == []


# ---------- delete ----------

def test_delete_requires_matching_confirmation(manager, fake):
    seed(manager, make_tunnel())
    fake.calls.clear()
    res = manager.delete("Demo", "demo")
    assert not res.ok and fake.calls == [] and manager.db.get_tunnel("Demo") is not None


def test_delete_removes_files_db_and_reports_leftover_dns(manager, fake):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    res = manager.delete("Demo", "Demo")
    assert res.ok, res.steps
    assert res.data["leftover_dns"] == ["a.example.com", "b.example.com"]
    assert not manager.config_path("toannc", "Demo").exists() and not manager.unit_path("Demo").exists()
    assert manager.db.get_tunnel("Demo") is None
    assert fake.has_call("tunnel", "delete", "Demo")


def test_delete_is_idempotent_when_files_already_missing(manager):
    seed(manager, make_tunnel())
    manager.config_path("toannc", "Demo").unlink()
    manager.unit_path("Demo").unlink()
    assert manager.delete("Demo", "Demo").ok


def test_delete_keeps_db_row_when_cloudflare_delete_fails(manager, fake):
    seed(manager, make_tunnel())
    fake.when_has("tunnel", "delete", result=Result(1, "", "tunnel has active connections"))
    res = manager.delete("Demo", "Demo")
    assert not res.ok and manager.db.get_tunnel("Demo") is not None


# ---------- control / lock / preview / update ----------

def test_control_runs_systemctl_and_rejects_unknown_action(manager, fake):
    seed(manager, make_tunnel())
    fake.calls.clear()
    assert manager.control("Demo", "restart").ok
    assert fake.argv() == [["systemctl", "restart", "cloudflared-Demo.service"]]
    assert not manager.control("Demo", "rm -rf").ok
    assert not manager.control("Ghost", "start").ok


def test_second_operation_on_same_tunnel_is_refused_while_busy(manager):
    seed(manager, make_tunnel())
    lock = manager._lock_for("Demo")
    assert lock.acquire(blocking=False)
    try:
        res = manager.control("Demo", "restart")
    finally:
        lock.release()
    assert not res.ok and res.steps[0].name == "lock"


def test_preview_shows_diff_and_hostname_changes(manager):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    p = manager.preview("Demo", make_tunnel(hosts=("a.example.com", "c.example.com")))
    assert p["errors"] == []
    assert p["added_hostnames"] == ["c.example.com"] and p["removed_hostnames"] == ["b.example.com"]
    assert "+  - hostname: c.example.com" in p["diff"] and "-  - hostname: b.example.com" in p["diff"]
    assert "http_status:404" in p["yaml"]


def test_preview_for_new_tunnel_flags_errors_and_duplicates(manager):
    seed(manager, make_tunnel())
    p = manager.preview(None, make_tunnel())  # same name as existing
    assert any("already exists" in e for e in p["errors"])
    dup = make_tunnel(name="Fresh")
    dup.rules = [Rule("a.example.com", "http://127.0.0.1:1", path="/x")] * 2
    assert any("duplicate" in e for e in manager.preview(None, dup)["errors"])
    assert manager.preview(None, make_tunnel(name="Fresh", user="ghost"))["errors"]


def test_update_cloudflared_validates_version_and_restarts_only_active(manager, fake):
    assert not manager.update_cloudflared("1.0; rm -rf /", True).ok
    assert fake.calls == []

    seed(manager, make_tunnel("One"))
    seed(manager, make_tunnel("Two"))
    fake.when_has("dpkg", "--print-architecture", result=Result(0, "amd64\n", ""))
    fake.when_has("is-active", "cloudflared-Two.service", result=Result(3, "inactive\n", ""))
    fake.calls.clear()
    res = manager.update_cloudflared("2025.9.0", restart_all=True)
    assert res.ok, res.steps
    curl = next(a for a in fake.argv() if a[0] == "curl")
    assert curl[-1] == "https://github.com/cloudflare/cloudflared/releases/download/2025.9.0/cloudflared-linux-amd64.deb"
    assert fake.has_call("dpkg", "-i")
    assert fake.has_call("systemctl", "restart", "cloudflared-One.service")
    assert not fake.has_call("systemctl", "restart", "cloudflared-Two.service")


def test_update_cloudflared_install_only_does_not_restart(manager, fake):
    seed(manager, make_tunnel("One"))
    fake.when_has("--print-architecture", result=Result(0, "arm64\n", ""))
    fake.calls.clear()
    assert manager.update_cloudflared("2025.9.0", restart_all=False).ok
    assert not fake.has_call("systemctl", "restart")


def test_update_cloudflared_unsupported_arch_fails(manager, fake):
    fake.when_has("--print-architecture", result=Result(0, "riscv64\n", ""))
    res = manager.update_cloudflared("2025.9.0", True)
    assert not res.ok and not fake.has_call("dpkg", "-i")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_flows.py -q`
Expected: collection error, `No module named 'app.flows'`.

- [ ] **Step 3: Implement**

`cfkit/app/flows.py`:
```python
from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.cf import Cloudflared
from app.config import Settings
from app.db import Database
from app.models import Step, Tunnel, hostnames
from app.render import render_unit, render_yaml
from app.runner import CommandError, Runner, tail
from app.svc import Systemd, unit_name
from app.validate import USER_RE, validate_tunnel

VERSION_RE = re.compile(r"\d+(\.\d+){1,3}")
ARCHES = {"amd64", "arm64"}


@dataclass
class FlowResult:
    steps: list[Step]
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


class Abort(Exception):
    """A recorded step failed; stop the flow."""


class StepLog:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def do(self, name: str, fn: Callable, *args, detail_from_result: bool = False):
        try:
            result = fn(*args)
        except Exception as e:  # CommandError, OSError, ...: always reported as a failed step
            self.steps.append(Step(name, False, str(e) or e.__class__.__name__))
            raise Abort() from e
        self.steps.append(Step(name, True, (result or "") if detail_from_result else ""))
        return result


def _fail(name: str, detail: str) -> FlowResult:
    return FlowResult([Step(name, False, detail)])


class Manager:
    def __init__(self, db: Database, cf: Cloudflared, svc: Systemd, runner: Runner, settings: Settings) -> None:
        self.db, self.cf, self.svc, self.runner, self.s = db, cf, svc, runner, settings
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ----- paths / locking / file helpers -----

    def config_path(self, user: str, name: str) -> Path:
        return self.s.home_of(user) / ".cloudflared" / f"config-{name}.yaml"

    def cred_path(self, user: str, uuid: str) -> Path:
        return self.s.home_of(user) / ".cloudflared" / f"{uuid}.json"

    def unit_path(self, name: str) -> Path:
        return self.s.systemd_dir / unit_name(name)

    def _lock_for(self, name: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(name, threading.Lock())

    def _locked(self, name: str, fn: Callable[[], FlowResult]) -> FlowResult:
        lock = self._lock_for(name)
        if not lock.acquire(blocking=False):
            return _fail("lock", "another operation is running on this tunnel")
        try:
            return fn()
        finally:
            lock.release()

    def _write_file(self, path: Path, text: str, owner: str | None, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        os.chmod(path, mode)
        if owner and self.s.chown:
            shutil.chown(path, user=owner, group=self.s.group_of(owner))

    def _write_atomic(self, path: Path, text: str, owner: str | None) -> None:
        tmp = path.with_name(path.name + ".tmp")
        self._write_file(tmp, text, owner)
        os.replace(tmp, path)

    def _render_config(self, t: Tunnel) -> str:
        uuid = t.uuid or "<uuid-assigned-on-create>"
        return render_yaml(uuid, str(self.cred_path(t.user_name, uuid)), t.rules)

    def _stage_config(self, t: Tunnel) -> Path:
        """Write `<config>.new` and validate it with cloudflared; caller publishes it."""
        tmp = self.config_path(t.user_name, t.name)
        tmp = tmp.with_name(tmp.name + ".new")
        self._write_file(tmp, self._render_config(t), t.user_name)
        try:
            self.cf.validate(tmp, t.user_name)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    def _write_unit(self, t: Tunnel) -> None:
        text = render_unit(
            name=t.name,
            cloudflared_bin=self.s.cloudflared_bin() or "/usr/bin/cloudflared",
            config_file=str(self.config_path(t.user_name, t.name)),
            user=t.user_name,
            group=self.s.group_of(t.user_name),
            home=str(self.s.home_of(t.user_name)),
        )
        self._write_atomic(self.unit_path(t.name), text, None)

    def _remove_files(self, t: Tunnel) -> None:
        cfg = self.config_path(t.user_name, t.name)
        paths = [self.unit_path(t.name), cfg, cfg.with_name(cfg.name + ".new")]
        if t.uuid:
            paths.append(self.cred_path(t.user_name, t.uuid))
        for p in paths:
            p.unlink(missing_ok=True)

    @staticmethod
    def _quiet(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except CommandError:
            pass

    def _check(self, t: Tunnel, creating: bool) -> list[str]:
        errs = validate_tunnel(t)
        if USER_RE.fullmatch(t.user_name) and not self.s.user_exists(t.user_name):
            errs.append("user: no such system user")
        if creating and not errs and self.db.get_tunnel(t.name) is not None:
            errs.append("name: tunnel already exists")
        return errs

    # ----- preview -----

    def preview(self, name: str | None, t: Tunnel) -> dict:
        old = self.db.get_tunnel(name) if name else None
        if old:
            t.name, t.user_name, t.uuid = old.name, old.user_name, old.uuid
        errors = self._check(t, creating=old is None and name is None)
        try:
            new_yaml = self._render_config(t)
        except KeyError:
            new_yaml = ""
        current = ""
        if old:
            path = self.config_path(old.user_name, old.name)
            current = path.read_text() if path.exists() else ""
        diff = "".join(difflib.unified_diff(current.splitlines(True), new_yaml.splitlines(True), "current", "new"))
        before = hostnames(old) if old else []
        after = hostnames(t)
        return {
            "yaml": new_yaml, "diff": diff, "errors": errors,
            "added_hostnames": [h for h in after if h not in before],
            "removed_hostnames": [h for h in before if h not in after],
        }

    # ----- create -----

    def create(self, t: Tunnel) -> FlowResult:
        errs = self._check(t, creating=True)
        if errs:
            return _fail("validate", "; ".join(errs))
        return self._locked(t.name, lambda: self._create(t))

    def _create(self, t: Tunnel) -> FlowResult:
        log = StepLog()
        try:
            t.uuid = log.do("tunnel create", self.cf.create, t.name, t.user_name)
            log.do("save", self.db.save_tunnel, t)
            for h in hostnames(t):
                log.do(f"dns {h}", self.cf.route_dns, t.name, h, t.user_name, detail_from_result=True)
            log.do("write config", self._publish_config, t)
            log.do("write unit", self._write_unit, t)
            log.do("daemon-reload", self.svc.daemon_reload)
            log.do("enable", self.svc.enable, t.name)
            log.do("start", self.svc.start, t.name)
        except Abort:
            self._rollback_create(t, log)
        return FlowResult(log.steps)

    def _publish_config(self, t: Tunnel) -> None:
        tmp = self._stage_config(t)
        os.replace(tmp, self.config_path(t.user_name, t.name))

    def _rollback_create(self, t: Tunnel, log: StepLog) -> None:
        notes: list[str] = []
        self._quiet(self.svc.stop, t.name)
        self._quiet(self.svc.disable, t.name)
        self._remove_files(t)
        self._quiet(self.svc.daemon_reload)
        if t.uuid:
            try:
                self.cf.delete(t.name, t.user_name)
            except CommandError as e:
                notes.append(f"tunnel delete failed: {e}")
            routed = [s.name[4:] for s in log.steps if s.name.startswith("dns ") and s.ok]
            if routed:
                notes.append(f"DNS records left in Cloudflare: {', '.join(routed)}")
        self.db.delete_tunnel(t.name)
        log.steps.append(Step("rollback", True, "; ".join(notes)))

    # ----- edit -----

    def edit(self, name: str, new: Tunnel) -> FlowResult:
        old = self.db.get_tunnel(name)
        if old is None:
            return _fail("lookup", "tunnel not found")
        new.name, new.user_name, new.uuid = old.name, old.user_name, old.uuid
        errs = self._check(new, creating=False)
        if errs:
            return _fail("validate", "; ".join(errs))
        return self._locked(name, lambda: self._edit(old, new))

    def _edit(self, old: Tunnel, new: Tunnel) -> FlowResult:
        log = StepLog()
        path = self.config_path(old.user_name, old.name)
        prev_text = path.read_text() if path.exists() else None
        was_active = self.svc.is_active(old.name) == "active"
        added = [h for h in hostnames(new) if h not in hostnames(old)]
        removed = [h for h in hostnames(old) if h not in hostnames(new)]
        replaced = False
        try:
            tmp = log.do("validate config", self._stage_config, new)
            if prev_text is not None:
                log.do("backup", self.db.add_backup, old.name, prev_text)
            log.do("write config", os.replace, tmp, path)
            replaced = True
            log.do("save", self.db.save_tunnel, new)
            for h in added:
                log.do(f"dns {h}", self.cf.route_dns, new.name, h, new.user_name, detail_from_result=True)
            if was_active:
                log.do("restart", self.svc.restart, new.name)
        except Abort:
            if replaced:
                self._restore(old, prev_text, path, was_active, log)
            return FlowResult(log.steps, {"removed_hostnames": removed})
        return FlowResult(log.steps, {"removed_hostnames": removed})

    def _restore(self, old: Tunnel, prev_text: str | None, path: Path, was_active: bool, log: StepLog) -> None:
        problems: list[str] = []
        try:
            if prev_text is None:
                path.unlink(missing_ok=True)
            else:
                self._write_atomic(path, prev_text, old.user_name)
            self.db.save_tunnel(old)
            if was_active:
                self.svc.restart(old.name)
        except Exception as e:
            problems.append(str(e) or e.__class__.__name__)
        log.steps.append(Step("restore previous config", not problems, "; ".join(problems)))

    # ----- delete -----

    def delete(self, name: str, confirm: str) -> FlowResult:
        t = self.db.get_tunnel(name)
        if t is None:
            return _fail("lookup", "tunnel not found")
        if confirm != name:
            return _fail("confirm", "confirmation does not match the tunnel name")
        return self._locked(name, lambda: self._delete(t))

    def _delete(self, t: Tunnel) -> FlowResult:
        log = StepLog()
        try:
            log.do("stop", self._quiet, self.svc.stop, t.name)
            log.do("disable", self._quiet, self.svc.disable, t.name)
            log.do("remove files", self._remove_files, t)
            log.do("daemon-reload", self.svc.daemon_reload)
            log.do("tunnel delete", self.cf.delete, t.name, t.user_name)
            log.do("remove from db", self.db.delete_tunnel, t.name)
        except Abort:
            return FlowResult(log.steps)
        return FlowResult(log.steps, {"leftover_dns": hostnames(t)})

    # ----- start / stop / restart -----

    def control(self, name: str, action: str) -> FlowResult:
        if action not in ("start", "stop", "restart"):
            return _fail("action", "unknown action")
        if self.db.get_tunnel(name) is None:
            return _fail("lookup", "tunnel not found")

        def run() -> FlowResult:
            log = StepLog()
            try:
                log.do(action, getattr(self.svc, action), name)
            except Abort:
                pass
            return FlowResult(log.steps)

        return self._locked(name, run)

    # ----- cloudflared update -----

    def update_cloudflared(self, version: str, restart_all: bool) -> FlowResult:
        if not VERSION_RE.fullmatch(version):
            return _fail("validate", "invalid version")
        return self._locked("__update__", lambda: self._update(version, restart_all))

    def _sh(self, args: list[str], timeout: int) -> None:
        r = self.runner.run(args, timeout=timeout)
        if not r.ok:
            raise CommandError(tail(r))

    def _arch(self) -> str:
        r = self.runner.run(["dpkg", "--print-architecture"])
        arch = r.out.strip()
        if not r.ok or arch not in ARCHES:
            raise CommandError(f"unsupported architecture: {arch or tail(r)}")
        return arch

    def _update(self, version: str, restart_all: bool) -> FlowResult:
        log = StepLog()
        try:
            arch = log.do("detect architecture", self._arch)
            url = f"https://github.com/cloudflare/cloudflared/releases/download/{version}/cloudflared-linux-{arch}.deb"
            with tempfile.TemporaryDirectory() as d:
                deb = str(Path(d) / "cloudflared.deb")
                log.do("download", self._sh, ["curl", "-fsSL", "-o", deb, url], 300)
                log.do("install", self._sh, ["dpkg", "-i", deb], 120)
            if restart_all:
                for t in self.db.list_tunnels():
                    if self.svc.is_active(t.name) == "active":
                        log.do(f"restart {t.name}", self.svc.restart, t.name)
        except Abort:
            pass
        return FlowResult(log.steps)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest tests/test_flows.py -q`
Expected: all pass. If `test_edit_ignores_name_and_user_changes` fails because `make_tunnel(name="Other")` collides, re-check that `edit()` overwrites `new.name`/`new.user_name` before `_check`.

- [ ] **Step 5: Run the whole suite**

Run: `cd cfkit && .venv/bin/pytest -q`
Expected: everything so far passes.

- [ ] **Step 6: Commit**

```bash
git add cfkit/app/flows.py cfkit/tests/conftest.py cfkit/tests/test_flows.py
git commit -m "feat(cfkit): create/edit/delete/control/update flows with rollback"
```

---

### Task 9: Auth + HTTP API + entrypoint

**Files:**
- Create: `cfkit/app/auth.py`, `cfkit/app/api.py`, `cfkit/app/main.py`, `cfkit/tests/test_auth.py`, `cfkit/tests/test_api.py`
- Create: `cfkit/static/index.html` (placeholder-free minimal page used by the static-file test; replaced in Task 10)

**Interfaces:**
- Consumes: `Manager`, `EnvService`, `Settings`, `importer.scan/import_candidates`, `Systemd.logs/is_active`.
- Produces: `hash_password(pw) -> str`, `verify_password(hash, pw) -> bool`, `LoginLimiter(max_failures=5, window=300, clock=time.monotonic)` with `blocked(ip)`, `fail(ip)`, `reset(ip)`; `create_app(settings, manager, env_service) -> FastAPI`; `build_app(settings) -> FastAPI`.
- HTTP API (all JSON; mutating routes need session + `X-CSRF-Token`):
  - `POST /api/login {password}` → `{ok, csrf}`; `POST /api/logout`; `GET /api/session` → `{authenticated, csrf?}`
  - `GET /api/env`; `POST /api/env/refresh`; `POST /api/env/update {restart: bool}`
  - `GET /api/tunnels`; `GET /api/tunnels/{name}`; `POST /api/tunnels`; `POST /api/tunnels/preview {name|null, tunnel}`; `PUT /api/tunnels/{name}`; `POST /api/tunnels/{name}/delete {confirm}`; `POST /api/tunnels/{name}/{start|stop|restart}`; `GET /api/tunnels/{name}/logs?lines=`
  - `GET /api/import/scan`; `POST /api/import {names: [..]}`
  - Flow responses: `{"ok": bool, "steps": [{name, ok, detail}], "data": {...}}` (HTTP 200 even when `ok` is false).

- [ ] **Step 1: Write the failing tests**

`cfkit/tests/test_auth.py`:
```python
from app.auth import LoginLimiter, hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("s3cret")
    assert h != "s3cret" and h.startswith("$argon2")
    assert verify_password(h, "s3cret") is True
    assert verify_password(h, "wrong") is False


def test_verify_with_empty_or_garbage_hash_is_false():
    assert verify_password("", "x") is False
    assert verify_password("not-a-hash", "x") is False


def test_limiter_blocks_after_max_failures_and_recovers():
    t = {"now": 0.0}
    lim = LoginLimiter(max_failures=3, window=100, clock=lambda: t["now"])
    for _ in range(3):
        assert not lim.blocked("1.1.1.1")
        lim.fail("1.1.1.1")
    assert lim.blocked("1.1.1.1") and not lim.blocked("2.2.2.2")
    t["now"] = 101
    assert not lim.blocked("1.1.1.1")


def test_limiter_reset_clears():
    lim = LoginLimiter(max_failures=1, window=100)
    lim.fail("ip")
    assert lim.blocked("ip")
    lim.reset("ip")
    assert not lim.blocked("ip")
```

`cfkit/tests/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth import hash_password
from app.env import EnvService, LatestCache
from tests.conftest import UUID, make_tunnel, seed
from tests.samples import fixture

PW = "pw-for-tests"


@pytest.fixture
def client(settings, manager, fake, tmp_path):
    settings.password_hash = hash_password(PW)
    env = EnvService(manager.cf, settings, LatestCache(fetch=lambda: "2099.1.1"))
    from app.runner import Result
    fake.when_has("--version", result=Result(0, "cloudflared version 2025.8.1 (built x)\n", ""))
    return TestClient(create_app(settings, manager, env))


def login(client):
    assert client.post("/api/login", json={"password": PW}).status_code == 200
    return {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}


def body(name="Demo", hosts=("a.example.com",), user="toannc"):
    return {
        "name": name, "user_name": user, "project": "P", "note": "",
        "rules": [{"hostname": h, "service": "http://127.0.0.1:5500"} for h in hosts],
    }


# ---- auth / transport security ----

def test_api_requires_login(client):
    for path in ("/api/tunnels", "/api/env", "/api/import/scan"):
        assert client.get(path).status_code == 401


def test_session_endpoint_reports_state(client):
    assert client.get("/api/session").json() == {"authenticated": False}
    login(client)
    s = client.get("/api/session").json()
    assert s["authenticated"] is True and s["csrf"]


def test_wrong_password_401_and_sixth_attempt_429_even_with_right_password(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"password": PW}).status_code == 429


def test_mutations_require_csrf_header(client):
    login(client)
    assert client.post("/api/tunnels", json=body()).status_code == 403
    assert client.post("/api/tunnels", json=body(), headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_foreign_host_header_rejected(client):
    r = client.get("/api/session", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_logout_clears_session(client):
    h = login(client)
    assert client.post("/api/logout", headers=h).status_code == 200
    assert client.get("/api/tunnels").status_code == 401


def test_security_headers_present(client):
    r = client.get("/api/session")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


# ---- tunnels ----

def test_create_list_get_roundtrip(client):
    h = login(client)
    res = client.post("/api/tunnels", json=body(hosts=("a.example.com", "b.example.com")), headers=h).json()
    assert res["ok"], res
    [row] = client.get("/api/tunnels").json()
    assert row["name"] == "Demo" and row["uuid"] == UUID and row["status"] == "active"
    assert row["hostnames"] == ["a.example.com", "b.example.com"] and row["rule_count"] == 2
    full = client.get("/api/tunnels/Demo").json()
    assert full["rules"][0]["hostname"] == "a.example.com"


def test_create_normalizes_hostname_case_and_blank_optionals(client):
    h = login(client)
    b = body(hosts=("A.Example.COM",))
    b["rules"][0].update(path="", http_host_header="  ")
    assert client.post("/api/tunnels", json=b, headers=h).json()["ok"]
    r = client.get("/api/tunnels/Demo").json()["rules"][0]
    assert r["hostname"] == "a.example.com" and r["path"] is None and r["http_host_header"] is None


def test_create_invalid_returns_ok_false_with_reason(client, fake):
    h = login(client)
    res = client.post("/api/tunnels", json=body(name="bad name"), headers=h).json()
    assert res["ok"] is False and "name" in res["steps"][0]["detail"]
    assert not fake.has_call("tunnel", "create")


def test_unknown_tunnel_404(client):
    login(client)
    assert client.get("/api/tunnels/Ghost").status_code == 404
    assert client.get("/api/tunnels/Ghost/logs").status_code == 404


def test_edit_via_put(client, manager):
    h = login(client)
    seed(manager, make_tunnel())
    res = client.put("/api/tunnels/Demo", json=body(hosts=("a.example.com", "z.example.com")), headers=h).json()
    assert res["ok"], res
    assert [r["hostname"] for r in client.get("/api/tunnels/Demo").json()["rules"]] == ["a.example.com", "z.example.com"]


def test_preview_endpoint(client, manager):
    h = login(client)
    seed(manager, make_tunnel())
    p = client.post("/api/tunnels/preview", json={"name": "Demo", "tunnel": body(hosts=("a.example.com", "n.example.com"))}, headers=h).json()
    assert p["errors"] == [] and p["added_hostnames"] == ["n.example.com"] and "n.example.com" in p["yaml"]


def test_control_logs_and_delete(client, manager, fake):
    from app.runner import Result
    h = login(client)
    seed(manager, make_tunnel())
    assert client.post("/api/tunnels/Demo/restart", headers=h).json()["ok"]
    assert client.post("/api/tunnels/Demo/explode", headers=h).json()["ok"] is False
    fake.when_has("journalctl", result=Result(0, "hello log\n", ""))
    assert client.get("/api/tunnels/Demo/logs?lines=5").json()["logs"] == "hello log\n"
    assert client.post("/api/tunnels/Demo/delete", json={"confirm": "nope"}, headers=h).json()["ok"] is False
    res = client.post("/api/tunnels/Demo/delete", json={"confirm": "Demo"}, headers=h).json()
    assert res["ok"] and res["data"]["leftover_dns"] == ["a.example.com"]


def test_logs_lines_are_clamped(client, manager, fake):
    login(client)
    seed(manager, make_tunnel())
    client.get("/api/tunnels/Demo/logs?lines=999999")
    call = next(a for a in fake.argv() if a[0] == "journalctl")
    assert call[call.index("-n") + 1] == "1000"


# ---- env ----

def test_env_endpoint_and_refresh(client, tmp_path, manager):
    login(client)
    seed(manager, make_tunnel(user="toannc"))
    info = client.get("/api/env").json()
    assert info["version"] == "2025.8.1" and info["latest"] == "2099.1.1" and info["update_available"] is True
    assert info["certs"] == {"toannc": False}
    h = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
    assert client.post("/api/env/refresh", headers=h).status_code == 200


def test_env_survives_unreachable_github(settings, manager, fake):
    from app.runner import Result
    settings.password_hash = hash_password(PW)
    fake.when_has("--version", result=Result(0, "cloudflared version 2025.8.1\n", ""))

    def boom():
        raise OSError("offline")

    c = TestClient(create_app(settings, manager, EnvService(manager.cf, settings, LatestCache(fetch=boom))))
    login(c)
    r = c.get("/api/env")
    assert r.status_code == 200 and r.json()["latest_error"] == "offline" and r.json()["version"] == "2025.8.1"


def test_env_update_uses_latest_version(client, fake):
    from app.runner import Result
    h = login(client)
    fake.when_has("--print-architecture", result=Result(0, "amd64\n", ""))
    res = client.post("/api/env/update", json={"restart": False}, headers=h).json()
    assert res["ok"], res
    curl = next(a for a in fake.argv() if a[0] == "curl")
    assert "/2099.1.1/" in curl[-1]


# ---- import ----

def test_import_scan_and_commit(client, settings, tmp_path):
    h = login(client)
    cfg_dir = tmp_path / "home" / "toannc" / ".cloudflared"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config-HotonChat.yaml").write_text(fixture("hotonchat.yaml"))
    [c] = client.get("/api/import/scan").json()
    assert c["name"] == "HotonChat" and c["importable"] and not c["known"] and c["rule_count"] == 8
    res = client.post("/api/import", json={"names": ["HotonChat", "Nope"]}, headers=h).json()
    assert res["imported"] == ["HotonChat"]
    assert client.get("/api/tunnels").json()[0]["name"] == "HotonChat"
    assert client.get("/api/import/scan").json()[0]["known"] is True


# ---- static ----

def test_index_served_without_login(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>" in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `cd cfkit && .venv/bin/pytest tests/test_auth.py tests/test_api.py -q`
Expected: FAIL, `No module named 'app.auth'`.

- [ ] **Step 3: Implement auth**

`cfkit/app/auth.py`:
```python
from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class LoginLimiter:
    """In-memory per-IP failure counter inside a sliding window."""

    def __init__(self, max_failures: int = 5, window: float = 300, clock: Callable[[], float] = time.monotonic) -> None:
        self._max, self._window, self._clock = max_failures, window, clock
        self._fails: dict[str, list[float]] = defaultdict(list)

    def _recent(self, ip: str) -> list[float]:
        now = self._clock()
        self._fails[ip] = [t for t in self._fails[ip] if now - t < self._window]
        return self._fails[ip]

    def blocked(self, ip: str) -> bool:
        return len(self._recent(ip)) >= self._max

    def fail(self, ip: str) -> None:
        self._recent(ip).append(self._clock())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)
```

- [ ] **Step 4: Implement the API**

`cfkit/app/api.py`:
```python
from __future__ import annotations

import hmac
import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import importer
from app.auth import LoginLimiter, verify_password
from app.config import Settings
from app.env import EnvService
from app.flows import FlowResult, Manager
from app.models import Rule, Tunnel, hostnames

CSP = "default-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


class LoginIn(BaseModel):
    password: str


class RuleIn(BaseModel):
    hostname: str
    service: str
    path: str | None = None
    http_host_header: str | None = None
    no_tls_verify: bool = False


class TunnelIn(BaseModel):
    name: str
    user_name: str
    project: str = ""
    note: str = ""
    rules: list[RuleIn] = []


class PreviewIn(BaseModel):
    name: str | None = None
    tunnel: TunnelIn


class ConfirmIn(BaseModel):
    confirm: str


class UpdateIn(BaseModel):
    restart: bool = False


class ImportIn(BaseModel):
    names: list[str]


def _blank_to_none(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def to_tunnel(b: TunnelIn) -> Tunnel:
    return Tunnel(
        name=b.name, user_name=b.user_name, project=b.project.strip(), note=b.note.strip(),
        rules=[
            Rule(
                hostname=r.hostname.strip().lower(), service=r.service.strip(),
                path=_blank_to_none(r.path), http_host_header=_blank_to_none(r.http_host_header),
                no_tls_verify=r.no_tls_verify,
            )
            for r in b.rules
        ],
    )


def out(res: FlowResult) -> dict:
    return {"ok": res.ok, "steps": [asdict(s) for s in res.steps], "data": res.data}


def create_app(settings: Settings, manager: Manager, env_service: EnvService) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    limiter = LoginLimiter()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="cfkit_session",
        same_site="strict", https_only=settings.https_only, max_age=8 * 3600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    def need_auth(request: Request) -> None:
        if not request.session.get("auth"):
            raise HTTPException(401, "login required")

    def need_csrf(request: Request) -> None:
        token = request.session.get("csrf", "")
        sent = request.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, sent):
            raise HTTPException(403, "bad csrf token")

    read = [Depends(need_auth)]
    write = [Depends(need_auth), Depends(need_csrf)]

    def summary(t: Tunnel) -> dict:
        return {
            "name": t.name, "user_name": t.user_name, "project": t.project, "note": t.note,
            "uuid": t.uuid, "hostnames": hostnames(t), "rule_count": len(t.rules),
            "status": manager.svc.is_active(t.name),
        }

    def get_or_404(name: str) -> Tunnel:
        t = manager.db.get_tunnel(name)
        if t is None:
            raise HTTPException(404, "tunnel not found")
        return t

    # ---- auth ----

    @app.post("/api/login")
    def login(body: LoginIn, request: Request):
        ip = request.client.host if request.client else "unknown"
        if limiter.blocked(ip):
            raise HTTPException(429, "too many attempts, try again later")
        if not verify_password(settings.password_hash, body.password):
            limiter.fail(ip)
            raise HTTPException(401, "invalid credentials")
        limiter.reset(ip)
        request.session.clear()
        request.session["auth"] = True
        request.session["csrf"] = secrets.token_urlsafe(32)
        return {"ok": True, "csrf": request.session["csrf"]}

    @app.post("/api/logout", dependencies=write)
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/session")
    def session(request: Request):
        if request.session.get("auth"):
            return {"authenticated": True, "csrf": request.session["csrf"]}
        return {"authenticated": False}

    # ---- env ----

    def users() -> list[str]:
        return sorted({t.user_name for t in manager.db.list_tunnels()})

    @app.get("/api/env", dependencies=read)
    def env_info():
        return env_service.collect(users())

    @app.post("/api/env/refresh", dependencies=write)
    def env_refresh():
        return env_service.collect(users(), force=True)

    @app.post("/api/env/update", dependencies=write)
    def env_update(body: UpdateIn):
        latest = env_service.collect(users())["latest"]
        if not latest:
            raise HTTPException(400, "latest version unknown; check connectivity and refresh")
        return out(manager.update_cloudflared(latest, body.restart))

    # ---- tunnels ----

    @app.get("/api/tunnels", dependencies=read)
    def list_tunnels():
        return [summary(t) for t in manager.db.list_tunnels()]

    @app.post("/api/tunnels/preview", dependencies=write)
    def preview(body: PreviewIn):
        return manager.preview(body.name, to_tunnel(body.tunnel))

    @app.get("/api/tunnels/{name}", dependencies=read)
    def get_tunnel(name: str):
        t = get_or_404(name)
        return {**summary(t), "rules": [asdict(r) for r in t.rules]}

    @app.post("/api/tunnels", dependencies=write)
    def create_tunnel(body: TunnelIn):
        return out(manager.create(to_tunnel(body)))

    @app.put("/api/tunnels/{name}", dependencies=write)
    def edit_tunnel(name: str, body: TunnelIn):
        return out(manager.edit(name, to_tunnel(body)))

    @app.post("/api/tunnels/{name}/delete", dependencies=write)
    def delete_tunnel(name: str, body: ConfirmIn):
        return out(manager.delete(name, body.confirm))

    @app.post("/api/tunnels/{name}/{action}", dependencies=write)
    def control(name: str, action: str):
        return out(manager.control(name, action))

    @app.get("/api/tunnels/{name}/logs", dependencies=read)
    def logs(name: str, lines: int = 200):
        get_or_404(name)
        return {"logs": manager.svc.logs(name, max(1, min(lines, 1000)))}

    # ---- import ----

    def scan() -> list[importer.Candidate]:
        known = {t.name for t in manager.db.list_tunnels()}
        return importer.scan(settings.discover_homes(), settings.systemd_dir, known)

    @app.get("/api/import/scan", dependencies=read)
    def import_scan():
        return [
            {
                "name": c.name, "user_name": c.user_name, "uuid": c.uuid, "rule_count": len(c.rules),
                "warnings": c.warnings, "known": c.known, "error": c.error, "importable": c.importable,
            }
            for c in scan()
        ]

    @app.post("/api/import", dependencies=write)
    def import_commit(body: ImportIn):
        wanted = set(body.names)
        picked = [c for c in scan() if c.name in wanted]
        return {"imported": importer.import_candidates(manager.db, picked)}

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
```

`cfkit/app/main.py`:
```python
from __future__ import annotations

import os
import sys

import uvicorn

from app.api import create_app
from app.cf import Cloudflared
from app.config import Settings, load_settings
from app.db import Database
from app.env import EnvService, LatestCache
from app.flows import Manager
from app.runner import SubprocessRunner
from app.svc import Systemd


def build_app(settings: Settings):
    runner = SubprocessRunner()
    cf = Cloudflared(runner, settings.cloudflared_bin)
    manager = Manager(Database(settings.db_path), cf, Systemd(runner), runner, settings)
    return create_app(settings, manager, EnvService(cf, settings, LatestCache()))


def main() -> None:
    settings = load_settings(os.environ)
    if not settings.password_hash or not settings.secret_key:
        sys.exit("CFKIT_PASSWORD_HASH and CFKIT_SECRET must be set (see /etc/cfkit/.env)")
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
```

`cfkit/static/index.html` (minimal; fully replaced in Task 10):
```html
<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>CFKit - Tunnel Manager</title></head>
<body><div id="app"></div></body></html>
```

- [ ] **Step 5: Run to verify pass**

Run: `cd cfkit && .venv/bin/pytest -q`
Expected: all pass. Likely snags: `TestClient` default Host is `testserver` (already in the `settings` fixture's `allowed_hosts`); `GET /api/tunnels/Demo/logs` route must be registered with a distinct method/path from `POST /api/tunnels/{name}/{action}` (it is: GET vs POST).

- [ ] **Step 6: Commit**

```bash
git add cfkit/app/auth.py cfkit/app/api.py cfkit/app/main.py cfkit/static/index.html cfkit/tests/test_auth.py cfkit/tests/test_api.py
git commit -m "feat(cfkit): auth, CSRF, rate limit, HTTP API and entrypoint"
```

---

### Task 10: Front end (port the mockup, wire to the API)

**Files:**
- Modify: `cfkit/static/index.html` (replace)
- Create: `cfkit/static/style.css`, `cfkit/static/app.js`
- Modify: `cfkit/tests/test_api.py` (append one test)

**Interfaces:**
- Consumes: the HTTP API from Task 9. The CSP header forbids inline styles and inline scripts: **no `style="…"` attributes, no `setAttribute("style")`, no inline `<script>`/`onclick=`**; all DOM text goes through `textContent`/text nodes (never `innerHTML`).

- [ ] **Step 1: Write the failing test (append to `cfkit/tests/test_api.py`)**

```python
def test_static_assets_served_and_index_has_no_inline_script_or_style(client):
    html = client.get("/").text
    assert 'src="app.js"' in html and 'href="style.css"' in html
    assert "<script>" not in html and "style=" not in html
    assert client.get("/app.js").status_code == 200
    assert "var(--acc)" in client.get("/style.css").text
```
Run: `cd cfkit && .venv/bin/pytest tests/test_api.py::test_static_assets_served_and_index_has_no_inline_script_or_style -q` → Expected: FAIL.

- [ ] **Step 2: index.html and CSS**

`cfkit/static/index.html`:
```html
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CFKit - Tunnel Manager</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div id="app"></div>
<div id="layer"></div>
<script src="app.js"></script>
</body>
</html>
```

Extract the mockup's stylesheet and append the utility classes the JS uses:
```bash
cd cfkit
sed -n '/<style>/,/<\/style>/p' ../docs/superpowers/mockups/cfkit-ui-mockup.html | sed '1d;$d' > static/style.css
cat >> static/style.css <<'EOF'
.ribbon{display:none}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.right{justify-content:flex-end}
.grow{flex:1}
.mt{margin-top:12px}
.hidden{display:none}
.login{max-width:340px;margin:15vh auto;padding:20px}
.login h1{margin-top:0}
.errors{color:var(--bad);margin:8px 0;padding-left:18px}
.warn-line{color:#a15c00}
.steps .fail::before{content:"✗ ";color:var(--bad)}
.steps .fail{color:var(--bad)}
.flash{padding:8px 12px;border-radius:8px;margin-bottom:12px}
.flash.ok{background:var(--okbg);color:var(--ok)}
.flash.bad{background:var(--badbg);color:var(--bad)}
.pre-box{max-height:300px}
.tabc.hidden{display:none}
.modal pre{max-height:300px;overflow:auto}
.env-note{font-size:12px;color:var(--mute)}
.panel .card{overflow-x:auto}
.logbox{white-space:pre-wrap}
EOF
```
The mockup's `.panel`/`.modal`/`.overlay` rules use a `.on` class for visibility; the JS below adds `on` when it creates them.

- [ ] **Step 3: app.js**

`cfkit/static/app.js`:
```js
"use strict";

const state = { csrf: null, tunnels: [], env: null, poll: null };
const app = document.getElementById("app");
const layer = document.getElementById("layer");

// ---------- helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf || "" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("unauthorized"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d) || res.statusText);
  }
  return data;
}

function statusBadge(s) {
  const cls = s === "active" ? "b-ok" : s === "failed" ? "b-bad" : "b-idle";
  return h("span", { class: `badge ${cls}` }, s);
}

function stepsList(steps) {
  return h("ul", { class: "steps" }, steps.map((s) =>
    h("li", { class: s.ok ? "ok" : "fail" }, s.name, s.detail ? ` - ${s.detail}` : "")));
}

function flash(res) {
  const old = document.getElementById("flash");
  if (old) old.remove();
  const box = h("div", { id: "flash", class: `flash ${res.ok ? "ok" : "bad"}` },
    res.ok ? "OK" : "Thất bại", stepsList(res.steps));
  const main = app.querySelector("main");
  if (main) main.prepend(box);
}

function closeLayer() { layer.replaceChildren(); }
function openModal(title, bodyNodes, buttons) {
  closeLayer();
  const overlay = h("div", { class: "overlay on", onclick: closeLayer });
  const modal = h("div", { class: "modal on" }, h("h3", {}, title), bodyNodes,
    h("div", { class: "row right mt" }, buttons));
  layer.append(overlay, modal);
  return modal;
}
const btn = (label, onclick, cls = "") => h("button", { class: cls, onclick }, label);

// ---------- login ----------
function showLogin() {
  clearInterval(state.poll);
  state.csrf = null;
  closeLayer();
  const input = h("input", { type: "password", placeholder: "Mật khẩu admin", autofocus: true });
  const err = h("div", { class: "errors" });
  const submit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("POST", "/api/login", { password: input.value });
      state.csrf = r.csrf;
      boot();
    } catch (ex) { err.textContent = ex.message; }
  };
  app.replaceChildren(h("form", { class: "card login", onsubmit: submit },
    h("h1", {}, "CFKIT"), input, err, h("div", { class: "mt" }, h("button", { class: "pri", type: "submit" }, "Đăng nhập"))));
}

// ---------- main view ----------
async function boot() {
  app.replaceChildren(
    h("header", {}, h("h1", {}, "CFKIT · Tunnel Manager"), btn("Đăng xuất", logout)),
    h("main", {}, h("div", { id: "env" }), h("div", { class: "tools", id: "tools" }), h("div", { id: "list" })));
  renderTools();
  await Promise.all([loadEnv(), loadTunnels()]);
  clearInterval(state.poll);
  state.poll = setInterval(() => { if (!document.hidden && !layer.firstChild) loadTunnels(); }, 5000);
}

async function logout() {
  try { await api("POST", "/api/logout"); } catch (_) { /* already logged out */ }
  showLogin();
}

function renderTools() {
  $("#tools").replaceChildren(
    btn("+ New tunnel", () => openEditor(null), "pri"),
    btn("Import existing", openImport),
    btn("Refresh", () => { loadEnv(true); loadTunnels(); }));
}
const $ = (s) => document.querySelector(s);

async function loadEnv(force = false) {
  try {
    state.env = force ? await api("POST", "/api/env/refresh") : await api("GET", "/api/env");
  } catch (ex) { if (ex.message !== "unauthorized") state.env = null; }
  renderEnv();
}

function renderEnv() {
  const e = state.env;
  const box = $("#env");
  if (!e) { box.replaceChildren(); return; }
  const certs = Object.entries(e.certs);
  box.replaceChildren(h("div", { class: "card env" },
    h("div", {}, h("label", {}, "cloudflared"),
      h("b", {}, e.installed ? "✓ đã cài" : "✗ chưa cài"), h("div", { class: "mono" }, e.path || "")),
    h("div", {}, h("label", {}, "Version"), h("b", {}, e.version || "?"),
      e.update_available ? h("div", {}, h("span", { class: "badge b-warn" }, `có bản mới ${e.latest}`)) : null,
      e.latest_error ? h("div", { class: "env-note" }, `không kiểm tra được bản mới: ${e.latest_error}`) : null),
    h("div", {}, h("label", {}, "cert.pem"),
      certs.length ? certs.map(([u, ok]) => h("div", {}, h("span", { class: `badge ${ok ? "b-ok" : "b-bad"}` }, ok ? u : `${u} · thiếu`)))
        : h("div", { class: "env-note" }, "(chưa có tunnel)")),
    h("div", { class: "envact" },
      btn("Update…", openUpdate), btn("Fix cert.pem…", openCert), btn("Check lại", () => loadEnv(true)))));
}

async function loadTunnels() {
  try { state.tunnels = await api("GET", "/api/tunnels"); } catch (_) { return; }
  const groups = new Map();
  for (const t of state.tunnels) {
    const key = t.project || "(không có project)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  }
  const list = $("#list");
  if (!state.tunnels.length) {
    list.replaceChildren(h("div", { class: "card login" }, "Chưa có tunnel. Dùng “Import existing” hoặc “+ New tunnel”."));
    return;
  }
  list.replaceChildren(...[...groups].map(([project, items]) =>
    h("div", { class: "group" },
      h("h2", {}, `${project} · ${items.length} tunnel`),
      h("div", { class: "card" }, h("table", {},
        h("tr", {}, ["Tunnel", "Hostnames", "Status", "UUID", "User", "Actions"].map((c) => h("th", {}, c))),
        items.map(row))))));
}

function row(t) {
  const act = (label, a, disabled) => h("button", { class: "sm", disabled, onclick: () => control(t.name, a) }, label);
  const on = t.status === "active";
  return h("tr", {},
    h("td", {}, h("b", {}, t.name), t.note ? h("div", { class: "note" }, t.note) : null),
    h("td", {}, t.hostnames.map((x) => h("span", { class: "chip" }, x))),
    h("td", {}, statusBadge(t.status)),
    h("td", { class: "mono" }, t.uuid ? `${t.uuid.slice(0, 8)}…` : "-"),
    h("td", {}, t.user_name),
    h("td", {}, h("div", { class: "acts" },
      act("Run", "start", on), act("Stop", "stop", !on), act("Restart", "restart", !on),
      h("button", { class: "sm", onclick: () => openEditor(t.name) }, "Edit"),
      h("button", { class: "sm", onclick: () => openLogs(t.name) }, "Logs"),
      h("button", { class: "sm danger", onclick: () => openDelete(t) }, "Delete"))));
}

async function control(name, action) {
  try { flash(await api("POST", `/api/tunnels/${encodeURIComponent(name)}/${action}`)); }
  catch (ex) { flash({ ok: false, steps: [{ name: action, ok: false, detail: ex.message }] }); }
  loadTunnels();
}

// ---------- editor (create / edit) ----------
const emptyRule = () => ({ hostname: "", path: "", service: "http://127.0.0.1:", http_host_header: "", no_tls_verify: false });

async function openEditor(name) {
  let draft;
  if (name) {
    const t = await api("GET", `/api/tunnels/${encodeURIComponent(name)}`);
    draft = {
      name: t.name, user_name: t.user_name, project: t.project, note: t.note,
      rules: t.rules.map((r) => ({ ...r, path: r.path || "", http_host_header: r.http_host_header || "" })),
    };
  } else {
    const users = state.env ? Object.keys(state.env.certs) : [];
    draft = { name: "", user_name: users[0] || "root", project: "", note: "", rules: [emptyRule()] };
  }
  const payload = () => ({
    name: draft.name, user_name: draft.user_name, project: draft.project, note: draft.note,
    rules: draft.rules.map((r) => ({
      hostname: r.hostname, service: r.service, path: r.path || null,
      http_host_header: r.http_host_header || null, no_tls_verify: r.no_tls_verify,
    })),
  });

  const field = (label, key, locked) => h("div", {}, h("label", {}, label),
    h("input", { value: draft[key], disabled: locked, oninput: (e) => { draft[key] = e.target.value; } }));

  const rulesBox = h("div", { class: "tabc rules card" });
  const yamlPre = h("pre", {});
  const diffPre = h("pre", {});
  const yamlTab = h("div", { class: "tabc hidden" }, yamlPre);
  const diffTab = h("div", { class: "tabc hidden" }, diffPre);
  const errBox = h("ul", { class: "errors" });
  const stepsBox = h("div", {});
  const applyBtn = h("button", { class: "pri", onclick: apply }, "Apply");
  const msg = h("span", { class: "msg" });

  function renderRules() {
    const input = (r, key, ph) => h("td", {}, h("input", { value: r[key], placeholder: ph || "", oninput: (e) => { r[key] = e.target.value; } }));
    const move = (i, d) => { const j = i + d; if (j < 0 || j >= draft.rules.length) return; [draft.rules[i], draft.rules[j]] = [draft.rules[j], draft.rules[i]]; renderRules(); };
    rulesBox.replaceChildren(
      h("table", {},
        h("tr", {}, ["", "Hostname", "Path", "Service", "Host header", "noTLS", ""].map((c) => h("th", {}, c))),
        draft.rules.map((r, i) => h("tr", {},
          h("td", {}, h("button", { class: "sm", onclick: () => move(i, -1) }, "↑"), h("button", { class: "sm", onclick: () => move(i, 1) }, "↓")),
          input(r, "hostname"), input(r, "path", "(mọi path)"), input(r, "service"), input(r, "http_host_header"),
          h("td", {}, h("input", { type: "checkbox", checked: r.no_tls_verify, onchange: (e) => { r.no_tls_verify = e.target.checked; } })),
          h("td", {}, h("button", { class: "sm danger", onclick: () => { draft.rules.splice(i, 1); renderRules(); } }, "✕")))),
        h("tr", { class: "fixed" }, h("td", {}, "🔒"), h("td", { colspan: 2 }, "(catch-all, tự động)"), h("td", { colspan: 4 }, "http_status:404"))),
      h("div", { class: "row mt" }, btn("+ Add rule", () => { draft.rules.push(emptyRule()); renderRules(); }, "sm"),
        h("span", { class: "note" }, "thứ tự quan trọng: cloudflared khớp từ trên xuống")));
  }

  async function preview() {
    const p = await api("POST", "/api/tunnels/preview", { name: name || null, tunnel: payload() });
    yamlPre.textContent = p.yaml;
    diffPre.replaceChildren(...(p.diff ? p.diff.split("\n").map((l) =>
      h("span", { class: l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : "" }, l + "\n"))
      : [document.createTextNode(name ? "(không có thay đổi)" : "(tunnel mới)")]));
    errBox.replaceChildren(...p.errors.map((e) => h("li", {}, e)));
    applyBtn.disabled = p.errors.length > 0;
    return p;
  }

  async function apply() {
    msg.textContent = "";
    stepsBox.replaceChildren();
    const p = await preview();
    if (p.errors.length) return;
    msg.textContent = "Đang apply…";
    applyBtn.disabled = true;
    try {
      const res = name ? await api("PUT", `/api/tunnels/${encodeURIComponent(name)}`, payload())
                       : await api("POST", "/api/tunnels", payload());
      stepsBox.replaceChildren(stepsList(res.steps));
      if (res.data && res.data.removed_hostnames && res.data.removed_hostnames.length) {
        stepsBox.append(h("div", { class: "warn-line" },
          `DNS còn lại trong Cloudflare (xóa tay nếu không dùng): ${res.data.removed_hostnames.join(", ")}`));
      }
      msg.textContent = res.ok ? "✓ Applied" : "✗ Thất bại - xem các bước";
      if (res.ok && !name) { name = draft.name; }
    } catch (ex) { msg.textContent = ex.message; }
    applyBtn.disabled = false;
    loadTunnels();
  }

  const tabs = [["Rules", rulesBox], ["Preview YAML", yamlTab], ["Diff vs hiện tại", diffTab]];
  const tabBar = h("div", { class: "tabs" }, tabs.map(([label, pane], i) =>
    h("div", { class: `tab${i === 0 ? " on" : ""}`, onclick: async (e) => {
      for (const t of tabBar.children) t.classList.remove("on");
      e.currentTarget.classList.add("on");
      for (const [, p] of tabs) p.classList.add("hidden");
      pane.classList.remove("hidden");
      if (pane !== rulesBox) await preview();
    } }, label)));

  renderRules();
  closeLayer();
  layer.append(
    h("div", { class: "overlay on", onclick: closeLayer }),
    h("aside", { class: "panel on" },
      h("header", {}, h("h1", {}, name ? `Edit tunnel · ${name}` : "New tunnel"), btn("✕", closeLayer)),
      h("div", { class: "pbody" },
        h("div", { class: "grid" }, field("Name" + (name ? " 🔒" : ""), "name", !!name), field("Service user" + (name ? " 🔒" : ""), "user_name", !!name),
          field("Project (tag)", "project"), field("Note", "note")),
        tabBar, rulesBox, yamlTab, diffTab, errBox, stepsBox),
      h("div", { class: "pfoot" }, msg, btn("Close", closeLayer), applyBtn)));
}

// ---------- modals ----------
async function openLogs(name) {
  const pre = h("pre", { class: "logbox" }, "…");
  let timer = null;
  const follow = h("input", { type: "checkbox", onchange: (e) => {
    clearInterval(timer);
    if (e.target.checked) timer = setInterval(load, 3000);
  } });
  async function load() {
    try { pre.textContent = (await api("GET", `/api/tunnels/${encodeURIComponent(name)}/logs?lines=200`)).logs || "(trống)"; }
    catch (ex) { pre.textContent = ex.message; }
  }
  const modal = openModal(`Logs · cloudflared-${name}`, pre,
    [h("label", { class: "row" }, follow, "Follow"), btn("Refresh", load), btn("Đóng", () => { clearInterval(timer); closeLayer(); })]);
  load();
  return modal;
}

function openDelete(t) {
  const input = h("input", { placeholder: t.name });
  const out = h("div", {});
  const del = h("button", { class: "danger", disabled: true, onclick: async () => {
    const res = await api("POST", `/api/tunnels/${encodeURIComponent(t.name)}/delete`, { confirm: input.value });
    out.replaceChildren(stepsList(res.steps));
    if (res.ok) {
      out.append(h("div", { class: "warn-line" }, "Xóa tay các DNS record trong Cloudflare dashboard:"),
        h("div", {}, res.data.leftover_dns.map((x) => h("span", { class: "chip" }, x))));
      del.disabled = true;
    }
    loadTunnels();
  } }, "Delete");
  input.addEventListener("input", () => { del.disabled = input.value !== t.name; });
  openModal(`Xóa tunnel «${t.name}»?`, [
    h("p", { class: "note" }, "Sẽ stop + disable service, xóa unit/YAML/credentials và chạy tunnel delete. DNS KHÔNG tự xóa:"),
    h("div", {}, t.hostnames.map((x) => h("span", { class: "chip" }, x))),
    h("label", { class: "mt" }, `Gõ ${t.name} để xác nhận`), input, out,
  ], [btn("Đóng", closeLayer), del]);
}

async function openImport() {
  const cands = await api("GET", "/api/import/scan");
  const picks = new Map();
  const rows = cands.map((c) => {
    const can = c.importable && !c.known;
    const cb = h("input", { type: "checkbox", disabled: !can, checked: can, onchange: (e) => picks.set(c.name, e.target.checked) });
    picks.set(c.name, can);
    return h("tr", {}, h("td", {}, cb), h("td", {}, h("b", {}, c.name)),
      h("td", { class: "note" }, `${c.rule_count} rules · user ${c.user_name}`,
        c.error ? h("div", { class: "errors" }, c.error) : null,
        c.warnings.map((w) => h("div", { class: "warn-line" }, w))),
      h("td", {}, h("span", { class: `badge ${c.known ? "b-idle" : can ? "b-ok" : "b-bad"}` }, c.known ? "đã có" : can ? "mới" : "lỗi")));
  });
  openModal("Import tunnel có sẵn", [
    h("p", { class: "note" }, "Quét ~/.cloudflared/config-*.yaml. Chỉ ghi vào DB của CFKit, không đụng file/service."),
    h("div", { class: "card" }, h("table", {}, rows.length ? rows : h("tr", {}, h("td", {}, "Không tìm thấy config nào")))),
  ], [btn("Cancel", closeLayer), btn("Import", async () => {
    const names = [...picks].filter(([, v]) => v).map(([k]) => k);
    await api("POST", "/api/import", { names });
    closeLayer();
    loadTunnels(); loadEnv();
  }, "pri")]);
}

function openUpdate() {
  const e = state.env || {};
  const out = h("div", {});
  const run = async (restart) => {
    out.replaceChildren("Đang chạy…");
    try { out.replaceChildren(stepsList((await api("POST", "/api/env/update", { restart })).steps)); }
    catch (ex) { out.textContent = ex.message; }
    loadEnv(true);
  };
  openModal("Update cloudflared", [
    h("p", { class: "note" }, `Hiện tại ${e.version || "?"} → mới nhất ${e.latest || "?"}. Sẽ chạy: curl tải .deb từ GitHub releases, rồi dpkg -i.`),
    h("p", { class: "note" }, "Restart làm mỗi tunnel mất kết nối vài giây (restart lần lượt từng cái)."), out,
  ], [btn("Đóng", closeLayer), btn("Chỉ cài", () => run(false)), btn("Cài + restart tất cả", () => run(true), "pri")]);
}

function openCert() {
  openModal("Thiếu cert.pem", [
    h("p", { class: "note" }, "Server không có browser. Chọn 1 cách:"),
    h("p", {}, "1) Trên máy có browser: cloudflared login, rồi scp ~/.cloudflared/cert.pem root@IP:<home của user>/.cloudflared/cert.pem"),
    h("p", {}, "2) Chạy cloudflared login trong terminal SSH của server và mở URL hiện ra trên browser."),
    h("p", { class: "note" }, "CFKit không tự chạy login (cần tương tác). Bấm “Check lại” sau khi xong."),
  ], [btn("Đóng", closeLayer), btn("Check lại", () => { closeLayer(); loadEnv(true); }, "pri")]);
}

// ---------- start ----------
(async function init() {
  try {
    const s = await api("GET", "/api/session");
    if (s.authenticated) { state.csrf = s.csrf; boot(); } else showLogin();
  } catch (_) { showLogin(); }
})();
```

- [ ] **Step 4: Run the test and syntax-check the JS**

Run:
```bash
cd cfkit && .venv/bin/pytest -q
command -v node >/dev/null && node --check static/app.js && echo "JS syntax OK" || echo "node not installed - skipped"
```
Expected: pytest all pass; `JS syntax OK` (or skipped).

- [ ] **Step 5: Manual check in a browser (required before claiming done)**

```bash
cd cfkit
export CFKIT_PASSWORD_HASH="$(.venv/bin/python -c 'from app.auth import hash_password; print(hash_password("dev"))')"
export CFKIT_SECRET=dev-secret CFKIT_DB=/tmp/cfkit-dev.db
.venv/bin/python -m app.main   # then open http://127.0.0.1:8787, password "dev"
```
Verify: login works; layout matches the mockup (CSP blocks inline styles, so any broken layout means a leaked `style=`); env card renders; “+ New tunnel” panel opens, Preview YAML shows output; browser console shows no CSP violations. Do NOT click Apply here (this dev run would try real `cloudflared`/`systemctl` as the current user). Stop the server with Ctrl+C.

- [ ] **Step 6: Commit**

```bash
git add cfkit/static cfkit/tests/test_api.py
git commit -m "feat(cfkit): web UI ported from mockup and wired to API"
```

---

### Task 11: Installer, README, final verification

**Files:**
- Create: `cfkit/install.sh`, `cfkit/README.md`

**Interfaces:**
- Consumes: `app.auth.hash_password`, `python -m app.main`, env vars from `config.py` (`CFKIT_PASSWORD_HASH`, `CFKIT_SECRET`, `CFKIT_PORT`, `CFKIT_DB`).

- [ ] **Step 1: Write `cfkit/install.sh`**

```bash
#!/bin/bash
# CFKit installer. Run as root: sudo ./install.sh   (re-run to upgrade; keeps /etc/cfkit/.env)
set -euo pipefail

APP_DIR=/opt/cfkit
ENV_DIR=/etc/cfkit
ENV_FILE=$ENV_DIR/.env
DATA_DIR=/var/lib/cfkit
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO] $1${NC}"; }
error() { echo -e "${RED}[ERROR] $1${NC}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Chạy với quyền root: sudo $0"
command -v python3 >/dev/null || error "Cần python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || error "Cần Python 3.10+"
python3 -c 'import venv, ensurepip' 2>/dev/null || error "Cần gói python3-venv (apt install python3-venv)"

info "Cài code vào $APP_DIR"
install -d -m 755 "$APP_DIR"
rm -rf "$APP_DIR/app" "$APP_DIR/static"
cp -r "$SRC_DIR/app" "$SRC_DIR/static" "$SRC_DIR/requirements.txt" "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

install -d -m 700 "$ENV_DIR" "$DATA_DIR"
if [[ ! -f $ENV_FILE ]]; then
    read -r -s -p "Mật khẩu admin: " PW; echo
    read -r -s -p "Nhập lại: " PW2; echo
    [[ -n $PW && $PW == "$PW2" ]] || error "Mật khẩu rỗng hoặc không khớp"
    HASH=$(CFKIT_PW=$PW PYTHONPATH=$APP_DIR "$APP_DIR/venv/bin/python" -c \
        'import os; from app.auth import hash_password; print(hash_password(os.environ["CFKIT_PW"]))')
    SECRET=$("$APP_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
    (
        umask 077
        cat > "$ENV_FILE" <<EOF
CFKIT_PASSWORD_HASH='$HASH'
CFKIT_SECRET='$SECRET'
CFKIT_PORT=8787
CFKIT_DB=$DATA_DIR/cfkit.db
EOF
    )
    chmod 600 "$ENV_FILE"
    info "Đã tạo $ENV_FILE"
else
    info "Giữ nguyên $ENV_FILE hiện có"
fi

cat > /etc/systemd/system/cfkit.service <<EOF
[Unit]
Description=CFKit - Cloudflare Tunnel Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
User=root
SyslogIdentifier=cfkit

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cfkit.service
systemctl restart cfkit.service

info "--- HOÀN THÀNH ---"
echo "CFKit chạy tại 127.0.0.1:8787 (chỉ localhost)."
echo "Từ máy của bạn:  ssh -L 8787:127.0.0.1:8787 <user>@<server>  rồi mở http://localhost:8787"
echo "Trạng thái: systemctl status cfkit    Log: journalctl -u cfkit -f"
```
Then: `chmod +x cfkit/install.sh`.

- [ ] **Step 2: Write `cfkit/README.md`**

```markdown
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
```

- [ ] **Step 3: Verify scripts and the full suite**

Run:
```bash
bash -n cfkit/install.sh && echo "install.sh syntax OK"
cd cfkit && .venv/bin/pytest -q
```
Expected: `install.sh syntax OK`; all tests pass. Invoke the **verification** skill and paste the pytest summary line as PASS evidence.

- [ ] **Step 4: Commit**

```bash
git add cfkit/install.sh cfkit/README.md
git commit -m "feat(cfkit): installer and README"
```

- [ ] **Step 5: Manual smoke test on the real server (human-run, not automated)**

On the target server: `sudo ./cfkit/install.sh`, open via `ssh -L`. Then:
1. Environment card shows the real cloudflared version, correct cert.pem status per user, and update badge behavior.
2. **Import existing** lists `HotonChat` / `SiloS3`; import them.
3. Open one in **Edit**, check the Diff tab shows **no change** (or only the comment lines dropped), then Apply a trivial change (e.g. note-only edit still rewrites YAML). Confirm `cloudflared tunnel list` shows the **same UUID**, the DNS record is untouched, and `systemctl is-active cloudflared-<name>` is `active`.
4. Confirm the assumptions the unit tests cannot: `cloudflared tunnel --config <file> ingress validate` works with that CLI version and flag order; `tunnel delete` of a stopped tunnel succeeds; the “record already exists” text matches `route dns` output. Report any mismatch; adjust `cf.py` only for what the real CLI actually prints.
5. Create a throwaway tunnel on a test subdomain, hit its URL, then Delete it and remove the leftover DNS record by hand.

---

## Self-Review (spec coverage)

| Spec section | Covered by |
|---|---|
| §2 data model (tunnels, rules, backups, catch-all not stored) | Task 3, Task 4 (`render` appends 404) |
| §3 import (scan, unit user, preview, no file changes) | Task 5, Task 9 (`/api/import/*`), Task 10 (modal) |
| §4 modules | Tasks 1-9 (plus `models/config/validate/flows/env` noted above) |
| §5 apply flows: create + rollback, edit + diff + backup + restore, run/stop/restart, delete + leftover DNS, logs, validation, atomic writes | Task 8 (+ Task 6 wrappers) |
| §6 UI list/panel/preview/diff/steps/logs/delete/environment | Task 10 (reorder via ↑/↓, noted) |
| §7 auth: hashed password in env file, cookie, CSRF, rate limit, localhost bind, no-injection | Tasks 2, 9, 11 |
| §8 deployment | Task 11 |
| §9 testing (golden, round-trip, fake runner sequences, API, manual smoke) | Tasks 4, 5, 8, 9, 11 step 5 |
| §10 repo layout | File Structure above |
| §11 assumptions (port 8787, `/opt/cfkit`, DNS left behind, cert.pem mode) | Global Constraints, Task 8, README |

Placeholder scan: none. Signature consistency checked across tasks (`Cloudflared.route_dns -> str`, `FlowResult.data` keys `removed_hostnames`/`leftover_dns`, `Manager.config_path(user, name)`, `Manager._write_atomic(path, text, owner)` used by `conftest.seed`).
