"""Superadmin web UI (server-rendered, no JavaScript required)."""

import time
from collections import defaultdict, deque
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func

from config import COOKIE_SECURE, SESSION_HOURS
from db import get_db
from models import (
    CONNECTIVITY_MODES,
    CONTRACT_STATUSES,
    AuditLog,
    BrandingLogo,
    Client,
    Contract,
    Device,
    Heartbeat,
    Plan,
    SuperAdmin,
    aware,
    now,
)
from security import SESSION_COOKIE, create_session, hash_password, sha256_hex, verify_password, verify_totp
from services import (
    audit,
    create_device,
    current_contract,
    is_online,
    LOGO_MAX_UPLOAD,
    TAGLINE_MAX,
    LogoError,
    get_logo,
    get_tagline,
    license_status,
    new_claim_code,
    provisioning_bundle,
    set_logo,
    set_tagline,
)
from web_common import check_csrf, client_ip, current_admin, redirect, render

router = APIRouter()

LOCK_AFTER = 5
LOCK_MINUTES = 15
_login_hits = defaultdict(deque)


def _ip_rate_limited(ip, limit=10, window=60):
    hits = _login_hits[ip]
    t = time.monotonic()
    while hits and t - hits[0] > window:
        hits.popleft()
    hits.append(t)
    return len(hits) > limit


# ---------------------------------------------------------------- form helpers


def text(form, key, required=False, max_len=200):
    value = str(form.get(key) or "").strip()[:max_len]
    if required and not value:
        raise HTTPException(400, f"{key} is required")
    return value


def integer(form, key, default=0, minimum=0, maximum=10**12):
    raw = str(form.get(key) or "").strip().replace(",", "").replace(" ", "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(400, f"{key} must be a whole number")
    if not minimum <= value <= maximum:
        raise HTTPException(400, f"{key} must be between {minimum} and {maximum}")
    return value


def optional_id(form, key):
    raw = str(form.get(key) or "").strip()
    return int(raw) if raw.isdigit() else None


def choice(form, key, options):
    value = str(form.get(key) or "")
    if value not in options:
        raise HTTPException(400, f"{key} must be one of {', '.join(options)}")
    return value


def day(form, key):
    try:
        return date.fromisoformat(str(form.get(key) or ""))
    except ValueError:
        raise HTTPException(400, f"{key} must be a date")


def get_or_404(db, model, obj_id):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, "not found")
    return obj


