from app.models import Tunnel
from app.render import render_unit, render_yaml
from app.validate import validate_tunnel
from tests.samples import HOTON_UUID, SILO_UUID, fixture, hoton_rules, silo_rules


def test_hotonchat_yaml_matches_golden():
    out = render_yaml(HOTON_UUID, f"/home/toannc/.cloudflared/{HOTON_UUID}.json", hoton_rules())
    assert out == fixture("hotonchat.yaml")


def test_silos3_yaml_matches_golden():
    out = render_yaml(SILO_UUID, f"/root/.cloudflared/{SILO_UUID}.json", silo_rules())
    assert out == fixture("silos3.yaml")


def test_catch_all_is_always_last_and_unique():
    out = render_yaml(HOTON_UUID, "/c.json", silo_rules())
    assert out.rstrip().endswith("  - service: http_status:404")
    assert out.count("http_status:404") == 1


def test_golden_samples_pass_validation():
    assert validate_tunnel(Tunnel("HotonChat", "toannc", hoton_rules())) == []
    assert validate_tunnel(Tunnel("SiloS3", "root", silo_rules())) == []


def test_unit_matches_golden():
    out = render_unit(
        name="HotonChat", cloudflared_bin="/usr/bin/cloudflared",
        config_file="/home/toannc/.cloudflared/config-HotonChat.yaml",
        user="toannc", group="toannc", home="/home/toannc",
    )
    assert out == fixture("hotonchat.service")
