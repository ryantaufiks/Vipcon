"""Vipcon LCD kiosk for the appliance's 3.5" touch screen (Waveshare 3.5" (B), 480x320).

The screen is the only thing a person standing next to the Pi can interact with, so it
does exactly one job:

    idle        full-screen screensaver: logo + pulsing fingerprint
    tap         unclaimed device -> QR to register the Client Admin (claim link)
                claimed device   -> QR to the local meeting address + client name
                inactive license -> "contact your administrator"
    60 s idle   back to the screensaver

It draws straight to the framebuffer and reads the touch panel with evdev: no X, no
browser, no shell, nothing to escape to. It never opens a network connection except
http://127.0.0.1:8000/healthz on the device itself; the control plane state comes from
/etc/appliance/kiosk/state.json written by the agent.

    python3 kiosk.py                 run on the device
    python3 kiosk.py --preview DIR   render every screen to PNG files (no hardware needed)
"""

import argparse
import glob
import json
import math
import os
import random
import re
import select
import signal
import socket
import sys
import time
import urllib.request

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ETC = os.environ.get("APPLIANCE_ETC", "/etc/appliance")
KIOSK_DIR = os.path.join(ETC, "kiosk")
HEALTH_URL = os.environ.get("KIOSK_HEALTH_URL", "http://127.0.0.1:8000/healthz")
NOW_URL = os.environ.get("KIOSK_NOW_URL", "http://127.0.0.1:8000/api/v1/meetings/now")
FONT_DIR = os.environ.get("KIOSK_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
IDLE_TIMEOUT = int(os.environ.get("KIOSK_IDLE_TIMEOUT", "60"))
FPS = float(os.environ.get("KIOSK_FPS", "6"))
PULSE_FRAMES = 24  # one breath = PULSE_FRAMES / FPS seconds
STATE_POLL = 5
# Ready-to-blit splash frame, shown by a udev rule the moment the LCD driver registers
# /dev/fb0 on the next boot (see splash.sh), long before Python could start.
SPLASH_FILE = os.environ.get("KIOSK_SPLASH_FILE", "/var/cache/vipcon-kiosk/splash.raw")
NETSETUP_STATE = os.environ.get("KIOSK_NETSETUP_STATE", "/run/vipcon-netsetup/state.json")
CONNECTED_NOTICE = 20  # seconds the "Online" screen stays up after Wi-Fi setup succeeds
FULL_REPAINT = 3  # seconds

BG = (6, 8, 12)
FG = (236, 240, 245)
MUTED = (128, 138, 152)
ACCENT = (255, 0, 0)  # brand red, same as the client logo
WARN = (240, 176, 64)

INACTIVE = ("suspended", "revoked", "expired", "no_contract")


# ---------------------------------------------------------------- state


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def local_claimed():
    """The device app is the source of truth for "has a Client Admin"; None if unreachable."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
            return bool(json.load(r).get("claimed"))
    except Exception:
        return None


def live_meetings():
    """How many meetings are in progress (as listed on /now); None if the device app is down."""
    try:
        with urllib.request.urlopen(NOW_URL, timeout=2) as r:
            return len(json.load(r).get("meetings") or [])
    except Exception:
        return None


def load_netsetup():
    try:
        with open(NETSETUP_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def netsetup_active(net):
    mode = net.get("mode")
    if mode in ("setup", "failed", "connecting"):
        return True
    return mode == "connected" and time.time() - net.get("updated_at", 0) < CONNECTED_NOTICE


def wifi_qr_payload(ssid, password):
    """The standard Wi-Fi QR format phones join from their camera app."""
    esc = lambda v: re.sub(r'([\\;,:"])', r"\\\1", v)  # noqa: E731
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def load_state():
    try:
        remote = json.loads(read(os.path.join(KIOSK_DIR, "state.json"), "{}") or "{}")
    except ValueError:
        remote = {}
    claimed = local_claimed()
    if claimed is None:
        claimed = bool(remote.get("claimed"))
    logo_path = os.path.join(KIOSK_DIR, "logo.png")
    try:
        logo_mtime = os.path.getmtime(logo_path)
    except OSError:
        logo_path, logo_mtime = None, None
    return {
        "device_id": read(os.path.join(ETC, "device_id")),
        "root_domain": read(os.path.join(ETC, "root_domain")),
        "site_fqdn": read(os.path.join(ETC, "site_fqdn")),
        "claim_code": read(os.path.join(ETC, "claim_code")),
        "claimed": claimed,
        "license_status": remote.get("license_status"),
        "client_name": remote.get("client_name"),
        "tagline": (remote.get("tagline") or "").strip()[:40],
        "lan_ip": lan_ip(),
        "live_meetings": live_meetings() if claimed else None,
        "net": load_netsetup(),
        "logo_path": logo_path,
        "logo_mtime": logo_mtime,
    }


def claim_link(st):
    return f"{local_link(st)}/claim?c={st['claim_code']}"


def local_link(st):
    """The device's office-network name (a Site ID, or its automatic dev-… name) once the agent
    has its certificate in place; before that, the LAN IP (works, with a certificate warning)."""
    if st.get("site_fqdn"):
        return f"https://{st['site_fqdn']}"
    return f"https://{st.get('lan_ip') or 'localhost'}"


def now_link(st):
    """The QR on a ready device opens the list of meetings in progress."""
    return local_link(st) + "/now"


# ---------------------------------------------------------------- drawing


def font(size, bold=False, mono=False):
    name = ("DejaVuSansMono" if mono else "DejaVuSans") + ("-Bold" if bold else "") + ".ttf"
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default(size)


def mix(color, amount, base=BG):
    return tuple(int(b + (c - b) * amount) for c, b in zip(color, base))


def fit_text(draw, text, max_width, size, bold=False, min_size=10):
    while size > min_size:
        f = font(size, bold)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 1
    return font(min_size, bold)


def ellipsize(draw, text, f, max_width):
    if draw.textlength(text, font=f) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=f) > max_width:
        text = text[:-1]
    return text + "…"


def wrap(draw, text, f, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=f) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def wrap_chars(draw, text, f, max_width):
    """Break a long token (URL, id) wherever it has to, preferring after a dot."""
    lines = []
    while text:
        n = len(text)
        while n > 1 and draw.textlength(text[:n], font=f) > max_width:
            n -= 1
        cut = text.rfind(".", 0, n) + 1 if n < len(text) else n
        cut = cut if cut > n // 2 else n
        lines.append(text[:cut])
        text = text[cut:]
    return lines


def spaced_text(draw, cx, y, text, f, fill, tracking, max_width):
    """Centred text with letter spacing (falls back to no spacing if it would not fit)."""
    widths = [draw.textlength(ch, font=f) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    if total > max_width:
        tracking, total = 0, sum(widths)
    x = cx - total / 2
    for ch, cw in zip(text, widths):
        draw.text((x, y), ch, font=f, fill=fill)
        x += cw + tracking


def text_center(draw, cx, y, text, f, fill):
    w = draw.textlength(text, font=f)
    draw.text((cx - w / 2, y), text, font=f, fill=fill)


def fingerprint(size, color, ss=4):
    """A stylised fingerprint icon: nested ridges open at the bottom, drawn supersampled."""
    big = size * ss
    img = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(img)
    width = max(2, int(big * 0.034))
    cx, cy = big * 0.5, big * 0.58
    # (half-width, start angle, end angle, breaks) per ridge, inside out; 0 deg = 3 o'clock.
    ridges = [
        (0.06, 195, 345, ()),
        (0.12, 150, 390, (250,)),
        (0.18, 140, 400, (300,)),
        (0.24, 150, 395, (215, 330)),
        (0.30, 165, 380, (270,)),
        (0.36, 185, 355, ()),
    ]
    for rx, start, end, breaks in ridges:
        rx *= big * 1.15
        ry = rx * 1.22
        box = [cx - rx, cy - ry, cx + rx, cy + ry]
        edges = [start] + [b for brk in breaks for b in (brk - 10, brk + 10)] + [end]
        for a, b in zip(edges[::2], edges[1::2]):
            d.arc(box, a, b, fill=255, width=width)
    # Core: one ridge rising into the innermost loop.
    d.line([cx, cy - big * 0.02, cx, cy + big * 0.17], fill=255, width=width)
    mask = img.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), color + (0,))
    out.putalpha(mask)
    return out


def network_backdrop(w, h, color, seed=7, ss=2):
    """Node-and-link mesh on the left and right edges that fades out towards the centre.

    Static (drawn once into the screensaver background), so it costs nothing per frame.
    """
    rnd = random.Random(seed)  # same pattern on every boot
    W, H = w * ss, h * ss
    fade = 0.36 * W  # how far in from each edge the mesh reaches
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def strength(x):
        edge = min(x, W - x)
        return max(0.0, 1 - edge / fade) ** 1.4

    nodes = []
    per_side = max(20, int(h / 7))
    for side in (0, 1):
        for _ in range(per_side):
            inward = (rnd.random() ** 1.5) * fade * 1.05  # denser near the edge
            x = inward if side == 0 else W - inward
            nodes.append((x, rnd.uniform(-0.03, 1.03) * H, rnd.random() < 0.12))

    reach = 0.22 * H
    for i, (x1, y1, _) in enumerate(nodes):
        near = sorted(
            ((math.hypot(x1 - x2, y1 - y2), x2, y2) for j, (x2, y2, _) in enumerate(nodes) if j != i),
            key=lambda t: t[0],
        )[:3]
        for dist, x2, y2 in near:
            if dist > reach:
                continue
            a = int(175 * min(strength(x1), strength(x2)) * (1 - dist / reach * 0.5))
            if a > 3:
                d.line([x1, y1, x2, y2], fill=color + (a,), width=ss)
    for x, y, hub in nodes:
        a = strength(x)
        if a <= 0.02:
            continue
        r = (2.6 if hub else 1.5) * ss
        if hub:  # soft halo around the occasional bigger node
            d.ellipse([x - r * 2.4, y - r * 2.4, x + r * 2.4, y + r * 2.4], fill=color + (int(40 * a),))
        d.ellipse([x - r, y - r, x + r, y + r], fill=color + (int(235 * a),))
    return img.resize((w, h), Image.LANCZOS)


def default_wordmark(width):
    f = font(int(width * 0.16), bold=True)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    w = int(probe.textlength("VIPCON", font=f))
    img = Image.new("RGBA", (w + 4, f.size + 12), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2, 0), "VIPCON", font=f, fill=FG)
    return img


def load_logo(path, max_w, max_h):
    logo = None
    if path:
        try:
            with Image.open(path) as im:
                logo = im.convert("RGBA")
        except Exception:
            logo = None
    if logo is None:
        logo = default_wordmark(max_w)
    logo.thumbnail((max_w, max_h), Image.LANCZOS)
    return for_dark_background(logo)


def for_dark_background(logo):
    """Logos made for white pages often have black text, which vanishes on this black
    screen. Turn near-black opaque pixels into the foreground colour; keep everything else."""
    a = np.asarray(logo, dtype=np.uint8).copy()
    rgb = a[..., :3].astype(np.int32)
    luma = (rgb[..., 0] * 299 + rgb[..., 1] * 587 + rgb[..., 2] * 114) // 1000
    dark = (luma < 60) & (a[..., 3] > 0)
    a[dark, 0], a[dark, 1], a[dark, 2] = FG
    return Image.fromarray(a, "RGBA")


GREEN = (72, 200, 120)


def modern_qr(text, size, dark=(12, 14, 20), eye=None, badge_color=None, ss=4, roundness=0.38, eye_round=1.0, gap=0.08):
    """A QR code with rounded modules, rounded finder "eyes" and an optional centre badge.

    Picks the strongest error correction that still gives >= 5 px modules, and only adds the
    centre badge with level H (30 % recovery) so the code always stays scannable.
    Returns an RGBA image of exactly size x size with a transparent background.
    """
    from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M, ERROR_CORRECT_Q

    for level in (ERROR_CORRECT_H, ERROR_CORRECT_Q, ERROR_CORRECT_M):
        qr = qrcode.QRCode(error_correction=level, border=0)
        qr.add_data(text)
        qr.make(fit=True)
        n = qr.modules_count
        if size // n >= 5 or level == ERROR_CORRECT_M:
            break
    cell = size // n
    badge = badge_color is not None and level == ERROR_CORRECT_H and cell >= 5
    eye = eye or dark
    S = cell * ss
    img = Image.new("RGBA", (n * S, n * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    finders = [(0, 0), (n - 7, 0), (0, n - 7)]

    def in_finder(r, c):
        return any(fr <= r < fr + 7 and fc <= c < fc + 7 for fr, fc in finders)

    # Keep the centre clear for the badge (about 22 % of the width, odd number of cells).
    hole = max(5, int(n * 0.22)) | 1 if badge else 0
    lo = (n - hole) // 2

    def in_hole(r, c):
        return badge and lo <= r < lo + hole and lo <= c < lo + hole

    pad = S * gap
    for r, row in enumerate(qr.modules):
        for c, on in enumerate(row):
            if on and not in_finder(r, c) and not in_hole(r, c):
                d.rounded_rectangle([c * S + pad, r * S + pad, (c + 1) * S - pad, (r + 1) * S - pad],
                                    radius=S * roundness, fill=dark)
    for fr, fc in finders:
        x, y = fc * S, fr * S
        d.rounded_rectangle([x, y, x + 7 * S - 1, y + 7 * S - 1], radius=S * 2.0 * eye_round, fill=dark)
        d.rounded_rectangle([x + S, y + S, x + 6 * S - 1, y + 6 * S - 1], radius=S * 1.4 * eye_round, fill=(255, 255, 255, 255))
        d.rounded_rectangle([x + 2 * S, y + 2 * S, x + 5 * S - 1, y + 5 * S - 1], radius=S * 1.0 * eye_round, fill=eye)
    if badge:
        cx = cy = n * S / 2
        rad = hole * S / 2 - S * 0.3
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(255, 255, 255, 255))
        mark = fingerprint(int(rad * 1.5), badge_color)
        img.paste(mark, (int(cx - mark.width / 2), int(cy - mark.height / 2)), mark)
    img = img.resize((n * cell, n * cell), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return out


def qr_card(text, size, radius, accent):
    """White rounded card holding the QR, with a quiet zone of at least ~2 modules."""
    card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(255, 255, 255, 255))
    inner = int(size * 0.84)
    # Finder eyes carry the accent, but must stay dark enough for scanners to see them.
    eye, k = accent, 1.0
    while 0.299 * eye[0] + 0.587 * eye[1] + 0.114 * eye[2] > 70 and k > 0.2:
        k -= 0.05
        eye = tuple(int(c * k) for c in accent)
    qr = modern_qr(text, inner, eye=eye, badge_color=accent)
    card.paste(qr, ((size - inner) // 2, (size - inner) // 2), qr)
    return card


def pill(draw, x, y, label, dot, f):
    """Status chip: rounded outline, coloured dot, letter-spaced label. Returns its height."""
    text = label.upper()
    track = 1.5
    tw = sum(draw.textlength(ch, font=f) for ch in text) + track * (len(text) - 1)
    h = int(f.size * 1.9)
    r = h / 2
    w = int(tw + h * 1.45)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, outline=mix(dot, 0.55), width=1, fill=mix(dot, 0.12))
    cy = y + h / 2
    draw.ellipse([x + r * 0.75 - 3, cy - 3, x + r * 0.75 + 3, cy + 3], fill=dot)
    tx = x + r * 0.75 + 9
    ty = cy - f.size * 0.62
    for ch in text:
        draw.text((tx, ty), ch, font=f, fill=mix(FG, 0.85))
        tx += draw.textlength(ch, font=f) + track
    return h


class Renderer:
    def __init__(self, width, height):
        self.w, self.h = width, height
        self.landscape = width >= height
        self.state = None
        self.cache = {}

    def set_state(self, st):
        key = (st["logo_path"], st["logo_mtime"], st["license_status"], st["claimed"], st.get("tagline"))
        if self.state is None or key != self.cache.get("key"):
            self.cache = {"key": key}
        self.state = st

    # Screensaver -------------------------------------------------------

    def _saver_layout(self):
        if "saver" not in self.cache:
            w, h = self.w, self.h
            u = min(w, h)
            logo = load_logo(self.state["logo_path"], int(w * 0.62), int(h * (0.22 if self.landscape else 0.18)))
            tagline = self.state.get("tagline") or ""
            tag_font = font(max(10, int(u * 0.045)))
            tag_gap = int(u * 0.035)
            tag_h = tag_gap + tag_font.size if tagline else 0
            inactive = self.state["claimed"] and self.state["license_status"] in INACTIVE
            color = WARN if inactive else ACCENT
            hint = "Service inactive" if inactive else "Touch the screen to begin"
            hint_font = font(int(u * 0.05))

            # Three bands with equal outer margins: brand block at the top, hint at the bottom,
            # fingerprint centred in the space between them (sized so its ripple ring fits).
            base = Image.new("RGB", (w, h), BG)
            d = ImageDraw.Draw(base)
            margin = int(h * 0.09)
            top = margin
            brand_bottom = top + logo.height + tag_h
            hb = d.textbbox((0, 0), hint, font=hint_font)
            hint_y = h - margin - hb[3]  # bottom of the glyphs sits on the bottom margin
            space = hint_y + hb[1] - brand_bottom
            fp_size = min(int(u * (0.38 if self.landscape else 0.42)), int(space / 1.5))
            fp_y = int(brand_bottom + (space - fp_size) / 2)

            mesh = network_backdrop(w, h, mix(WARN if inactive else ACCENT, 0.6))
            base.paste(mesh, (0, 0), mesh)
            base.paste(logo, ((w - logo.width) // 2, top), logo)
            if tagline:
                spaced_text(d, w / 2, top + logo.height + tag_gap, tagline.upper(),
                            tag_font, mix(FG, 0.75), tracking=max(1, int(u * 0.008)), max_width=w * 0.9)
            text_center(d, w / 2, hint_y, hint, hint_font, mix(MUTED, 0.9))
            self.cache["saver"] = (base, fingerprint(fp_size, color), color, ((w - fp_size) // 2, fp_y), fp_size)
        return self.cache["saver"]

    def splash(self, status):
        """Boot / restart screen: mesh, logo and tagline centred, a short status line."""
        w, h = self.w, self.h
        u = min(w, h)
        inactive = self.state["claimed"] and self.state["license_status"] in INACTIVE
        img = Image.new("RGB", (w, h), BG)
        mesh = network_backdrop(w, h, mix(WARN if inactive else ACCENT, 0.6))
        img.paste(mesh, (0, 0), mesh)
        d = ImageDraw.Draw(img)
        logo = load_logo(self.state["logo_path"], int(w * 0.66), int(h * (0.24 if self.landscape else 0.18)))
        tagline = self.state.get("tagline") or ""
        tag_font = font(max(10, int(u * 0.045)))
        tag_gap = int(u * 0.04)
        block = logo.height + (tag_gap + tag_font.size if tagline else 0)
        top = (h - block) // 2 - int(h * 0.03)
        img.paste(logo, ((w - logo.width) // 2, top), logo)
        if tagline:
            spaced_text(d, w / 2, top + logo.height + tag_gap, tagline.upper(), tag_font, mix(FG, 0.75),
                        tracking=max(1, int(u * 0.008)), max_width=w * 0.9)
        sf = font(max(9, int(u * 0.04)))
        sb = d.textbbox((0, 0), status, font=sf)
        text_center(d, w / 2, h - int(h * 0.09) - sb[3], status, sf, mix(MUTED, 0.8))
        return img

    def saver_band(self):
        """Rows of the screen that change between screensaver frames."""
        _, _, _, (x, y), size = self._saver_layout()
        pad = int(size * 0.22)
        return max(0, y - pad), min(self.h, y + size + pad)

    def screensaver(self, phase):
        base, fp, color, (x, y), size = self._saver_layout()
        frame = base.copy()
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * phase)  # 0..1..0
        # Ripple: a ring expanding out of the print and fading away.
        ripple = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ripple)
        cx, cy = x + size / 2, y + size / 2
        r = size * (0.52 + 0.22 * phase)
        alpha = int(110 * (1 - phase))
        rd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (alpha,), width=2)
        frame.paste(ripple, (0, 0), ripple)
        # Soft glow behind the print, brightest at the top of the breath.
        glow = fp.filter(ImageFilter.GaussianBlur(size * 0.06))
        g_alpha = glow.getchannel("A").point(lambda a: int(a * (0.25 + 0.55 * breath)))
        glow.putalpha(g_alpha)
        frame.paste(glow, (x, y), glow)
        body = fp.copy()
        body.putalpha(fp.getchannel("A").point(lambda a: int(a * (0.55 + 0.45 * breath))))
        frame.paste(body, (x, y), body)
        return frame

    # Tapped screens -----------------------------------------------------

    def _detail_background(self):
        if "detail_bg" not in self.cache:
            bg = Image.new("RGB", (self.w, self.h), BG)
            inactive = self.state["claimed"] and self.state["license_status"] in INACTIVE
            mesh = network_backdrop(self.w, self.h, mix(WARN if inactive else ACCENT, 0.3), seed=11)
            bg.paste(mesh, (0, 0), mesh)
            self.cache["detail_bg"] = bg
        return self.cache["detail_bg"].copy()

    def _panel(self, status, status_color, title, body, remaining, qr_text=None, meta=(), title_color=FG):
        """Detail screen: QR card on the left (or top in portrait), text column, countdown bar."""
        w, h = self.w, self.h
        u = min(w, h)
        img = self._detail_background()
        d = ImageDraw.Draw(img)
        pad = int(u * 0.075)
        if qr_text and self.landscape:
            cs = h - 2 * pad
            img.paste(card := qr_card(qr_text, cs, int(cs * 0.07), status_color if status_color != GREEN else ACCENT),
                      (pad, pad), card)
            tx, tw, ty, bottom = pad * 2 + cs - int(pad * 0.3), w - cs - pad * 3 + int(pad * 0.3), pad, h - pad
        elif qr_text:
            cs = min(w - 2 * pad, int(h * 0.5))
            img.paste(card := qr_card(qr_text, cs, int(cs * 0.07), ACCENT), ((w - cs) // 2, pad), card)
            tx, tw, ty, bottom = pad, w - 2 * pad, pad * 2 + cs, h - pad
        else:
            tx, tw, ty, bottom = pad * 2, w - 4 * pad, int(h * 0.2), h - pad

        ty += pill(d, tx, ty, status, status_color, font(max(9, int(u * 0.034)), bold=True)) + int(u * 0.045)
        tf = fit_text(d, title, tw, int(u * 0.078), bold=True, min_size=14)
        for line in wrap(d, title, tf, tw):
            d.text((tx, ty), line, font=tf, fill=title_color)
            ty += int(tf.size * 1.2)
        ty += int(u * 0.025)
        for text, color in body:
            bf = font(int(u * 0.047))
            for line in wrap(d, text, bf, tw):
                d.text((tx, ty), line, font=bf, fill=color)
                ty += int(bf.size * 1.32)
            ty += int(u * 0.012)
        hf = font(max(9, int(u * 0.036)))
        bar_y = bottom - 3
        hint_y = bar_y - int(u * 0.03) - hf.size
        mf = font(int(u * 0.038), mono=True)
        ty += int(u * 0.02)
        for text in meta:
            for line in wrap_chars(d, text, mf, tw):
                if ty + mf.size > hint_y - int(u * 0.02):
                    break  # never run into the footer; meta is the least important text
                d.text((tx, ty), line, font=mf, fill=mix(MUTED, 0.95))
                ty += int(mf.size * 1.3)

        if remaining is None:  # screens that stay until something happens: no countdown
            return img
        # Countdown: a thin bar that empties, with the hint above it.
        d.text((tx, hint_y), "Tap anywhere to go back", font=hf, fill=MUTED)
        d.rounded_rectangle([tx, bar_y, tx + tw, bar_y + 3], radius=2, fill=mix(MUTED, 0.25))
        left = max(0.0, min(1.0, remaining / IDLE_TIMEOUT))
        if left > 0:
            d.rounded_rectangle([tx, bar_y, tx + max(4, int(tw * left)), bar_y + 3], radius=2, fill=status_color)
        return img

    def network(self):
        """Wi-Fi setup screens, driven by device/netsetup (shown instead of everything else)."""
        net = self.state.get("net") or {}
        mode = net.get("mode")
        if mode == "connecting":
            return self._panel("Connecting", ACCENT, "Connecting…",
                               [(f"Joining {net.get('target_ssid', 'the network')}.", FG),
                                ("This can take up to a minute.", MUTED)], None)
        if mode == "connected":
            body = [(f"Connected to {net['network']}." if net.get("network") else "Network connected.", FG)]
            return self._panel("Online", GREEN, "Vipcon is online", body, None,
                               meta=[f"IP {net['ip']}"] if net.get("ip") else ())
        ssid, password = net.get("ap_ssid", ""), net.get("ap_password", "")
        if mode == "failed":
            body = [(net.get("error") or "Could not connect.", WARN),
                    ("Scan to try again.", FG)]
        else:
            body = [("1. Scan to join the setup network.", FG),
                    ("2. Pick your office Wi-Fi on the page that opens.", mix(FG, 0.8))]
        return self._panel("Try again" if mode == "failed" else "Wi-Fi setup", WARN if mode == "failed" else ACCENT,
                           "Connect to Wi-Fi", body, None, qr_text=wifi_qr_payload(ssid, password),
                           meta=[ssid, f"Pass {password}"])

    def detail(self, remaining):
        st = self.state
        if not st["device_id"] or not st["root_domain"]:
            return self._panel("Setup", MUTED, "Device not ready",
                               [("This device has not been provisioned yet.", FG),
                                ("Please contact your Vipcon administrator.", MUTED)], remaining)
        if st["claimed"] and st["license_status"] in INACTIVE:
            return self._panel("Inactive", WARN, "Service inactive",
                               [("This device's licence is inactive. New meetings cannot be started.", FG),
                                ("Please contact your Vipcon administrator.", MUTED)],
                               remaining, meta=[st["device_id"]], title_color=WARN)
        if not st["claimed"]:
            if not st["claim_code"]:
                return self._panel("Activation", ACCENT, "No activation code",
                                   [("Ask your Vipcon administrator to issue a new activation code.", MUTED)], remaining)
            return self._panel("Activation", ACCENT, "Activate Vipcon",
                               [("Scan with your phone to register the Client Admin.", mix(FG, 0.9))],
                               remaining, qr_text=claim_link(st), meta=[st["device_id"]])
        body = []
        if st["client_name"]:
            body.append((st["client_name"], ACCENT))
        live = st.get("live_meetings")
        if live is not None:
            body.append(("No meetings in progress" if live == 0 else
                         f"{live} meeting{'s' if live != 1 else ''} in progress", FG))
        body.append(("Scan to join.", mix(FG, 0.8)))
        meta = [f"https://{st['lan_ip']}/now"] if st["lan_ip"] else []
        return self._panel("Ready", GREEN, "Vipcon is ready", body, remaining, qr_text=now_link(st), meta=meta)


# ---------------------------------------------------------------- hardware


class Framebuffer:
    def __init__(self, path=None):
        self.path = path or self.find()
        name = os.path.basename(self.path)
        sysfs = f"/sys/class/graphics/{name}"
        self.width, self.height = map(int, read(f"{sysfs}/virtual_size").split(","))
        self.bpp = int(read(f"{sysfs}/bits_per_pixel"))
        self.stride = int(read(f"{sysfs}/stride", "0")) or self.width * self.bpp // 8
        if self.bpp not in (16, 32):
            raise SystemExit(f"{self.path}: unsupported {self.bpp} bpp")
        self.fd = os.open(self.path, os.O_RDWR)

    @staticmethod
    def find():
        if os.environ.get("KIOSK_FB"):
            return os.environ["KIOSK_FB"]
        # Prefer the SPI panel (fbtft / tinydrm) over HDMI.
        for fb in sorted(glob.glob("/sys/class/graphics/fb*")):
            name = read(f"{fb}/name").lower()
            if any(k in name for k in ("ili9486", "ili", "fbtft", "mipi-dbi", "waveshare", "panel")):
                return "/dev/" + os.path.basename(fb)
        return "/dev/fb1" if os.path.exists("/dev/fb1") else "/dev/fb0"

    def encode(self, img):
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        if self.bpp == 16:
            px = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
            rows = px.astype("<u2").tobytes()
            bpr = self.width * 2
        else:
            bgrx = np.zeros(a.shape[:2] + (4,), dtype=np.uint8)
            bgrx[..., 0], bgrx[..., 1], bgrx[..., 2] = a[..., 2], a[..., 1], a[..., 0]
            rows = bgrx.tobytes()
            bpr = self.width * 4
        if bpr == self.stride:
            return rows
        pad = b"\0" * (self.stride - bpr)
        return b"".join(rows[i:i + bpr] + pad for i in range(0, len(rows), bpr))

    def show(self, data, y0=0, y1=None):
        """Write rows y0..y1 only: fbtft pushes just the dirty pages over SPI."""
        y1 = self.height if y1 is None else y1
        os.pwrite(self.fd, data[y0 * self.stride:y1 * self.stride], y0 * self.stride)


class Touch:
    """Any touch-down on the panel counts as a tap; no calibration needed."""

    def __init__(self):
        import evdev

        self.evdev = evdev
        self.dev = None
        self.next_scan = 0

    def _scan(self):
        if time.time() < self.next_scan:
            return
        self.next_scan = time.time() + 10
        want = os.environ.get("KIOSK_TOUCH", "")
        for path in self.evdev.list_devices():
            try:
                dev = self.evdev.InputDevice(path)
            except OSError:
                continue
            keys = dev.capabilities().get(self.evdev.ecodes.EV_KEY, [])
            if (want and want in (path, dev.name)) or (
                not want and (self.evdev.ecodes.BTN_TOUCH in keys or "ads7846" in dev.name.lower())
            ):
                self.dev = dev
                print(f"kiosk: touch {path} ({dev.name})", flush=True)
                return
            dev.close()

    def wait(self, timeout):
        """Return True if the screen was touched within timeout seconds."""
        if self.dev is None:
            self._scan()
            if self.dev is None:
                time.sleep(timeout)
                return False
        try:
            ready, _, _ = select.select([self.dev.fd], [], [], timeout)
            if not ready:
                return False
            tapped = False
            for ev in self.dev.read():
                if ev.type == self.evdev.ecodes.EV_KEY and ev.code == self.evdev.ecodes.BTN_TOUCH and ev.value == 1:
                    tapped = True
            return tapped
        except OSError:  # panel went away (driver reload); rescan later
            self.dev = None
            return False


def save_splash(fb, renderer):
    """Cache the encoded splash so the next boot can show it before anything else runs."""
    try:
        data = fb.encode(renderer.splash("Starting…"))
        os.makedirs(os.path.dirname(SPLASH_FILE), exist_ok=True)
        tmp = SPLASH_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, SPLASH_FILE)
    except OSError as e:
        print(f"kiosk: could not save splash: {e}", flush=True)


def take_console(enable):
    """Stop the text console from drawing over the panel while the kiosk runs."""
    try:
        import fcntl

        KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0x00, 0x01
        for path in ("/dev/tty0", "/dev/tty1"):
            with open(path, "w") as tty:
                fcntl.ioctl(tty, KDSETMODE, KD_GRAPHICS if enable else KD_TEXT)
    except OSError as e:
        print(f"kiosk: could not switch console mode: {e}", flush=True)


# ---------------------------------------------------------------- main


def run():
    fb = Framebuffer()
    print(f"kiosk: {fb.path} {fb.width}x{fb.height} {fb.bpp}bpp", flush=True)
    touch = Touch()
    renderer = Renderer(fb.width, fb.height)
    take_console(True)

    # systemd stops us with SIGTERM on reboot/shutdown: leave the branded screen up, not a console.
    def on_term(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_term)

    mode, mode_since, last_poll = "saver", time.time(), 0
    frames, frames_key, band = [], None, (0, fb.height)
    splash_key = None
    net_shown = None
    detail_shown = None
    i = 0
    try:
        while True:
            if time.time() - last_poll >= STATE_POLL:
                renderer.set_state(load_state())
                last_poll = time.time()
            net = renderer.state.get("net") or {}
            if netsetup_active(net):
                # Wi-Fi setup owns the screen; refresh when its state changes.
                net_key = (net.get("mode"), net.get("updated_at"))
                if net_key != net_shown:
                    fb.show(fb.encode(renderer.network()))
                    net_shown = net_key
                touch.wait(0.5)
                last_poll = min(last_poll, time.time() - STATE_POLL + 1)  # poll every second here
                mode, frames_key = "saver", None
                continue
            net_shown = None
            if mode == "saver":
                key = renderer.cache.get("key")
                if key != frames_key:
                    if key != splash_key:
                        save_splash(fb, renderer)
                        splash_key = key
                    frames = [fb.encode(renderer.screensaver(n / PULSE_FRAMES)) for n in range(PULSE_FRAMES)]
                    frames_key, band = key, renderer.saver_band()
                    fb.show(frames[0])  # full repaint once, then only the animated band
                # Mostly push only the animated band; every few seconds repaint everything so
                # anything else that touched the panel (boot messages) is wiped.
                if i % int(FPS * FULL_REPAINT) == 0:
                    fb.show(frames[i % PULSE_FRAMES])
                else:
                    fb.show(frames[i % PULSE_FRAMES], *band)
                i += 1
                if touch.wait(1 / FPS):
                    mode, mode_since, detail_shown = "detail", time.time(), None
                    renderer.set_state(load_state())
                    last_poll = time.time()
            else:
                remaining = max(0, IDLE_TIMEOUT - int(time.time() - mode_since))
                if remaining != detail_shown:
                    fb.show(fb.encode(renderer.detail(remaining)))
                    detail_shown = remaining
                if remaining == 0 or touch.wait(0.5):
                    mode, frames_key = "saver", None
    finally:
        if renderer.state is not None:
            try:
                fb.show(fb.encode(renderer.splash("Please wait…")))
            except Exception:
                pass
        take_console(False)


def preview(out_dir, width, height):
    os.makedirs(out_dir, exist_ok=True)
    base = {
        "device_id": "dev_3f9a1c2b7d4e", "root_domain": "xrostr.com", "claim_code": "Zk3f-example-claim-code",
        "claimed": False, "license_status": None, "client_name": None, "lan_ip": "192.168.1.23",
        "tagline": os.environ.get("KIOSK_PREVIEW_TAGLINE", ""),
        "live_meetings": 2,
        "net": {},
        "logo_path": os.environ.get("KIOSK_PREVIEW_LOGO"), "logo_mtime": 0,
    }
    cases = {
        "wifi-setup": {"net": {"mode": "setup", "ap_ssid": "Vipcon-Setup-7EDA", "ap_password": "k7mq2xr9tp"}},
        "wifi-failed": {"net": {"mode": "failed", "ap_ssid": "Vipcon-Setup-7EDA", "ap_password": "k7mq2xr9tp",
                                "error": "Could not join Office-5G: the password looks wrong."}},
        "wifi-connecting": {"net": {"mode": "connecting", "target_ssid": "Office-5G"}},
        "wifi-online": {"net": {"mode": "connected", "network": "Office-5G", "ip": "192.168.10.42"}},
        "saver": {}, "unclaimed": {},
        "claimed": {"claimed": True, "license_status": "active", "client_name": "Example Holdings Pte Ltd"},
        "inactive": {"claimed": True, "license_status": "expired"},
    }
    for name, extra in cases.items():
        r = Renderer(width, height)
        r.set_state(dict(base, **extra))
        if name.startswith("wifi-"):
            imgs = [r.network()]
        else:
            imgs = [r.screensaver(0.5), r.splash("Starting…")] if name == "saver" else [r.detail(42)]
        if name == "inactive":
            imgs.insert(0, r.screensaver(0.5))
        for n, img in enumerate(imgs):
            path = os.path.join(out_dir, f"{width}x{height}-{name}{'-' + str(n) if len(imgs) > 1 else ''}.png")
            img.save(path)
            print(path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--preview", metavar="DIR")
    p.add_argument("--size", default="480x320")
    args = p.parse_args()
    if args.preview:
        w, h = map(int, args.size.split("x"))
        preview(args.preview, w, h)
    else:
        if os.geteuid() != 0:
            print("kiosk: warning, not root; framebuffer/touch/tty access may fail", file=sys.stderr)
        run()
