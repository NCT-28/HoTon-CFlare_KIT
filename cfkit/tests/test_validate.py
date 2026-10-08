import pytest

from app.models import Rule, Tunnel
from app.validate import validate_tunnel


def rule(**kw):
    base = dict(hostname="app.example.com", service="http://127.0.0.1:5500")
    base.update(kw)
    return Rule(**base)


def tunnel(**kw):
    base = dict(name="HotonChat", user_name="toannc", rules=[rule()])
    base.update(kw)
    return Tunnel(**base)


def errs(t):
    return validate_tunnel(t)


def test_valid_tunnel_has_no_errors():
    assert errs(tunnel()) == []


@pytest.mark.parametrize("name", ["", "1abc", "a b", "a;rm -rf /", "name\n", "a/b", "x" * 64, "$(id)", "a.b"])
def test_bad_names_rejected(name):
    assert any(e.startswith("name:") for e in errs(tunnel(name=name)))


@pytest.mark.parametrize("user", ["", "Root", "a b", "root\n", "-x", "a;b"])
def test_bad_users_rejected(user):
    assert any(e.startswith("user:") for e in errs(tunnel(user_name=user)))


@pytest.mark.parametrize("host", [
    "", "UPPER.example.com", "no_dot", "a b.com", "a.com\n", "-a.example.com",
    "a.example.com;ls", "*.example.com", "a..com",
])
def test_bad_hostnames_rejected(host):
    assert any("hostname" in e for e in errs(tunnel(rules=[rule(hostname=host)])))


@pytest.mark.parametrize("service", [
    "", "ftp://127.0.0.1:1", "http://127.0.0.1", "http://127.0.0.1:0",
    "http://127.0.0.1:70000", "http://a b:80", "http://127.0.0.1:80\n", "http://x:80/path",
])
def test_bad_services_rejected(service):
    assert any("service" in e for e in errs(tunnel(rules=[rule(service=service)])))


@pytest.mark.parametrize("path", ["api", "/a b", "/a#b", "/a\nb", "/x;y", "/a\n"])
def test_bad_paths_rejected(path):
    assert any("path" in e for e in errs(tunnel(rules=[rule(path=path)])))


@pytest.mark.parametrize("header", ["a b", 'x"y', "h\n", "h:99999x"])
def test_bad_host_headers_rejected(header):
    assert any("host header" in e for e in errs(tunnel(rules=[rule(http_host_header=header)])))


def test_good_optional_fields_accepted():
    r = rule(path="/hoton-chat*", http_host_header="localhost:5507", no_tls_verify=True)
    assert errs(tunnel(rules=[r])) == []


def test_empty_string_path_and_header_mean_unset():
    assert errs(tunnel(rules=[rule(path="", http_host_header="")])) == []


def test_at_least_one_rule():
    assert any("at least one rule" in e for e in errs(tunnel(rules=[])))


def test_duplicate_hostname_and_path():
    rules = [rule(path="/a"), rule(path="/a")]
    assert any("duplicate" in e for e in errs(tunnel(rules=rules)))


def test_rule_after_pathless_rule_for_same_host_is_unreachable():
    rules = [rule(), rule(path="/x")]
    assert any("rule 2" in e and "unreachable" in e for e in errs(tunnel(rules=rules)))


def test_pathless_rule_last_is_fine():
    assert errs(tunnel(rules=[rule(path="/x"), rule()])) == []


def test_other_host_after_pathless_rule_is_fine():
    assert errs(tunnel(rules=[rule(), rule(hostname="b.example.com", path="/x")])) == []


@pytest.mark.parametrize("field", ["project", "note"])
def test_project_and_note_no_newline_and_length(field):
    assert any(e.startswith(field) for e in errs(tunnel(**{field: "a\nb"})))
    assert any(e.startswith(field) for e in errs(tunnel(**{field: "x" * 201})))
