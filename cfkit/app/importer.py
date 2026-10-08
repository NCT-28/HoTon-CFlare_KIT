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
