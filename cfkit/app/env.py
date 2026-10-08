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
