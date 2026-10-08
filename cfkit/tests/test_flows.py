from app.models import Rule
from app.render import render_yaml
from app.runner import Result
from tests.conftest import UUID, make_tunnel, seed


def names(res):
    return [s.name for s in res.steps]


# ---------- create ----------

def test_create_happy_path_order_and_files(manager, fake, settings):
    t = make_tunnel(hosts=("a.example.com", "b.example.com"))
    res = manager.create(t)
    assert res.ok, res.steps

    argv = fake.argv()
    idx = lambda *tok: next(i for i, a in enumerate(argv) if all(x in a for x in tok))
    assert idx("tunnel", "create") < idx("route", "a.example.com") < idx("route", "b.example.com") \
        < idx("ingress", "validate") < idx("daemon-reload") < idx("enable") < idx("start")

    cfg = manager.config_path("toannc", "Demo")
    cred = settings.home_of("toannc") / ".cloudflared" / f"{UUID}.json"
    assert cfg.read_text() == render_yaml(UUID, str(cred), t.rules)
    unit = manager.unit_path("Demo").read_text()
    assert f"ExecStart=/usr/bin/cloudflared --config {cfg} tunnel run" in unit and "User=toannc" in unit
    assert manager.db.get_tunnel("Demo").uuid == UUID


def test_create_runs_cloudflared_as_the_service_user(manager, fake):
    manager.create(make_tunnel(user="root"))
    create_call = next(c for c in fake.calls if "create" in c[0] and "tunnel" in c[0])
    assert create_call[1] == "root"


def test_create_rejects_invalid_input_before_any_command(manager, fake):
    bad = make_tunnel(name="x\nrm -rf /")
    res = manager.create(bad)
    assert not res.ok and fake.calls == []
    assert manager.db.list_tunnels() == []


def test_create_rejects_unknown_system_user_and_duplicate_name(manager, fake):
    assert not manager.create(make_tunnel(user="ghost")).ok
    assert fake.calls == []
    manager.create(make_tunnel())
    fake.calls.clear()
    dup = manager.create(make_tunnel())
    assert not dup.ok and "already exists" in dup.steps[0].detail and fake.calls == []


def test_create_failure_rolls_back_everything(manager, fake, settings):
    fake.when_has("systemctl", "start", result=Result(1, "", "boom"))
    res = manager.create(make_tunnel())
    assert not res.ok
    failed = next(s for s in res.steps if not s.ok)
    assert failed.name == "start" and "boom" in failed.detail
    assert res.steps[-1].name == "rollback"
    assert not manager.config_path("toannc", "Demo").exists()
    assert not manager.unit_path("Demo").exists()
    assert manager.db.get_tunnel("Demo") is None
    assert fake.has_call("tunnel", "delete", "Demo")


def test_create_failure_before_tunnel_exists_skips_cloudflare_delete(manager, fake):
    fake.when_has("tunnel", "create", result=Result(1, "", "no cert"))
    res = manager.create(make_tunnel())
    assert not res.ok and not fake.has_call("tunnel", "delete")
    assert manager.db.get_tunnel("Demo") is None


def test_create_dns_already_exists_is_success_with_warning(manager, fake):
    fake.when_has("route", result=Result(1, "", "record already exists"))
    res = manager.create(make_tunnel())
    assert res.ok
    dns = next(s for s in res.steps if s.name.startswith("dns "))
    assert "verify" in dns.detail


# ---------- edit ----------

def test_edit_adds_rule_only_routes_new_hostname_and_keeps_uuid(manager, fake):
    seed(manager, make_tunnel(hosts=("a.example.com",)))
    fake.calls.clear()
    new = make_tunnel(hosts=("a.example.com", "c.example.com"))
    res = manager.edit("Demo", new)
    assert res.ok, res.steps
    assert not fake.has_call("tunnel", "create") and not fake.has_call("tunnel", "delete")
    routes = [a for a in fake.argv() if "route" in a]
    assert len(routes) == 1 and "c.example.com" in routes[0]
    assert fake.has_call("systemctl", "restart", "cloudflared-Demo.service")
    saved = manager.db.get_tunnel("Demo")
    assert saved.uuid == UUID and [r.hostname for r in saved.rules] == ["a.example.com", "c.example.com"]
    assert "c.example.com" in manager.config_path("toannc", "Demo").read_text()
    assert len(manager.db.list_backups("Demo")) == 1


