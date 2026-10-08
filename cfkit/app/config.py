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
