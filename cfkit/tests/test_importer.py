from app.db import Database
from app.importer import import_candidates, parse_config, parse_unit_user, scan
from app.render import render_yaml
from tests.samples import HOTON_UUID, fixture, hoton_rules


def test_parse_script_output_with_comments():
    uuid, rules, warnings = parse_config(fixture("hotonchat_script.yaml"))
    assert uuid == HOTON_UUID
    assert rules == hoton_rules()
    assert len(warnings) == 1 and "comment" in warnings[0]


def test_golden_file_has_no_warnings():
    assert parse_config(fixture("hotonchat.yaml"))[2] == []


def test_unknown_top_level_keys_warn():
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /c\nprotocol: quic\nloglevel: debug\ningress:\n  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
    _, rules, warnings = parse_config(text)
    assert len(rules) == 1
    assert any("protocol" in w and "loglevel" in w for w in warnings)


def test_roundtrip_golden_is_identical():
    text = fixture("hotonchat.yaml")
    uuid, rules, _ = parse_config(text)
    assert render_yaml(uuid, f"/home/toannc/.cloudflared/{uuid}.json", rules) == text


def test_unsupported_origin_request_keys_warn_but_rule_kept():
    text = (
        f"tunnel: {HOTON_UUID}\ncredentials-file: /c.json\ningress:\n"
        "  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
        "    originRequest:\n      connectTimeout: 5s\n      noTLSVerify: true\n"
        "  - service: http_status:404\n"
    )
    _, rules, warnings = parse_config(text)
    assert len(rules) == 1 and rules[0].no_tls_verify is True
    assert any("connectTimeout" in w for w in warnings)


def test_rule_without_hostname_warned_and_404_catch_all_silent():
    text = (
        f"tunnel: {HOTON_UUID}\ncredentials-file: /c.json\ningress:\n"
        "  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
        "  - service: http://127.0.0.1:9\n"
    )
    _, rules, warnings = parse_config(text)
    assert [r.hostname for r in rules] == ["a.example.com"]
    assert len(warnings) == 1 and "http://127.0.0.1:9" in warnings[0]


def test_hostnames_are_lowercased():
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /c\ningress:\n  - hostname: A.Example.COM\n    service: http://127.0.0.1:1\n"
    assert parse_config(text)[1][0].hostname == "a.example.com"


def test_parse_unit_user():
    assert parse_unit_user(fixture("hotonchat.service")) == "toannc"
    assert parse_unit_user("[Service]\nExecStart=x\n") is None


def _seed(tmp_path, name="HotonChat", text=None, user="toannc", unit_user=None):
    home = tmp_path / "home" / user
    (home / ".cloudflared").mkdir(parents=True, exist_ok=True)
    (home / ".cloudflared" / f"config-{name}.yaml").write_text(text if text is not None else fixture("hotonchat.yaml"))
    sysd = tmp_path / "systemd"
    sysd.mkdir(exist_ok=True)
    if unit_user:
        (sysd / f"cloudflared-{name}.service").write_text(f"[Service]\nUser={unit_user}\n")
    return {user: home}, sysd


def test_scan_finds_config_and_unit_user(tmp_path):
    homes, sysd = _seed(tmp_path, unit_user="toannc")
    [c] = scan(homes, sysd, known=set())
    assert (c.name, c.user_name, c.uuid, c.known) == ("HotonChat", "toannc", HOTON_UUID, False)
    assert c.importable and c.rules == hoton_rules()


def test_scan_prefers_unit_user_over_directory_owner(tmp_path):
    homes, sysd = _seed(tmp_path, user="root", unit_user="toannc")
    assert scan(homes, sysd, known=set())[0].user_name == "toannc"


def test_scan_marks_known(tmp_path):
    homes, sysd = _seed(tmp_path)
    assert scan(homes, sysd, known={"HotonChat"})[0].known is True


def test_scan_invalid_yaml_not_importable(tmp_path):
    homes, sysd = _seed(tmp_path, text="tunnel: [unclosed\n")
    [c] = scan(homes, sysd, known=set())
    assert not c.importable and c.error and "YAML" in c.error


def test_scan_bad_name_not_importable(tmp_path):
    homes, sysd = _seed(tmp_path, name="bad name")
    [c] = scan(homes, sysd, known=set())
    assert not c.importable and "name" in c.error


def test_validation_problems_become_warnings_not_blockers(tmp_path):
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /c\ningress:\n  - hostname: a.example.com\n    service: http://localhost\n"
    homes, sysd = _seed(tmp_path, text=text)
    [c] = scan(homes, sysd, known=set())
    assert c.importable and any("service" in w for w in c.warnings)


def test_import_candidates_saves_only_new_importable(tmp_path):
    homes, sysd = _seed(tmp_path)
    cands = scan(homes, sysd, known=set())
    db = Database(tmp_path / "t.db")
    assert import_candidates(db, cands) == ["HotonChat"]
    saved = db.get_tunnel("HotonChat")
    assert saved.uuid == HOTON_UUID and saved.rules == hoton_rules() and saved.user_name == "toannc"
    cands2 = scan(homes, sysd, known={"HotonChat"})
    assert import_candidates(db, cands2) == []


def test_scan_warns_when_credentials_file_differs_from_derived_path(tmp_path):
    text = f"tunnel: {HOTON_UUID}\ncredentials-file: /etc/cloudflared/x.json\ningress:\n  - hostname: a.example.com\n    service: http://127.0.0.1:1\n"
    homes, sysd = _seed(tmp_path, text=text)
    [c] = scan(homes, sysd, known=set())
    assert c.importable and any("credentials-file" in w and "/etc/cloudflared/x.json" in w for w in c.warnings)


def test_scan_matching_credentials_file_no_warning(tmp_path):
    home = tmp_path / "home" / "toannc"
    text = fixture("hotonchat.yaml").replace("/home/toannc", str(home))
    homes, sysd = _seed(tmp_path, text=text)
    [c] = scan(homes, sysd, known=set())
    assert not any("credentials-file" in w for w in c.warnings)


def test_scan_does_not_follow_symlinked_config(tmp_path):
    homes, sysd = _seed(tmp_path)
    cfg = tmp_path / "home" / "toannc" / ".cloudflared" / "config-HotonChat.yaml"
    secret = tmp_path / "secret.yaml"
    secret.write_text(fixture("hotonchat.yaml"))
    cfg.unlink()
    cfg.symlink_to(secret)
    [c] = scan(homes, sysd, known=set())
    assert not c.importable and c.error and "read" in c.error
