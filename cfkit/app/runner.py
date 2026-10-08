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
