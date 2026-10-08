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
