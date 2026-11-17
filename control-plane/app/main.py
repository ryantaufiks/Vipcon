import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import HEARTBEAT_RETENTION_DAYS
from sqlalchemy import inspect, text

from db import SessionLocal, engine
from models import Base, Heartbeat, now
import admin
import device_api
from services import sync_cert_wanted
from web_common import LoginRequired, render

log = logging.getLogger("control-plane")


def prune_heartbeats():
    db = SessionLocal()
    try:
        cutoff = now() - timedelta(days=HEARTBEAT_RETENTION_DAYS)
        deleted = db.query(Heartbeat).filter(Heartbeat.ts < cutoff).delete()
        db.commit()
        if deleted:
            log.info("pruned %d old heartbeats", deleted)
    finally:
        db.close()


async def maintenance_loop():
    while True:
        try:
            await asyncio.to_thread(prune_heartbeats)
        except Exception:
            log.exception("maintenance failed")
        await asyncio.sleep(3600)


def migrate():
    """Columns added after the first deploy (create_all only creates missing tables)."""
    cols = {c["name"] for c in inspect(engine).get_columns("devices")}
    with engine.begin() as conn:
        if "site_id" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN site_id VARCHAR(40)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_devices_site_id ON devices (site_id)"))
        if "dns_ip" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN dns_ip VARCHAR(45)"))


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    migrate()
    db = SessionLocal()
    try:
        sync_cert_wanted(db)
    finally:
        db.close()
    task = asyncio.create_task(maintenance_loop())
    yield
    task.cancel()


app = FastAPI(title="Vipcon Control Plane", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
app.include_router(device_api.router)
app.include_router(admin.router)

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", status_code=exc.status_code, code=exc.status_code, detail=exc.detail)


@app.get("/healthz")
def healthz():
    return {"ok": True}
