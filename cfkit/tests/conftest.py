import pytest

from app.cf import Cloudflared
from app.config import Settings
from app.db import Database
from app.flows import Manager
from app.models import Rule, Tunnel
from app.runner import Result
from app.svc import Systemd
from tests.fakes import FakeRunner

UUID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def fake():
    f = FakeRunner()
    f.when_has("is-active", result=Result(0, "active\n", ""))
    f.when_has("tunnel", "create", result=Result(0, "", f"Created tunnel T with id {UUID}"))
    return f


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "db.sqlite",
        systemd_dir=tmp_path / "systemd",
        home_of=lambda u: tmp_path / "home" / u,
        group_of=lambda u: u,
        user_exists=lambda u: u in {"toannc", "root"},
        discover_homes=lambda: {u: tmp_path / "home" / u for u in ("toannc", "root") if (tmp_path / "home" / u).exists()},
        cloudflared_bin=lambda: "/usr/bin/cloudflared",
        chown=False,
        secret_key="test-secret",
        allowed_hosts=["testserver"],
    )


@pytest.fixture
def manager(settings, fake):
    return Manager(Database(settings.db_path), Cloudflared(fake, settings.cloudflared_bin), Systemd(fake), fake, settings)


def make_tunnel(name="Demo", hosts=("a.example.com",), user="toannc", uuid=None):
    return Tunnel(
        name=name, user_name=user, project="P", uuid=uuid,
        rules=[Rule(h, "http://127.0.0.1:5500") for h in hosts],
    )


def seed(manager, t):
    """Put a tunnel into DB + config file + unit file as if it was created earlier."""
    t.uuid = t.uuid or UUID
    manager.db.save_tunnel(t)
    manager._write_atomic(manager.config_path(t.user_name, t.name), manager._render_config(t), None)
    manager._write_atomic(manager.unit_path(t.name), "[Unit]\n", None)
    return t
