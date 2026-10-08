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
