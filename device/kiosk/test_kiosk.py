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


def test_every_screen_renders():
    for extra in ({}, {"claimed": True}, {"claimed": True, "license_status": "expired"},
                  {"device_id": ""}, {"claim_code": ""}):
        r = kiosk.Renderer(480, 320)
        r.set_state(dict(BASE, **extra))
        assert r.screensaver(0.3).size == (480, 320)
        assert r.detail(10).size == (480, 320)
