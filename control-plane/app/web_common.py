import hmac
import os
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import COOKIE_SECURE, ROOT_DOMAIN, env
from db import get_db
from models import SuperAdmin, aware
from security import SESSION_COOKIE, decode_session

DISPLAY_TZ = ZoneInfo(env("DISPLAY_TZ", "Asia/Jakarta"))
FLASH_COOKIE = "vipcon_cp_flash"

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def fmt_dt(value, with_time=True):
    if not value:
        return "—"
    if hasattr(value, "hour"):
        value = aware(value).astimezone(DISPLAY_TZ)
        return value.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")
    return value.isoformat()


def fmt_money(value):
    return f"{value or 0:,}"


templates.env.filters["dt"] = fmt_dt
templates.env.filters["money"] = fmt_money
templates.env.globals["ROOT_DOMAIN"] = ROOT_DOMAIN
templates.env.globals["CURRENCY"] = env("CURRENCY", "SGD")


def client_ip(request: Request):
    # The app is only reachable through cloudflared on localhost, so these headers are trusted.
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )


class LoginRequired(Exception):
    pass


def current_admin(request: Request, db=Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise LoginRequired()
    try:
        session = decode_session(token)
    except Exception:
        raise LoginRequired()
    admin = db.query(SuperAdmin).filter_by(id=int(session["sub"]), active=True).first()
    if not admin:
        raise LoginRequired()
    request.state.admin = admin
    request.state.csrf = session["csrf"]
    return admin


async def check_csrf(request: Request):
    form = await request.form()
    expected = getattr(request.state, "csrf", None)

    if not expected or not hmac.compare_digest(str(form.get("csrf") or ""), expected):
        raise HTTPException(403, "CSRF check failed; reload the page and try again")
    return form


def render(request: Request, name, status_code=200, **context):
    flash = request.cookies.get(FLASH_COOKIE)
    context.update(
        request=request,
        admin=getattr(request.state, "admin", None),
        csrf=getattr(request.state, "csrf", ""),
        flash=unquote(flash) if flash else None,
    )
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    if flash:
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response


def redirect(url, flash=None):
    response = RedirectResponse(url, status_code=303)
    if flash:
        response.set_cookie(
            FLASH_COOKIE, quote(flash), max_age=60, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/"
        )
    return response
