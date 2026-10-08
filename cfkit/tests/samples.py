from __future__ import annotations

from pathlib import Path

from app.models import Rule

FIX = Path(__file__).parent / "fixtures"
HOTON_UUID = "3f9a21c0-1b2c-4d3e-8f4a-a1b2c3d4e5f6"
SILO_UUID = "b71e04d8-5a6f-4c7d-9e8f-0123456789ab"


def fixture(name: str) -> str:
    return (FIX / name).read_text()


def hoton_rules() -> list[Rule]:
    h, api, s3, hdr = "f1p.info.vn", "http://127.0.0.1:5500", "http://127.0.0.1:5507", "localhost:5507"
    return (
        [Rule(h, api, path="/api*"),
         Rule(h, api, path="/rsocket*", no_tls_verify=True),
         Rule(h, api, path="/ws*", no_tls_verify=True)]
        + [Rule(h, s3, path=p, http_host_header=hdr)
           for p in ("/hoton-avatars*", "/hoton-chat*", "/hoton-blog*", "/hoton-feeds*")]
        + [Rule(h, "http://127.0.0.1:5501")]
    )


def silo_rules() -> list[Rule]:
    return [
        Rule("s3.f1p.info.vn", "http://127.0.0.1:5507", http_host_header="localhost:5507"),
        Rule("silo-api.f1p.info.vn", "http://127.0.0.1:5508"),
    ]
