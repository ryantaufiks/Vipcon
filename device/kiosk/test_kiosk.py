"""Render every kiosk screen and check the QR codes on it still scan.

    pip install pillow numpy qrcode zxing-cpp pytest && pytest -q device/kiosk
"""

import os
import sys

import pytest
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(__file__))
import kiosk  # noqa: E402

zxingcpp = pytest.importorskip("zxingcpp")

BASE = {
    "device_id": "dev_7edad6a81f89", "root_domain": "xrostr.com",
    "claim_code": "nT0kq8yPZ3xV-2bW9LmR4sJc7hFd1aQe", "claimed": False, "license_status": "active",
    "client_name": "Example Holdings Pte Ltd", "tagline": "Private Edge Conferencing",
    "lan_ip": "192.168.1.23", "logo_path": None, "logo_mtime": None, "live_meetings": 3,
}


def decoded(img):
    return {r.text for r in zxingcpp.read_barcodes(img.convert("L"))}


@pytest.mark.parametrize("size", [(480, 320), (320, 480)])
@pytest.mark.parametrize("claimed", [False, True])
def test_detail_screen_qr_scans(size, claimed):
    st = dict(BASE, claimed=claimed)
    want = kiosk.now_link(st) if claimed else kiosk.claim_link(st)
    r = kiosk.Renderer(*size)
    r.set_state(st)
    screen = r.detail(30)
    assert screen.size == size
    # As captured by a phone: the raw screen, a smaller photo, and a soft-focus one.
    for img in (screen,
                screen.resize((size[0] * 6 // 10, size[1] * 6 // 10), Image.LANCZOS),
                screen.resize((size[0] * 2, size[1] * 2), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))):
        assert want in decoded(img)


def test_site_name_is_used_for_links():
    st = dict(BASE, claimed=True, site_fqdn="securistate.lan.xrostr.com")
    assert kiosk.now_link(st) == "https://securistate.lan.xrostr.com/now"
    assert kiosk.now_link(dict(BASE, site_fqdn="")) == "https://192.168.1.23/now"  # no certificate yet
    assert kiosk.claim_link(dict(BASE, site_fqdn="dev-7edad6a81f89.lan.xrostr.com")) == \
        "https://dev-7edad6a81f89.lan.xrostr.com/claim?c=nT0kq8yPZ3xV-2bW9LmR4sJc7hFd1aQe"
    assert kiosk.claim_link(BASE).startswith("https://192.168.1.23/claim?c=")


def test_every_screen_renders():
    for extra in ({}, {"claimed": True}, {"claimed": True, "license_status": "expired"},
                  {"device_id": ""}, {"claim_code": ""}):
        r = kiosk.Renderer(480, 320)
        r.set_state(dict(BASE, **extra))
        assert r.screensaver(0.3).size == (480, 320)
        assert r.splash("Starting…").size == (480, 320)
        assert r.detail(10).size == (480, 320)


def test_splash_cache_matches_framebuffer_size(tmp_path, monkeypatch):
    monkeypatch.setattr(kiosk, "SPLASH_FILE", str(tmp_path / "splash.raw"))
    fb = object.__new__(kiosk.Framebuffer)
    fb.width, fb.height, fb.bpp, fb.stride = 480, 320, 16, 960
    r = kiosk.Renderer(480, 320)
    r.set_state(dict(BASE, claimed=True))
    kiosk.save_splash(fb, r)
    assert (tmp_path / "splash.raw").stat().st_size == fb.stride * fb.height  # what splash.sh checks


@pytest.mark.parametrize("mode", ["setup", "failed", "connecting", "connected"])
def test_wifi_setup_screens(mode):
    net = {"mode": mode, "ap_ssid": "Vipcon-Setup-7EDA", "ap_password": "k7mq2xr9tp", "target_ssid": "Office",
           "network": "Office", "ip": "192.168.10.42", "error": "Could not join Office: the password looks wrong.",
           "updated_at": __import__("time").time()}
    assert kiosk.netsetup_active(net)
    r = kiosk.Renderer(480, 320)
    r.set_state(dict(BASE, net=net))
    screen = r.network()
    if mode in ("setup", "failed"):
        want = "WIFI:T:WPA;S:Vipcon-Setup-7EDA;P:k7mq2xr9tp;;"
        assert want in decoded(screen)
        assert want in decoded(screen.resize((288, 192), Image.LANCZOS))


def test_wifi_qr_escapes_special_characters():
    assert kiosk.wifi_qr_payload('Cafe;"A"', "p:w,d\\") == 'WIFI:T:WPA;S:Cafe\\;\\"A\\";P:p\\:w\\,d\\\\;;'
    assert not kiosk.netsetup_active({"mode": "online"})
    assert not kiosk.netsetup_active({"mode": "connected", "updated_at": 0})


def test_public_name_is_used_for_the_room_qr_but_not_the_claim():
    st = dict(BASE, claimed=True, site_fqdn="client1.lan.xrostr.com", public_fqdn="client1.xrostr.com",
              room_key=kiosk.room_key("seed"))
    assert kiosk.now_link(st) == f"https://client1.xrostr.com/now?k={kiosk.room_key('seed')}"
    # Claiming stays on the office network.
    assert kiosk.claim_link(st).startswith("https://client1.lan.xrostr.com/claim?c=")
    # Tunnel down (no public name) or no seed yet: the office-network link.
    assert kiosk.now_link(dict(st, public_fqdn="")) == "https://client1.lan.xrostr.com/now"
    assert kiosk.now_link(dict(st, room_key="")) == "https://client1.lan.xrostr.com/now"
    # The key changes every day and needs the seed.
    assert kiosk.room_key("seed", 0) != kiosk.room_key("seed", 86400) and kiosk.room_key("") == ""


def test_room_key_matches_the_device_app():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    import hashlib
    import hmac
    from datetime import date

    expected = hmac.new(b"seed", f"now:{date(1970, 1, 2).isoformat()}".encode(), hashlib.sha256).hexdigest()[:20]
    assert kiosk.room_key("seed", 86400 + 5) == expected


def test_brand_screen_uses_the_client_logo_or_vipcon(tmp_path):
    st = dict(BASE, claimed=True, license_status="active")
    r = kiosk.Renderer(480, 320)
    r.set_state(st)
    assert r.accent == kiosk.GOLD  # no client logo: the VIPCON logo and its gold
    assert r.screensaver(0.5).size == r.splash("Starting…").size == (480, 320)
    red = tmp_path / "logo.png"
    kiosk.Image.new("RGBA", (200, 60), (220, 20, 30, 255)).save(red)
    r = kiosk.Renderer(480, 320)
    r.set_state(dict(st, logo_path=str(red), logo_mtime=1))
    assert r.accent[0] > 180 and r.accent[1] < 80  # the client's own colour
    y0, y1 = r.saver_band()
    a, b = r.screensaver(0.1), r.screensaver(0.6)
    diff = kiosk.np.argwhere(kiosk.np.asarray(a) != kiosk.np.asarray(b))
    assert diff.size and diff[:, 0].min() >= y0 and diff[:, 0].max() < y1  # only the band animates
