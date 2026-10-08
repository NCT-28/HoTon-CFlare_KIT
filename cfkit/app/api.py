from __future__ import annotations

import hmac
import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import importer
from app.auth import LoginLimiter, verify_password
from app.config import Settings
from app.env import EnvService
from app.flows import FlowResult, Manager
from app.models import Rule, Tunnel, hostnames

CSP = "default-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


class LoginIn(BaseModel):
    password: str


class RuleIn(BaseModel):
    hostname: str
    service: str
    path: str | None = None
    http_host_header: str | None = None
    no_tls_verify: bool = False


class TunnelIn(BaseModel):
    name: str
    user_name: str
    project: str = ""
    note: str = ""
    rules: list[RuleIn] = []


class PreviewIn(BaseModel):
    name: str | None = None
    tunnel: TunnelIn


class ConfirmIn(BaseModel):
    confirm: str


class UpdateIn(BaseModel):
    restart: bool = False


class ImportIn(BaseModel):
    names: list[str]


def _blank_to_none(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def to_tunnel(b: TunnelIn) -> Tunnel:
    return Tunnel(
        name=b.name, user_name=b.user_name, project=b.project.strip(), note=b.note.strip(),
        rules=[
            Rule(
                hostname=r.hostname.strip().lower(), service=r.service.strip(),
                path=_blank_to_none(r.path), http_host_header=_blank_to_none(r.http_host_header),
                no_tls_verify=r.no_tls_verify,
            )
            for r in b.rules
        ],
    )


def out(res: FlowResult) -> dict:
    return {"ok": res.ok, "steps": [asdict(s) for s in res.steps], "data": res.data}


def create_app(settings: Settings, manager: Manager, env_service: EnvService) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    limiter = LoginLimiter()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="cfkit_session",
        same_site="strict", https_only=settings.https_only, max_age=8 * 3600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    def need_auth(request: Request) -> None:
        if not request.session.get("auth"):
            raise HTTPException(401, "login required")

    def need_csrf(request: Request) -> None:
        token = request.session.get("csrf", "")
        sent = request.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, sent):
            raise HTTPException(403, "bad csrf token")

    read = [Depends(need_auth)]
    write = [Depends(need_auth), Depends(need_csrf)]

    def summary(t: Tunnel) -> dict:
        return {
            "name": t.name, "user_name": t.user_name, "project": t.project, "note": t.note,
            "uuid": t.uuid, "hostnames": hostnames(t), "rule_count": len(t.rules),
            "status": manager.svc.is_active(t.name),
        }

    def get_or_404(name: str) -> Tunnel:
        t = manager.db.get_tunnel(name)
        if t is None:
            raise HTTPException(404, "tunnel not found")
        return t

    # ---- auth ----

    @app.post("/api/login")
    def login(body: LoginIn, request: Request):
        ip = request.client.host if request.client else "unknown"
        if limiter.blocked(ip):
            raise HTTPException(429, "too many attempts, try again later")
        if not verify_password(settings.password_hash, body.password):
            limiter.fail(ip)
            raise HTTPException(401, "invalid credentials")
        limiter.reset(ip)
        request.session.clear()
        request.session["auth"] = True
        request.session["csrf"] = secrets.token_urlsafe(32)
        return {"ok": True, "csrf": request.session["csrf"]}

    @app.post("/api/logout", dependencies=write)
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/session")
    def session(request: Request):
        if request.session.get("auth"):
            return {"authenticated": True, "csrf": request.session["csrf"]}
        return {"authenticated": False}

    # ---- env ----

    def users() -> list[str]:
        return sorted({t.user_name for t in manager.db.list_tunnels()})

    @app.get("/api/env", dependencies=read)
    def env_info():
        return env_service.collect(users())

    @app.post("/api/env/refresh", dependencies=write)
    def env_refresh():
        return env_service.collect(users(), force=True)

    @app.post("/api/env/update", dependencies=write)
    def env_update(body: UpdateIn):
        latest = env_service.collect(users())["latest"]
        if not latest:
            raise HTTPException(400, "latest version unknown; check connectivity and refresh")
        return out(manager.update_cloudflared(latest, body.restart))

    # ---- tunnels ----

    @app.get("/api/tunnels", dependencies=read)
    def list_tunnels():
        return [summary(t) for t in manager.db.list_tunnels()]

    @app.post("/api/tunnels/preview", dependencies=write)
    def preview(body: PreviewIn):
        return manager.preview(body.name, to_tunnel(body.tunnel))

    @app.get("/api/tunnels/{name}", dependencies=read)
    def get_tunnel(name: str):
        t = get_or_404(name)
        return {**summary(t), "rules": [asdict(r) for r in t.rules]}

    @app.post("/api/tunnels", dependencies=write)
    def create_tunnel(body: TunnelIn):
        return out(manager.create(to_tunnel(body)))

    @app.put("/api/tunnels/{name}", dependencies=write)
    def edit_tunnel(name: str, body: TunnelIn):
        return out(manager.edit(name, to_tunnel(body)))

    @app.post("/api/tunnels/{name}/delete", dependencies=write)
    def delete_tunnel(name: str, body: ConfirmIn):
        return out(manager.delete(name, body.confirm))

    @app.post("/api/tunnels/{name}/{action}", dependencies=write)
    def control(name: str, action: str):
        return out(manager.control(name, action))

    @app.get("/api/tunnels/{name}/logs", dependencies=read)
    def logs(name: str, lines: int = 200):
        get_or_404(name)
        return {"logs": manager.svc.logs(name, max(1, min(lines, 1000)))}

    # ---- import ----

    def scan() -> list[importer.Candidate]:
        known = {t.name for t in manager.db.list_tunnels()}
        return importer.scan(settings.discover_homes(), settings.systemd_dir, known)

    @app.get("/api/import/scan", dependencies=read)
    def import_scan():
        return [
            {
                "name": c.name, "user_name": c.user_name, "uuid": c.uuid, "rule_count": len(c.rules),
                "warnings": c.warnings, "known": c.known, "error": c.error, "importable": c.importable,
            }
            for c in scan()
        ]

    @app.post("/api/import", dependencies=write)
    def import_commit(body: ImportIn):
        wanted = set(body.names)
        picked = [c for c in scan() if c.name in wanted]
        return {"imported": importer.import_candidates(manager.db, picked)}

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
