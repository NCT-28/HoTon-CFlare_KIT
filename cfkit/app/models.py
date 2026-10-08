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
