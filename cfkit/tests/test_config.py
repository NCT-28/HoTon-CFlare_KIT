from pathlib import Path

from app.config import load_settings


def test_load_settings_defaults():
    s = load_settings({})
    assert s.port == 8787 and s.host == "127.0.0.1"
    assert s.db_path == Path("/var/lib/cfkit/cfkit.db")
    assert s.allowed_hosts == ["127.0.0.1", "localhost"]
    assert s.password_hash == "" and s.https_only is False


def test_load_settings_from_env():
    s = load_settings({
        "CFKIT_PORT": "9000", "CFKIT_HOST": "0.0.0.0", "CFKIT_DB": "/tmp/x.db",
        "CFKIT_PASSWORD_HASH": "h", "CFKIT_SECRET": "s", "CFKIT_HTTPS_ONLY": "1",
        "CFKIT_ALLOWED_HOSTS": "a.example.com, localhost",
    })
    assert (s.port, s.host, s.db_path) == (9000, "0.0.0.0", Path("/tmp/x.db"))
    assert (s.password_hash, s.secret_key, s.https_only) == ("h", "s", True)
    assert s.allowed_hosts == ["a.example.com", "localhost"]