def test_edit_reports_removed_hostnames(manager):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com",)))
    assert res.ok and res.data["removed_hostnames"] == ["b.example.com"]


def test_edit_ignores_name_and_user_changes(manager):
    seed(manager, make_tunnel(user="toannc"))
    changed = make_tunnel(name="Other", user="root")
    res = manager.edit("Demo", changed)
    assert res.ok
    saved = manager.db.get_tunnel("Demo")
    assert saved.user_name == "toannc" and manager.db.get_tunnel("Other") is None


def test_edit_inactive_tunnel_is_not_started(manager, fake):
    seed(manager, make_tunnel())
    fake.when_has("is-active", result=Result(3, "inactive\n", ""))
    fake.calls.clear()
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert res.ok
    assert not fake.has_call("systemctl", "restart") and not fake.has_call("systemctl", "start")


def test_edit_restart_failure_restores_previous_yaml_and_db(manager, fake):
    old = seed(manager, make_tunnel(hosts=("a.example.com",)))
    cfg = manager.config_path("toannc", "Demo")
    before = cfg.read_text()
    fake.when_has("systemctl", "restart", result=Result(1, "", "restart failed"))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok
    assert res.steps[-1].name == "restore previous config"
    assert cfg.read_text() == before
    assert [r.hostname for r in manager.db.get_tunnel("Demo").rules] == ["a.example.com"]


def test_edit_validate_failure_leaves_everything_untouched(manager, fake):
    seed(manager, make_tunnel())
    cfg = manager.config_path("toannc", "Demo")
    before = cfg.read_text()
    fake.when_has("ingress", "validate", result=Result(1, "", "invalid ingress"))
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok and cfg.read_text() == before
    assert not cfg.with_name(cfg.name + ".new").exists()
    assert not fake.has_call("systemctl", "restart")


def test_edit_invalid_input_and_unknown_tunnel(manager, fake):
    assert not manager.edit("Ghost", make_tunnel()).ok
    seed(manager, make_tunnel())
    fake.calls.clear()
    bad = make_tunnel()
    bad.rules = [Rule("A B", "http://127.0.0.1:1")]
    assert not manager.edit("Demo", bad).ok and fake.calls == []


# ---------- delete ----------

def test_delete_requires_matching_confirmation(manager, fake):
    seed(manager, make_tunnel())
    fake.calls.clear()
    res = manager.delete("Demo", "demo")
    assert not res.ok and fake.calls == [] and manager.db.get_tunnel("Demo") is not None


def test_delete_removes_files_db_and_reports_leftover_dns(manager, fake):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    res = manager.delete("Demo", "Demo")
    assert res.ok, res.steps
    assert res.data["leftover_dns"] == ["a.example.com", "b.example.com"]
    assert not manager.config_path("toannc", "Demo").exists() and not manager.unit_path("Demo").exists()
    assert manager.db.get_tunnel("Demo") is None
    assert fake.has_call("tunnel", "delete", "Demo")


def test_delete_is_idempotent_when_files_already_missing(manager):
    seed(manager, make_tunnel())
    manager.config_path("toannc", "Demo").unlink()
    manager.unit_path("Demo").unlink()
    assert manager.delete("Demo", "Demo").ok


def test_delete_keeps_db_row_when_cloudflare_delete_fails(manager, fake):
    seed(manager, make_tunnel())
    fake.when_has("tunnel", "delete", result=Result(1, "", "tunnel has active connections"))
    res = manager.delete("Demo", "Demo")
    assert not res.ok and manager.db.get_tunnel("Demo") is not None


# ---------- control / lock / preview / update ----------

def test_control_runs_systemctl_and_rejects_unknown_action(manager, fake):
    seed(manager, make_tunnel())
    fake.calls.clear()
    assert manager.control("Demo", "restart").ok
    assert fake.argv() == [["systemctl", "restart", "cloudflared-Demo.service"]]
    assert not manager.control("Demo", "rm -rf").ok
    assert not manager.control("Ghost", "start").ok


