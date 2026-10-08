from app.cf import Cloudflared
from app.config import Settings
from app.env import EnvService, LatestCache, parse_version
from app.runner import Result
from tests.fakes import FakeRunner


def test_parse_version_compares_numerically():
    assert parse_version("2025.8.1") == (2025, 8, 1)
    assert parse_version("2025.10.0") > parse_version("2025.9.5")
    assert parse_version("") == ()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_cache_respects_ttl_and_force():
    calls = []
    clock = Clock()
    cache = LatestCache(fetch=lambda: calls.append(1) or "2025.9.0", ttl=100, clock=clock)
    assert cache.get() == ("2025.9.0", None)
    clock.t = 50
    cache.get()
    assert len(calls) == 1
    clock.t = 101
    cache.get()
    assert len(calls) == 2
    cache.get(force=True)
    assert len(calls) == 3


def test_cache_failure_reports_error_keeps_last_value_and_retries_sooner():
    clock = Clock()
    state = {"fail": False, "n": 0}

    def fetch():
        state["n"] += 1
        if state["fail"]:
            raise OSError("network down")
        return "2025.9.0"

    cache = LatestCache(fetch=fetch, ttl=1000, clock=clock)
    cache.get()
    state["fail"] = True
    clock.t = 1001
    ver, err = cache.get()
    assert ver == "2025.9.0" and "network down" in err
    clock.t = 1001 + 301  # error ttl is 300s
    cache.get()
    assert state["n"] == 3


def service(tmp_path, installed="2025.8.1", latest="2025.9.0", bin_path="/usr/bin/cloudflared", fail_latest=False):
    f = FakeRunner()
    f.when_has("--version", result=Result(0, f"cloudflared version {installed} (built x)\n", ""))
    settings = Settings(db_path=tmp_path / "d.db", home_of=lambda u: tmp_path / u, cloudflared_bin=lambda: bin_path)

    def fetch():
        if fail_latest:
            raise OSError("no route to github")
        return latest

    return EnvService(Cloudflared(f, settings.cloudflared_bin), settings, LatestCache(fetch=fetch))


def test_collect_update_available(tmp_path):
    (tmp_path / "toannc" / ".cloudflared").mkdir(parents=True)
    (tmp_path / "toannc" / ".cloudflared" / "cert.pem").write_text("x")
    info = service(tmp_path).collect(["toannc", "root"])
    assert info["installed"] and info["path"] == "/usr/bin/cloudflared"
    assert (info["version"], info["latest"], info["update_available"]) == ("2025.8.1", "2025.9.0", True)
    assert info["certs"] == {"toannc": True, "root": False}
    assert info["latest_error"] is None


def test_collect_up_to_date(tmp_path):
    assert service(tmp_path, installed="2025.9.0").collect([])["update_available"] is False


def test_collect_latest_unreachable_does_not_break(tmp_path):
    info = service(tmp_path, fail_latest=True).collect([])
    assert info["version"] == "2025.8.1" and info["latest"] is None
    assert info["update_available"] is False and "no route" in info["latest_error"]


def test_collect_not_installed(tmp_path):
    info = service(tmp_path, bin_path=None).collect(["root"])
    assert info["installed"] is False and info["version"] is None and info["update_available"] is False
