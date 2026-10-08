import sqlite3

from app.db import Database
from app.models import Rule, Tunnel

U = "3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6"


def make(name="A", **kw):
    base = dict(
        name=name, user_name="root", project="P", note="n", uuid=U,
        rules=[
            Rule("a.example.com", "http://127.0.0.1:1", path="/x", http_host_header="h:1", no_tls_verify=True),
            Rule("b.example.com", "http://127.0.0.1:2"),
        ],
    )
    base.update(kw)
    return Tunnel(**base)


def test_roundtrip_preserves_everything(tmp_path):
    db = Database(tmp_path / "t.db")
    t = make()
    db.save_tunnel(t)
    assert db.get_tunnel("A") == t


def test_get_missing_returns_none(tmp_path):
    assert Database(tmp_path / "t.db").get_tunnel("nope") is None


def test_upsert_replaces_rules_and_order(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    db.save_tunnel(make(rules=[Rule("z.example.com", "http://127.0.0.1:9"), Rule("y.example.com", "http://127.0.0.1:8")], note="new"))
    got = db.get_tunnel("A")
    assert [r.hostname for r in got.rules] == ["z.example.com", "y.example.com"]
    assert got.note == "new"
    assert len(db.list_tunnels()) == 1


def test_list_sorted_by_project_then_name(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make("B", project="Z"))
    db.save_tunnel(make("C", project="A"))
    db.save_tunnel(make("A", project="A"))
    assert [t.name for t in db.list_tunnels()] == ["A", "C", "B"]


def test_delete_cascades_rules_and_backups(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    db.add_backup("A", "yaml")
    db.delete_tunnel("A")
    assert db.get_tunnel("A") is None
    c = sqlite3.connect(tmp_path / "t.db")
    assert c.execute("SELECT COUNT(*) FROM ingress_rules").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM config_backups").fetchone()[0] == 0


def test_backups_newest_first_and_pruned_to_20(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_tunnel(make())
    for i in range(25):
        db.add_backup("A", f"v{i}")
    backups = db.list_backups("A")
    assert len(backups) == 20 and backups[0] == "v24" and backups[-1] == "v5"


def test_backup_for_unknown_tunnel_is_ignored(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_backup("ghost", "x")
    assert db.list_backups("ghost") == []