def test_second_operation_on_same_tunnel_is_refused_while_busy(manager):
    seed(manager, make_tunnel())
    lock = manager._lock_for("Demo")
    assert lock.acquire(blocking=False)
    try:
        res = manager.control("Demo", "restart")
    finally:
        lock.release()
    assert not res.ok and res.steps[0].name == "lock"


def test_preview_shows_diff_and_hostname_changes(manager):
    seed(manager, make_tunnel(hosts=("a.example.com", "b.example.com")))
    p = manager.preview("Demo", make_tunnel(hosts=("a.example.com", "c.example.com")))
    assert p["errors"] == []
    assert p["added_hostnames"] == ["c.example.com"] and p["removed_hostnames"] == ["b.example.com"]
    assert "+  - hostname: c.example.com" in p["diff"] and "-  - hostname: b.example.com" in p["diff"]
    assert "http_status:404" in p["yaml"]


def test_preview_for_new_tunnel_flags_errors_and_duplicates(manager):
    seed(manager, make_tunnel())
    p = manager.preview(None, make_tunnel())  # same name as existing
    assert any("already exists" in e for e in p["errors"])
    dup = make_tunnel(name="Fresh")
    dup.rules = [Rule("a.example.com", "http://127.0.0.1:1", path="/x")] * 2
    assert any("duplicate" in e for e in manager.preview(None, dup)["errors"])
    assert manager.preview(None, make_tunnel(name="Fresh", user="ghost"))["errors"]


def test_update_cloudflared_validates_version_and_restarts_only_active(manager, fake):
    assert not manager.update_cloudflared("1.0; rm -rf /", True).ok
    assert fake.calls == []

    seed(manager, make_tunnel("One"))
    seed(manager, make_tunnel("Two"))
    fake.when_has("dpkg", "--print-architecture", result=Result(0, "amd64\n", ""))
    fake.when_has("is-active", "cloudflared-Two.service", result=Result(3, "inactive\n", ""))
    fake.calls.clear()
    res = manager.update_cloudflared("2025.9.0", restart_all=True)
    assert res.ok, res.steps
    curl = next(a for a in fake.argv() if a[0] == "curl")
    assert curl[-1] == "https://github.com/cloudflare/cloudflared/releases/download/2025.9.0/cloudflared-linux-amd64.deb"
    assert fake.has_call("dpkg", "-i")
    assert fake.has_call("systemctl", "restart", "cloudflared-One.service")
    assert not fake.has_call("systemctl", "restart", "cloudflared-Two.service")


def test_update_cloudflared_install_only_does_not_restart(manager, fake):
    seed(manager, make_tunnel("One"))
    fake.when_has("--print-architecture", result=Result(0, "arm64\n", ""))
    fake.calls.clear()
    assert manager.update_cloudflared("2025.9.0", restart_all=False).ok
    assert not fake.has_call("systemctl", "restart")


def test_update_cloudflared_unsupported_arch_fails(manager, fake):
    fake.when_has("--print-architecture", result=Result(0, "riscv64\n", ""))
    res = manager.update_cloudflared("2025.9.0", True)
    assert not res.ok and not fake.has_call("dpkg", "-i")


# ---------- final-review fixes ----------

def test_create_refuses_when_unit_or_config_already_on_disk(manager, fake):
    unit = manager.unit_path("Demo")
    cfg = manager.config_path("toannc", "Demo")
    unit.parent.mkdir(parents=True)
    cfg.parent.mkdir(parents=True)
    unit.write_text("LIVE-UNIT")
    cfg.write_text("LIVE-CFG")
    res = manager.create(make_tunnel())
    assert not res.ok and fake.calls == []
    assert unit.read_text() == "LIVE-UNIT" and cfg.read_text() == "LIVE-CFG"


def test_create_failing_at_tunnel_create_never_touches_services(manager, fake):
    fake.when_has("tunnel", "create", result=Result(1, "", "tunnel with name Demo already exists"))
    res = manager.create(make_tunnel())
    assert not res.ok and names(res) == ["tunnel create"]
    assert not fake.has_call("systemctl")


