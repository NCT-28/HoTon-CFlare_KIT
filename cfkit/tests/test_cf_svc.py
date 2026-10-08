from pathlib import Path

import pytest

from app.cf import Cloudflared
from app.runner import CommandError, Result
from app.svc import Systemd, unit_name
from tests.fakes import FakeRunner

UUID = "11111111-2222-4333-8444-555555555555"
BIN = "/usr/bin/cloudflared"


def cf(fake, bin_path=BIN):
    return Cloudflared(fake, lambda: bin_path)


def test_create_parses_uuid_from_stderr_and_runs_as_user():
    f = FakeRunner()
    f.when_has("tunnel", "create", result=Result(0, "", f"Tunnel credentials written to /h/.cloudflared/{UUID}.json\nCreated tunnel T with id {UUID}"))
    assert cf(f).create("T", "toannc") == UUID
    assert f.calls[0] == ([BIN, "tunnel", "create", "T"], "toannc")


def test_create_failure_raises_with_stderr_tail():
    f = FakeRunner()
    f.when_has("create", result=Result(1, "", "Cannot determine default origin certificate path"))
    with pytest.raises(CommandError, match="origin certificate"):
        cf(f).create("T", "root")


def test_create_without_uuid_in_output_raises():
    f = FakeRunner()
    f.when_has("create", result=Result(0, "ok but no id", ""))
    with pytest.raises(CommandError, match="tunnel id"):
        cf(f).create("T", "root")


def test_not_installed_raises():
    with pytest.raises(CommandError, match="not installed"):
        cf(FakeRunner(), bin_path=None).create("T", "root")


def test_route_dns_ok_and_already_exists_warns_other_errors_raise():
    f = FakeRunner()
    assert cf(f).route_dns("T", "a.example.com", "root") == ""
    assert f.calls[0][0] == [BIN, "tunnel", "route", "dns", "T", "a.example.com"]
    f.when_has("route", result=Result(1, "", "An A, AAAA, or CNAME record with that host already exists."))
    assert "already exists" in cf(f).route_dns("T", "a.example.com", "root")
    f.when_has("route", result=Result(1, "", "authentication error"))
    with pytest.raises(CommandError, match="authentication"):
        cf(f).route_dns("T", "a.example.com", "root")


def test_validate_runs_ingress_validate_on_that_file_as_user():
    f = FakeRunner()
    cf(f).validate(Path("/h/c.yaml"), "toannc")
    assert f.calls[0] == ([BIN, "tunnel", "--config", "/h/c.yaml", "ingress", "validate"], "toannc")
    f.when_has("validate", result=Result(1, "", "bad ingress"))
    with pytest.raises(CommandError, match="bad ingress"):
        cf(f).validate(Path("/h/c.yaml"), "toannc")


def test_delete_uses_force_and_tolerates_missing_tunnel():
    f = FakeRunner()
    cf(f).delete("T", "root")
    assert f.calls[0][0] == [BIN, "tunnel", "delete", "-f", "T"]
    f.when_has("delete", result=Result(1, "", "Tunnel T not found"))
    cf(f).delete("T", "root")
    f.when_has("delete", result=Result(1, "", f"Tunnel {UUID} has already been deleted"))
    cf(f).delete(UUID, "root")
    f.when_has("delete", result=Result(1, "", "connection refused"))
    with pytest.raises(CommandError):
        cf(f).delete("T", "root")


def test_version_parsing():
    f = FakeRunner()
    f.when_has("--version", result=Result(0, "cloudflared version 2025.8.1 (built 2025-08-20-1200 UTC)\n", ""))
    assert cf(f).version() == "2025.8.1"
    assert cf(f, bin_path=None).version() is None


def test_systemd_commands_and_failures():
    f = FakeRunner()
    s = Systemd(f)
    s.daemon_reload(); s.enable("T"); s.start("T"); s.restart("T"); s.stop("T"); s.disable("T")
    assert f.argv() == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "cloudflared-T.service"],
        ["systemctl", "start", "cloudflared-T.service"],
        ["systemctl", "restart", "cloudflared-T.service"],
        ["systemctl", "stop", "cloudflared-T.service"],
        ["systemctl", "disable", "cloudflared-T.service"],
    ]
    f.when_has("start", result=Result(1, "", "Unit not found"))
    with pytest.raises(CommandError, match="Unit not found"):
        s.start("T")


def test_is_active_reads_stdout_even_on_nonzero_exit():
    f = FakeRunner()
    f.when_has("is-active", result=Result(3, "inactive\n", ""))
    assert Systemd(f).is_active("T") == "inactive"
    f.when_has("is-active", result=Result(1, "", ""))
    assert Systemd(f).is_active("T") == "unknown"


def test_logs_command():
    f = FakeRunner()
    f.when_has("journalctl", result=Result(0, "line1\nline2\n", ""))
    assert Systemd(f).logs("T", 50) == "line1\nline2\n"
    assert f.calls[0][0] == ["journalctl", "-u", "cloudflared-T.service", "-n", "50", "--no-pager"]


def test_unit_name():
    assert unit_name("HotonChat") == "cloudflared-HotonChat.service"


def test_route_dns_overwrite_adds_flag_before_positionals():
    f = FakeRunner()
    cf(f).route_dns(UUID, "a.example.com", "root", overwrite=True)
    assert f.calls[0][0] == [BIN, "tunnel", "route", "dns", "--overwrite-dns", UUID, "a.example.com"]
