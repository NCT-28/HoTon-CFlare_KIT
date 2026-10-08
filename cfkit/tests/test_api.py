import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth import hash_password
from app.env import EnvService, LatestCache
from app.runner import Result
from tests.conftest import UUID, make_tunnel, seed
from tests.samples import fixture

PW = "pw-for-tests"


@pytest.fixture
def client(settings, manager, fake, tmp_path):
    settings.password_hash = hash_password(PW)
    env = EnvService(manager.cf, settings, LatestCache(fetch=lambda: "2099.1.1"))
    fake.when_has("--version", result=Result(0, "cloudflared version 2025.8.1 (built x)\n", ""))
    return TestClient(create_app(settings, manager, env))


def login(client):
    assert client.post("/api/login", json={"password": PW}).status_code == 200
    return {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}


def body(name="Demo", hosts=("a.example.com",), user="toannc"):
    return {
        "name": name, "user_name": user, "project": "P", "note": "",
        "rules": [{"hostname": h, "service": "http://127.0.0.1:5500"} for h in hosts],
    }


# ---- auth / transport security ----

def test_api_requires_login(client):
    for path in ("/api/tunnels", "/api/env", "/api/import/scan"):
        assert client.get(path).status_code == 401


def test_session_endpoint_reports_state(client):
    assert client.get("/api/session").json() == {"authenticated": False}
    login(client)
    s = client.get("/api/session").json()
    assert s["authenticated"] is True and s["csrf"]


def test_wrong_password_401_and_sixth_attempt_429_even_with_right_password(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"password": PW}).status_code == 429


def test_mutations_require_csrf_header(client):
    login(client)
    assert client.post("/api/tunnels", json=body()).status_code == 403
    assert client.post("/api/tunnels", json=body(), headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_foreign_host_header_rejected(client):
    r = client.get("/api/session", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_logout_clears_session(client):
    h = login(client)
    assert client.post("/api/logout", headers=h).status_code == 200
    assert client.get("/api/tunnels").status_code == 401


def test_security_headers_present(client):
    r = client.get("/api/session")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


# ---- tunnels ----

def test_create_list_get_roundtrip(client):
    h = login(client)
    res = client.post("/api/tunnels", json=body(hosts=("a.example.com", "b.example.com")), headers=h).json()
    assert res["ok"], res
    [row] = client.get("/api/tunnels").json()
    assert row["name"] == "Demo" and row["uuid"] == UUID and row["status"] == "active"
    assert row["hostnames"] == ["a.example.com", "b.example.com"] and row["rule_count"] == 2
    full = client.get("/api/tunnels/Demo").json()
    assert full["rules"][0]["hostname"] == "a.example.com"


def test_create_normalizes_hostname_case_and_blank_optionals(client):
    h = login(client)
    b = body(hosts=("A.Example.COM",))
    b["rules"][0].update(path="", http_host_header="  ")
    assert client.post("/api/tunnels", json=b, headers=h).json()["ok"]
    r = client.get("/api/tunnels/Demo").json()["rules"][0]
    assert r["hostname"] == "a.example.com" and r["path"] is None and r["http_host_header"] is None


def test_create_invalid_returns_ok_false_with_reason(client, fake):
    h = login(client)
    res = client.post("/api/tunnels", json=body(name="bad name"), headers=h).json()
    assert res["ok"] is False and "name" in res["steps"][0]["detail"]
    assert not fake.has_call("tunnel", "create")


def test_unknown_tunnel_404(client):
    login(client)
    assert client.get("/api/tunnels/Ghost").status_code == 404
    assert client.get("/api/tunnels/Ghost/logs").status_code == 404


def test_edit_via_put(client, manager):
    h = login(client)
    seed(manager, make_tunnel())
    res = client.put("/api/tunnels/Demo", json=body(hosts=("a.example.com", "z.example.com")), headers=h).json()
    assert res["ok"], res
    assert [r["hostname"] for r in client.get("/api/tunnels/Demo").json()["rules"]] == ["a.example.com", "z.example.com"]


def test_preview_endpoint(client, manager):
    h = login(client)
    seed(manager, make_tunnel())
    p = client.post("/api/tunnels/preview", json={"name": "Demo", "tunnel": body(hosts=("a.example.com", "n.example.com"))}, headers=h).json()
    assert p["errors"] == [] and p["added_hostnames"] == ["n.example.com"] and "n.example.com" in p["yaml"]


def test_control_logs_and_delete(client, manager, fake):
    h = login(client)
    seed(manager, make_tunnel())
    assert client.post("/api/tunnels/Demo/restart", headers=h).json()["ok"]
    assert client.post("/api/tunnels/Demo/explode", headers=h).json()["ok"] is False
    fake.when_has("journalctl", result=Result(0, "hello log\n", ""))
    assert client.get("/api/tunnels/Demo/logs?lines=5").json()["logs"] == "hello log\n"
    assert client.post("/api/tunnels/Demo/delete", json={"confirm": "nope"}, headers=h).json()["ok"] is False
    res = client.post("/api/tunnels/Demo/delete", json={"confirm": "Demo"}, headers=h).json()
    assert res["ok"] and res["data"]["leftover_dns"] == ["a.example.com"]


def test_logs_lines_are_clamped(client, manager, fake):
    login(client)
    seed(manager, make_tunnel())
    client.get("/api/tunnels/Demo/logs?lines=999999")
    call = next(a for a in fake.argv() if a[0] == "journalctl")
    assert call[call.index("-n") + 1] == "1000"


# ---- env ----

def test_env_endpoint_and_refresh(client, tmp_path, manager):
    login(client)
    seed(manager, make_tunnel(user="toannc"))
    info = client.get("/api/env").json()
    assert info["version"] == "2025.8.1" and info["latest"] == "2099.1.1" and info["update_available"] is True
    assert info["certs"] == {"toannc": False}
    h = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
    assert client.post("/api/env/refresh", headers=h).status_code == 200


def test_env_survives_unreachable_github(settings, manager, fake):
    settings.password_hash = hash_password(PW)
    fake.when_has("--version", result=Result(0, "cloudflared version 2025.8.1\n", ""))

    def boom():
        raise OSError("offline")

    c = TestClient(create_app(settings, manager, EnvService(manager.cf, settings, LatestCache(fetch=boom))))
    login(c)
    r = c.get("/api/env")
    assert r.status_code == 200 and r.json()["latest_error"] == "offline" and r.json()["version"] == "2025.8.1"


def test_env_update_uses_latest_version(client, fake):
    h = login(client)
    fake.when_has("--print-architecture", result=Result(0, "amd64\n", ""))
    res = client.post("/api/env/update", json={"restart": False}, headers=h).json()
    assert res["ok"], res
    curl = next(a for a in fake.argv() if a[0] == "curl")
    assert "/2099.1.1/" in curl[-1]


# ---- import ----

def test_import_scan_and_commit(client, settings, tmp_path):
    h = login(client)
    cfg_dir = tmp_path / "home" / "toannc" / ".cloudflared"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config-HotonChat.yaml").write_text(fixture("hotonchat.yaml"))
    [c] = client.get("/api/import/scan").json()
    assert c["name"] == "HotonChat" and c["importable"] and not c["known"] and c["rule_count"] == 8
    res = client.post("/api/import", json={"names": ["HotonChat", "Nope"]}, headers=h).json()
    assert res["imported"] == ["HotonChat"]
    assert client.get("/api/tunnels").json()[0]["name"] == "HotonChat"
    assert client.get("/api/import/scan").json()[0]["known"] is True


# ---- static ----

def test_index_served_without_login(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>" in r.text
