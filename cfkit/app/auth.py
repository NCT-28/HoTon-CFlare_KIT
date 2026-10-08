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
