from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.models import Rule, Tunnel

SCHEMA = """
CREATE TABLE IF NOT EXISTS tunnels(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  tunnel_uuid TEXT,
  user_name TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingress_rules(
  id INTEGER PRIMARY KEY,
  tunnel_id INTEGER NOT NULL REFERENCES tunnels(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  hostname TEXT NOT NULL,
  path TEXT,
  service TEXT NOT NULL,
  http_host_header TEXT,
  no_tls_verify INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS config_backups(
  id INTEGER PRIMARY KEY,
  tunnel_id INTEGER NOT NULL REFERENCES tunnels(id) ON DELETE CASCADE,
  yaml_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

KEEP_BACKUPS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _load(c: sqlite3.Connection, row: sqlite3.Row) -> Tunnel:
        rules = [
            Rule(
                hostname=r["hostname"], service=r["service"], path=r["path"],
                http_host_header=r["http_host_header"], no_tls_verify=bool(r["no_tls_verify"]),
            )
            for r in c.execute("SELECT * FROM ingress_rules WHERE tunnel_id=? ORDER BY position", (row["id"],))
        ]
        return Tunnel(
            name=row["name"], user_name=row["user_name"], rules=rules,
            project=row["project"], note=row["note"], uuid=row["tunnel_uuid"],
        )

    def save_tunnel(self, t: Tunnel) -> None:
        now = _now()
        with self._conn() as c:
            row = c.execute("SELECT id FROM tunnels WHERE name=?", (t.name,)).fetchone()
            if row is None:
                tid = c.execute(
                    "INSERT INTO tunnels(name,tunnel_uuid,user_name,project,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (t.name, t.uuid, t.user_name, t.project, t.note, now, now),
                ).lastrowid
            else:
                tid = row["id"]
                c.execute(
                    "UPDATE tunnels SET tunnel_uuid=?, user_name=?, project=?, note=?, updated_at=? WHERE id=?",
                    (t.uuid, t.user_name, t.project, t.note, now, tid),
                )
                c.execute("DELETE FROM ingress_rules WHERE tunnel_id=?", (tid,))
            c.executemany(
                "INSERT INTO ingress_rules(tunnel_id,position,hostname,path,service,http_host_header,no_tls_verify) VALUES(?,?,?,?,?,?,?)",
                [(tid, i, r.hostname, r.path, r.service, r.http_host_header, int(r.no_tls_verify)) for i, r in enumerate(t.rules)],
            )

    def get_tunnel(self, name: str) -> Tunnel | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tunnels WHERE name=?", (name,)).fetchone()
            return self._load(c, row) if row else None

    def list_tunnels(self) -> list[Tunnel]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM tunnels ORDER BY project, name").fetchall()
            return [self._load(c, r) for r in rows]

    def delete_tunnel(self, name: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM tunnels WHERE name=?", (name,))

    def add_backup(self, name: str, yaml_text: str) -> None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM tunnels WHERE name=?", (name,)).fetchone()
            if row is None:
                return
            c.execute(
                "INSERT INTO config_backups(tunnel_id,yaml_text,created_at) VALUES(?,?,?)",
                (row["id"], yaml_text, _now()),
            )
            c.execute(
                "DELETE FROM config_backups WHERE tunnel_id=? AND id NOT IN "
                "(SELECT id FROM config_backups WHERE tunnel_id=? ORDER BY id DESC LIMIT ?)",
                (row["id"], row["id"], KEEP_BACKUPS),
            )

    def list_backups(self, name: str) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT b.yaml_text FROM config_backups b JOIN tunnels t ON t.id=b.tunnel_id "
                "WHERE t.name=? ORDER BY b.id DESC",
                (name,),
            ).fetchall()
            return [r["yaml_text"] for r in rows]
