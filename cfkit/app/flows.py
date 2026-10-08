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
