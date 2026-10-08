from __future__ import annotations

from app.models import Rule


def render_yaml(uuid: str, credentials_file: str, rules: list[Rule]) -> str:
    """Values must already be validated (validate.py); they are written unquoted."""
    lines = [f"tunnel: {uuid}", f"credentials-file: {credentials_file}", "", "ingress:"]
    for r in rules:
        lines.append(f"  - hostname: {r.hostname}")
        if r.path:
            lines.append(f"    path: {r.path}")
        lines.append(f"    service: {r.service}")
        opts: list[str] = []
        if r.http_host_header:
            opts.append(f'      httpHostHeader: "{r.http_host_header}"')
        if r.no_tls_verify:
            opts.append("      noTLSVerify: true")
        if opts:
            lines.append("    originRequest:")
            lines += opts
    lines.append("  - service: http_status:404")
    return "\n".join(lines) + "\n"


def render_unit(name: str, cloudflared_bin: str, config_file: str, user: str, group: str, home: str) -> str:
    return (
        "[Unit]\n"
        f"Description=Cloudflare Tunnel {name}\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={cloudflared_bin} --config {config_file} tunnel run\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        f"User={user}\n"
        f"Group={group}\n"
        f"Environment=HOME={home}\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        f"SyslogIdentifier=cloudflared-{name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
