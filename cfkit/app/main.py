from __future__ import annotations

import os
import sys

import uvicorn

from app.api import create_app
from app.cf import Cloudflared
from app.config import Settings, load_settings
from app.db import Database
from app.env import EnvService, LatestCache
from app.flows import Manager
from app.runner import SubprocessRunner
from app.svc import Systemd


def build_app(settings: Settings):
    runner = SubprocessRunner()
    cf = Cloudflared(runner, settings.cloudflared_bin)
    manager = Manager(Database(settings.db_path), cf, Systemd(runner), runner, settings)
    return create_app(settings, manager, EnvService(cf, settings, LatestCache()))


def main() -> None:
    settings = load_settings(os.environ)
    if not settings.password_hash or not settings.secret_key:
        sys.exit("CFKIT_PASSWORD_HASH and CFKIT_SECRET must be set (see /etc/cfkit/.env)")
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