def test_create_rechecks_duplicate_name_inside_the_lock(manager, fake):
    seed(manager, make_tunnel())
    fake.calls.clear()
    res = manager._create(make_tunnel())
    assert not res.ok and fake.calls == []
    assert manager.db.get_tunnel("Demo") is not None


def test_edit_does_not_follow_symlink_planted_at_staging_path(manager, tmp_path):
    seed(manager, make_tunnel())
    cfg = manager.config_path("toannc", "Demo")
    victim = tmp_path / "victim"
    victim.write_text("secret")
    cfg.with_name(cfg.name + ".new").symlink_to(victim)
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert res.ok, res.steps
    assert victim.read_text() == "secret"


def test_preview_does_not_read_through_symlinked_config(manager, tmp_path):
    seed(manager, make_tunnel())
    cfg = manager.config_path("toannc", "Demo")
    secret = tmp_path / "root-only"
    secret.write_text("ROOT-SECRET-CONTENT\n")
    cfg.unlink()
    cfg.symlink_to(secret)
    p = manager.preview("Demo", make_tunnel())
    assert "ROOT-SECRET-CONTENT" not in p["diff"]
    assert any("cannot read" in e for e in p["errors"])


def test_edit_does_not_resurrect_a_tunnel_deleted_before_it_got_the_lock(manager, fake):
    seed(manager, make_tunnel())
    orig = manager._locked
    fired = []

    def hook(name, fn):
        if not fired:
            fired.append(1)
            assert manager.delete("Demo", "Demo").ok
        return orig(name, fn)

    manager._locked = hook
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok
    assert manager.db.get_tunnel("Demo") is None
    assert not manager.config_path("toannc", "Demo").exists()


def test_delete_after_concurrent_delete_reports_not_found(manager):
    seed(manager, make_tunnel())
    orig = manager._locked
    fired = []

    def hook(name, fn):
        if not fired:
            fired.append(1)
            assert manager.delete("Demo", "Demo").ok
        return orig(name, fn)

    manager._locked = hook
    assert not manager.delete("Demo", "Demo").ok


def test_edit_detects_crash_loop_after_restart_and_restores(manager, fake):
    seed(manager, make_tunnel(hosts=("a.example.com",)))
    cfg = manager.config_path("toannc", "Demo")
    before = cfg.read_text()
    calls = {"n": 0}

    def is_active(args):
        calls["n"] += 1
        return Result(0, "active\n" if calls["n"] == 1 else "activating\n", "")

    fake.when_has("is-active", result=is_active)
    res = manager.edit("Demo", make_tunnel(hosts=("a.example.com", "z.example.com")))
    assert not res.ok
    failed = next(s for s in res.steps if not s.ok)
    assert failed.name == "restart" and "activating" in failed.detail
    assert res.steps[-1].name == "restore previous config"
    assert cfg.read_text() == before
    assert [r.hostname for r in manager.db.get_tunnel("Demo").rules] == ["a.example.com"]


def test_create_rolls_back_when_service_is_not_active_after_start(manager, fake):
    fake.when_has("is-active", result=Result(3, "failed\n", ""))
    res = manager.create(make_tunnel())
    assert not res.ok
    assert next(s for s in res.steps if not s.ok).name == "start"
    assert res.steps[-1].name == "rollback"
    assert manager.db.get_tunnel("Demo") is None
    assert not manager.config_path("toannc", "Demo").exists()


def test_service_must_stay_active_through_the_settle_window(manager, fake):
    slept = []
    manager.settle, manager.poll_interval, manager.sleep = 1.0, 0.5, slept.append
    seen = {"n": 0}

    def is_active(args):
        seen["n"] += 1
        return Result(0, "active\n" if seen["n"] < 3 else "failed\n", "")

    fake.when_has("is-active", result=is_active)
    import pytest as _pytest
    from app.runner import CommandError
    with _pytest.raises(CommandError, match="failed"):
        manager._ensure_running("Demo")
    assert slept == [0.5, 0.5]
