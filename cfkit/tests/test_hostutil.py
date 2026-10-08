import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parent.parent / "static" / "hostutil.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def js(expr: str):
    code = f"const H = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


D = "f1p.info.vn"


@pytest.mark.parametrize("sub,expected", [
    ("api", "api.f1p.info.vn"),
    ("  API ", "api.f1p.info.vn"),
    ("a.b", "a.b.f1p.info.vn"),
    ("@", "f1p.info.vn"),
    ("f1p.info.vn", "f1p.info.vn"),
    ("api.f1p.info.vn", "api.f1p.info.vn"),
    ("", ""),
])
def test_to_host_with_domain(sub, expected):
    assert js(f"H.toHost({json.dumps(sub)}, {json.dumps(D)})") == expected


def test_to_host_without_domain_keeps_input_lowercased():
    assert js('H.toHost(" Api.Example.COM ", "")') == "api.example.com"


@pytest.mark.parametrize("host,expected", [
    ("api.f1p.info.vn", "api"),
    ("a.b.f1p.info.vn", "a.b"),
    ("f1p.info.vn", "@"),
    ("", ""),
    ("other.example.com", None),
    ("notf1p.info.vn", None),
])
def test_sub_of(host, expected):
    assert js(f"H.subOf({json.dumps(host)}, {json.dumps(D)})") == expected


def test_sub_of_without_domain_is_null():
    assert js('H.subOf("api.f1p.info.vn", "")') is None


def test_normalize_domain():
    assert js('H.normalizeDomain("  .F1P.info.vn. ")') == "f1p.info.vn"
