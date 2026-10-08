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
        errs.append(f"name: {t.name!r} is not valid; must start with a letter; letters, digits, '_' and '-' only (max 63)")
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