# ---------------------------------------------------------------- auth


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
async def login(request: Request, db=Depends(get_db)):
    form = await request.form()
    ip = client_ip(request)
    error = "Incorrect email, password or 2FA code."
    if _ip_rate_limited(ip):
        return render(request, "login.html", status_code=429, error="Too many attempts. Please wait a moment and try again.")
    email = str(form.get("email") or "").strip().lower()
    admin = db.query(SuperAdmin).filter_by(email=email, active=True).first()
    if not admin:
        return render(request, "login.html", status_code=401, error=error, email=email)
    if admin.locked_until and aware(admin.locked_until) > now():
        return render(request, "login.html", status_code=423, error="Account temporarily locked. Please try again later.")
    step = None
    if verify_password(str(form.get("password") or ""), admin.password_hash):
        step = verify_totp(admin.totp_secret, str(form.get("totp") or ""), admin.last_totp_step)
    if step is None:
        admin.failed_attempts = (admin.failed_attempts or 0) + 1
        if admin.failed_attempts >= LOCK_AFTER:
            admin.locked_until = now() + timedelta(minutes=LOCK_MINUTES)
            admin.failed_attempts = 0
            audit(db, admin.email, "superadmin.locked", admin.email, ip)
        db.commit()
        return render(request, "login.html", status_code=401, error=error, email=email)
    admin.failed_attempts = 0
    admin.locked_until = None
    admin.last_totp_step = step
    admin.last_login = now()
    audit(db, admin.email, "superadmin.login", admin.email, ip)
    db.commit()
    response = redirect("/")
    response.set_cookie(
        SESSION_COOKIE,
        create_session(admin.id),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    await check_csrf(request)
    audit(db, admin.email, "superadmin.logout", admin.email, client_ip(request))
    db.commit()
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/account")
def account_page(request: Request, admin=Depends(current_admin)):
    return render(request, "account.html")


@router.post("/account/password")
async def change_password(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    if not verify_password(str(form.get("current") or ""), admin.password_hash):
        return render(request, "account.html", status_code=400, error="Current password is incorrect.")
    new = str(form.get("new") or "")
    if len(new) < 12:
        return render(request, "account.html", status_code=400, error="New password must be at least 12 characters.")
    if new != str(form.get("confirm") or ""):
        return render(request, "account.html", status_code=400, error="Passwords do not match.")
    admin.password_hash = hash_password(new)
    audit(db, admin.email, "superadmin.password_changed", admin.email, client_ip(request))
    db.commit()
    return redirect("/account", "Password changed.")


# ---------------------------------------------------------------- dashboard


def device_rows(db, devices):
    latest = {}
    ids = [d.device_id for d in devices]
    if ids:
        sub = (
            db.query(Heartbeat.device_id, func.max(Heartbeat.id).label("mid"))
            .filter(Heartbeat.device_id.in_(ids))
            .group_by(Heartbeat.device_id)
            .subquery()
        )
        for hb in db.query(Heartbeat).join(sub, Heartbeat.id == sub.c.mid):
            latest[hb.device_id] = hb
    rows = []
    for d in devices:
        status, contract = license_status(db, d)
        rows.append({"device": d, "online": is_online(d), "hb": latest.get(d.device_id), "license": status, "contract": contract})
    return rows


@router.get("/")
def dashboard(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    devices = db.query(Device).order_by(Device.id.desc()).all()
    rows = device_rows(db, devices)
    soon = date.today() + timedelta(days=30)
    expiring = (
        db.query(Contract)
        .filter(Contract.status == "active", Contract.end_date >= date.today(), Contract.end_date <= soon)
        .order_by(Contract.end_date)
        .all()
    )
    stats = {
        "devices": len(devices),
        "online": sum(1 for r in rows if r["online"]),
        "claimed": sum(1 for d in devices if d.claimed_at),
        "clients": db.query(Client).count(),
        "attention": sum(1 for r in rows if r["license"] != "active" and r["device"].status != "revoked"),
    }
    return render(request, "dashboard.html", rows=rows, stats=stats, expiring=expiring)


# ---------------------------------------------------------------- plans


@router.get("/plans")
def plans(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(request, "plans.html", plans=db.query(Plan).order_by(Plan.id).all())


@router.get("/plans/new")
def plan_new(request: Request, admin=Depends(current_admin)):
    return render(request, "plan_form.html", plan=None, modes=CONNECTIVITY_MODES)


@router.get("/plans/{plan_id}")
def plan_edit(plan_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(request, "plan_form.html", plan=get_or_404(db, Plan, plan_id), modes=CONNECTIVITY_MODES)


def apply_plan_form(plan, form):
    plan.name = text(form, "name", required=True)
    plan.price_monthly = integer(form, "price_monthly")
    plan.price_yearly = integer(form, "price_yearly")
    plan.max_participants = integer(form, "max_participants", 10, 1, 100)
    plan.max_users = integer(form, "max_users", 20, 1, 1000)
    plan.max_concurrent_meetings = integer(form, "max_concurrent_meetings", 5, 1, 50)
    plan.connectivity_mode = choice(form, "connectivity_mode", CONNECTIVITY_MODES)
    plan.features = {"e2ee_default": form.get("e2ee_default") == "on"}
    plan.active = form.get("active") == "on"


@router.post("/plans")
async def plan_create(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    plan = Plan()
    apply_plan_form(plan, form)
    db.add(plan)
    db.flush()
    audit(db, admin.email, "plan.created", plan.id, client_ip(request), name=plan.name)
    db.commit()
    return redirect("/plans", f"Plan {plan.name} created.")


@router.post("/plans/{plan_id}")
async def plan_update(plan_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    plan = get_or_404(db, Plan, plan_id)
    apply_plan_form(plan, form)
    audit(db, admin.email, "plan.updated", plan.id, client_ip(request), name=plan.name)
    db.commit()
    return redirect("/plans", f"Plan {plan.name} saved.")


# ---------------------------------------------------------------- clients


@router.get("/clients")
def clients(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    rows = db.query(Client).order_by(Client.name).all()
    counts = dict(db.query(Device.client_id, func.count(Device.id)).group_by(Device.client_id).all())
    return render(request, "clients.html", clients=rows, counts=counts)


@router.get("/clients/new")
def client_new(request: Request, admin=Depends(current_admin)):
    return render(request, "client_form.html", client=None)


@router.get("/clients/{client_id}")
def client_detail(client_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    client = get_or_404(db, Client, client_id)
    devices = db.query(Device).filter_by(client_id=client.id).order_by(Device.id).all()
    contracts = db.query(Contract).filter_by(client_id=client.id).order_by(Contract.end_date.desc()).all()
    return render(
        request,
        "client_detail.html",
        client=client,
        rows=device_rows(db, devices),
        contracts=contracts,
        logo=get_logo(db, client.id),
        global_logo=get_logo(db, None),
        tagline=get_tagline(db, client.id),
        global_tagline=get_tagline(db, None),
        tagline_max=TAGLINE_MAX,
    )


@router.get("/clients/{client_id}/edit")
def client_edit(client_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(request, "client_form.html", client=get_or_404(db, Client, client_id))


def apply_client_form(client, form):
    client.name = text(form, "name", required=True)
    client.contact_email = text(form, "contact_email") or None
    client.contact_phone = text(form, "contact_phone") or None
    client.notes = text(form, "notes", max_len=2000) or None


@router.post("/clients")
async def client_create(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    client = Client()
    apply_client_form(client, form)
    db.add(client)
    db.flush()
    audit(db, admin.email, "client.created", client.id, client_ip(request), name=client.name)
    db.commit()
    return redirect(f"/clients/{client.id}", f"Client {client.name} created.")


@router.post("/clients/{client_id}")
async def client_update(client_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    client = get_or_404(db, Client, client_id)
    apply_client_form(client, form)
    audit(db, admin.email, "client.updated", client.id, client_ip(request), name=client.name)
    db.commit()
    return redirect(f"/clients/{client.id}", "Client saved.")


# ---------------------------------------------------------------- branding


@router.get("/branding")
def branding(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    custom = (
        db.query(BrandingLogo, Client)
        .join(Client, BrandingLogo.client_id == Client.id)
        .order_by(Client.name)
        .all()
    )
    return render(
        request, "branding.html", logo=get_logo(db, None), custom=custom,
        tagline=get_tagline(db, None), tagline_max=TAGLINE_MAX,
    )


@router.get("/branding/logo/{logo_id}.png")
def branding_logo_png(logo_id: int, admin=Depends(current_admin), db=Depends(get_db)):
    logo = get_or_404(db, BrandingLogo, logo_id)
    return Response(logo.png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.post("/branding/logo")
async def branding_logo_update(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    client_id = optional_id(form, "client_id")
    client = get_or_404(db, Client, client_id) if client_id else None
    back = f"/clients/{client.id}" if client else "/branding"
    target = f"client:{client.id}" if client else "global"
    if form.get("action") == "delete":
        logo = get_logo(db, client_id)
        if logo:
            db.delete(logo)
            audit(db, admin.email, "branding.logo_removed", target, client_ip(request))
            db.commit()
        return redirect(back, "Logo removed.")
    upload = form.get("logo")
    if upload is None or not hasattr(upload, "read"):
        return redirect(back, "Choose a logo file first.")
    data = await upload.read(LOGO_MAX_UPLOAD + 1)
    try:
        logo = set_logo(db, client_id, data)
    except LogoError as e:
        return redirect(back, f"Logo rejected: {e}")
    audit(db, admin.email, "branding.logo_set", target, client_ip(request),
          sha256=logo.sha256, width=logo.width, height=logo.height)
    db.commit()
    return redirect(back, "Logo saved. Devices pick it up on their next heartbeat.")


@router.post("/branding/tagline")
async def branding_tagline_update(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    client_id = optional_id(form, "client_id")
    client = get_or_404(db, Client, client_id) if client_id else None
    row = set_tagline(db, client_id, str(form.get("tagline") or ""))
    audit(db, admin.email, "branding.tagline_set", f"client:{client.id}" if client else "global",
          client_ip(request), tagline=row.tagline if row else None)
    db.commit()
    return redirect(f"/clients/{client.id}" if client else "/branding",
                    "Tagline saved." if row else "Tagline removed.")


# ---------------------------------------------------------------- devices


def device_form_context(db):
    return {
        "clients": db.query(Client).order_by(Client.name).all(),
        "plans": db.query(Plan).filter_by(active=True).order_by(Plan.id).all(),
        "modes": CONNECTIVITY_MODES,
    }


@router.get("/devices")
def devices(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(request, "devices.html", rows=device_rows(db, db.query(Device).order_by(Device.id.desc()).all()))


@router.get("/devices/new")
def device_new(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(request, "device_new.html", **device_form_context(db))


@router.post("/devices")
async def device_create(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    client_id = optional_id(form, "client_id")
    plan_id = optional_id(form, "plan_id")
    if client_id:
        get_or_404(db, Client, client_id)
    plan = get_or_404(db, Plan, plan_id) if plan_id else None
    mode = choice(form, "connectivity_mode", CONNECTIVITY_MODES) if form.get("connectivity_mode") else None
    mode = mode or (plan.connectivity_mode if plan else "standard")
    device, secret, claim_code = create_device(db, text(form, "label"), client_id, plan_id, mode)
    audit(db, admin.email, "device.created", device.device_id, client_ip(request), mode=mode)
    db.commit()
    # Secrets are shown exactly once, on this response; the claim code is only stored hashed.
    return render(request, "device_provisioned.html", regenerated=False, **provisioning_bundle(device, secret, claim_code))


def find_device(db, device_id):
    device = db.query(Device).filter_by(device_id=device_id).first()
    if not device:
        raise HTTPException(404, "device not found")
    return device


@router.get("/devices/{device_id}")
def device_detail(device_id: str, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    device = find_device(db, device_id)
    heartbeats = db.query(Heartbeat).filter_by(device_id=device.device_id).order_by(Heartbeat.id.desc()).limit(20).all()
    contracts = db.query(Contract).filter_by(device_id=device.id).order_by(Contract.end_date.desc()).all()
    status, contract = license_status(db, device)
    history = (
        db.query(AuditLog).filter_by(target=device.device_id).order_by(AuditLog.id.desc()).limit(20).all()
    )
    return render(
        request,
        "device_detail.html",
        device=device,
        online=is_online(device),
        heartbeats=heartbeats,
        contracts=contracts,
        license=status,
        contract=contract,
        history=history,
        **device_form_context(db),
    )


@router.post("/devices/{device_id}")
async def device_update(device_id: str, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    device = find_device(db, device_id)
    client_id = optional_id(form, "client_id")
    plan_id = optional_id(form, "plan_id")
    if client_id:
        get_or_404(db, Client, client_id)
    if plan_id:
        get_or_404(db, Plan, plan_id)
    before = {"client_id": device.client_id, "plan_id": device.plan_id, "mode": device.connectivity_mode}
    device.label = text(form, "label") or None
    device.client_id = client_id
    device.plan_id = plan_id
    device.connectivity_mode = choice(form, "connectivity_mode", CONNECTIVITY_MODES)
    after = {"client_id": device.client_id, "plan_id": device.plan_id, "mode": device.connectivity_mode}
    audit(db, admin.email, "device.updated", device.device_id, client_ip(request), before=before, after=after)
    db.commit()
    return redirect(f"/devices/{device.device_id}", "Device saved.")


STATUS_ACTIONS = {
    "suspend": ("suspended", ("provisioned", "claimed")),
    "reactivate": (None, ("suspended",)),
    "revoke": ("revoked", ("provisioned", "claimed", "suspended")),
}


@router.post("/devices/{device_id}/status")
async def device_status(device_id: str, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    device = find_device(db, device_id)
    action = choice(form, "action", tuple(STATUS_ACTIONS))
    target, allowed_from = STATUS_ACTIONS[action]
    if device.status not in allowed_from:
        raise HTTPException(409, f"cannot {action} a {device.status} device")
    if action == "revoke" and text(form, "confirm_id") != device.device_id:
        return redirect(f"/devices/{device.device_id}", "Revoke cancelled: type the device ID exactly to confirm.")
    before = device.status
    device.status = target or ("claimed" if device.claimed_at else "provisioned")
    audit(db, admin.email, f"device.{action}", device.device_id, client_ip(request), before=before, after=device.status)
    db.commit()
    return redirect(f"/devices/{device.device_id}", f"Device status: {device.status}.")


@router.post("/devices/{device_id}/regenerate-claim")
async def device_regenerate_claim(device_id: str, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    await check_csrf(request)
    device = find_device(db, device_id)
    if device.status == "revoked":
        raise HTTPException(409, "device revoked")
    claim_code = new_claim_code()
    device.claim_code_hash = sha256_hex(claim_code)
    device.qr_version = (device.qr_version or 1) + 1
    was_claimed = device.claimed_at is not None
    device.claimed_at = None
    if device.status == "claimed":
        device.status = "provisioned"
    audit(
        db, admin.email, "device.claim_regenerated", device.device_id, client_ip(request),
        qr_version=device.qr_version, was_claimed=was_claimed,
    )
    db.commit()
    return render(
        request, "device_provisioned.html", regenerated=True, was_claimed=was_claimed,
        **provisioning_bundle(device, None, claim_code),
    )


# ---------------------------------------------------------------- contracts


@router.get("/contracts")
def contracts(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    rows = db.query(Contract).order_by(Contract.end_date.desc()).all()
    return render(request, "contracts.html", contracts=rows, today=date.today())


def contract_form_context(db):
    return {
        "clients": db.query(Client).order_by(Client.name).all(),
        "devices": db.query(Device).filter(Device.status != "revoked").order_by(Device.device_id).all(),
        "plans": db.query(Plan).order_by(Plan.id).all(),
        "statuses": CONTRACT_STATUSES,
    }


@router.get("/contracts/new")
def contract_new(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    preset = {k: request.query_params.get(k) for k in ("client_id", "device_id")}
    return render(request, "contract_form.html", contract=None, preset=preset, **contract_form_context(db))


@router.get("/contracts/{contract_id}")
def contract_edit(contract_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    return render(
        request, "contract_form.html", contract=get_or_404(db, Contract, contract_id), preset={},
        **contract_form_context(db),
    )


def apply_contract_form(db, contract, form):
    contract.client_id = get_or_404(db, Client, optional_id(form, "client_id") or 0).id
    device = get_or_404(db, Device, optional_id(form, "device_id") or 0)
    contract.device_id = device.id
    contract.plan_id = get_or_404(db, Plan, optional_id(form, "plan_id") or 0).id
    contract.start_date = day(form, "start_date")
    contract.end_date = day(form, "end_date")
    if contract.end_date < contract.start_date:
        raise HTTPException(400, "end_date must be after start_date")
    contract.price_agreed = integer(form, "price_agreed")
    contract.status = choice(form, "status", CONTRACT_STATUSES)
    return device


@router.post("/contracts")
async def contract_create(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    contract = Contract()
    device = apply_contract_form(db, contract, form)
    db.add(contract)
    # The contract decides the device's owner and plan from now on.
    device.client_id = contract.client_id
    device.plan_id = contract.plan_id
    db.flush()
    audit(
        db, admin.email, "contract.created", device.device_id, client_ip(request),
        contract_id=contract.id, start=str(contract.start_date), end=str(contract.end_date),
    )
    db.commit()
    return redirect("/contracts", "Contract created.")


@router.post("/contracts/{contract_id}")
async def contract_update(contract_id: int, request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    form = await check_csrf(request)
    contract = get_or_404(db, Contract, contract_id)
    device = apply_contract_form(db, contract, form)
    if current_contract(db, device) is contract or contract.status == "active":
        device.client_id = contract.client_id
        device.plan_id = contract.plan_id
    audit(
        db, admin.email, "contract.updated", device.device_id, client_ip(request),
        contract_id=contract.id, status=contract.status, end=str(contract.end_date),
    )
    db.commit()
    return redirect("/contracts", "Contract saved.")


# ---------------------------------------------------------------- audit


@router.get("/audit")
def audit_log(request: Request, admin=Depends(current_admin), db=Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(300).all()
    return render(request, "audit.html", rows=rows)
