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
import select
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
FONT_DIR = os.environ.get("KIOSK_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
IDLE_TIMEOUT = int(os.environ.get("KIOSK_IDLE_TIMEOUT", "60"))
FPS = float(os.environ.get("KIOSK_FPS", "6"))
PULSE_FRAMES = 24  # one breath = PULSE_FRAMES / FPS seconds
STATE_POLL = 5
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
        "claim_code": read(os.path.join(ETC, "claim_code")),
        "claimed": claimed,
        "license_status": remote.get("license_status"),
        "client_name": remote.get("client_name"),
        "tagline": (remote.get("tagline") or "").strip()[:40],
        "lan_ip": lan_ip(),
        "logo_path": logo_path,
        "logo_mtime": logo_mtime,
    }


def claim_link(st):
    return f"https://{st['device_id']}.{st['root_domain']}/claim?c={st['claim_code']}"


def local_link(st):
    return f"https://{st['device_id']}.local.{st['root_domain']}"


# ---------------------------------------------------------------- drawing


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
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


def qr_image(text, size):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    modules = qr.modules_count + 2 * qr.border
    qr.box_size = max(1, size // modules)  # whole pixels per module keep it crisp to scan
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    out = Image.new("RGB", (size, size), "white")
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return out


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
            fp_size = int(u * (0.34 if self.landscape else 0.38))
            logo = load_logo(self.state["logo_path"], int(w * 0.62), int(h * (0.22 if self.landscape else 0.18)))
            tagline = self.state.get("tagline") or ""
            tag_font = font(max(10, int(u * 0.045)))
            tag_gap = int(u * 0.03)
            tag_h = tag_gap + tag_font.size if tagline else 0
            gap = int(h * 0.06)
            hint_h = int(u * 0.09)
            total = logo.height + tag_h + gap + fp_size + gap + hint_h
            top = max(6, (h - total) // 2)
            base = Image.new("RGB", (w, h), BG)
            base.paste(logo, ((w - logo.width) // 2, top), logo)
            if tagline:
                spaced_text(ImageDraw.Draw(base), w / 2, top + logo.height + tag_gap, tagline.upper(),
                            tag_font, mix(FG, 0.75), tracking=max(1, int(u * 0.008)), max_width=w * 0.9)
            fp_y = top + logo.height + tag_h + gap
            hint_y = fp_y + fp_size + gap
            inactive = self.state["claimed"] and self.state["license_status"] in INACTIVE
            color = WARN if inactive else ACCENT
            d = ImageDraw.Draw(base)
            hint = "Service inactive" if inactive else "Touch the screen to begin"
            text_center(d, w / 2, hint_y, hint, font(int(min(w, h) * 0.05)), mix(MUTED, 0.9))
            self.cache["saver"] = (base, fingerprint(fp_size, color), color, ((w - fp_size) // 2, fp_y), fp_size)
        return self.cache["saver"]

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

    def _panel(self, title, lines, qr_text=None, foot=None, title_color=FG):
        w, h = self.w, self.h
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        u = min(w, h)  # font sizes follow the short side so portrait fits too
        pad = int(u * 0.05)
        if qr_text and self.landscape:
            qs = min(h - 2 * pad, int(w * 0.5))
            img.paste(qr_image(qr_text, qs), (pad, pad))
            tx, tw, ty = pad * 2 + qs, w - qs - pad * 3, pad
        elif qr_text:
            qs = min(w - 2 * pad, int(h * 0.48))
            img.paste(qr_image(qr_text, qs), ((w - qs) // 2, pad))
            tx, tw, ty = pad, w - 2 * pad, pad * 2 + qs
        else:
            tx, tw, ty = pad, w - 2 * pad, int(h * 0.22)
        tf = fit_text(d, title, tw, int(u * 0.075), bold=True)
        for line in wrap(d, title, tf, tw):
            d.text((tx, ty), line, font=tf, fill=title_color)
            ty += int(tf.size * 1.25)
        ty += int(u * 0.02)
        bf = font(int(u * 0.05))
        for text, color, mono in lines:
            f = font(int(u * 0.042)) if mono else bf
            for line in (wrap_chars(d, text, f, tw) if mono else wrap(d, text, f, tw)):
                d.text((tx, ty), line, font=f, fill=color)
                ty += int(f.size * 1.35)
            ty += int(u * 0.012)
        if foot:
            ff = fit_text(d, foot, tw, int(u * 0.042), min_size=9)
            d.text((tx, h - pad - ff.size), foot, font=ff, fill=MUTED)
        return img

    def detail(self, remaining):
        st = self.state
        foot = f"Tap to go back · {remaining}s"
        if not st["device_id"] or not st["root_domain"]:
            return self._panel("Device not ready", [("This device has not been provisioned yet. Please contact your Vipcon administrator.", MUTED, False)], foot=foot)
        if st["claimed"] and st["license_status"] in INACTIVE:
            return self._panel(
                "Service inactive",
                [("This device's licence is inactive. New meetings cannot be started.", FG, False),
                 ("Please contact your Vipcon administrator.", MUTED, False),
                 (st["device_id"], MUTED, True)],
                foot=foot, title_color=WARN,
            )
        if not st["claimed"]:
            if not st["claim_code"]:
                return self._panel("No activation code", [("Ask your Vipcon administrator to issue a new activation code.", MUTED, False)], foot=foot)
            return self._panel(
                "Activate Vipcon",
                [("Scan with your phone to register the Client Admin.", FG, False),
                 (st["device_id"], MUTED, True)],
                qr_text=claim_link(st), foot=foot,
            )
        lines = []
        if st["client_name"]:
            lines.append((st["client_name"], ACCENT, False))
        lines.append(("Scan to open Vipcon on this network.", FG, False))
        lines.append((f"{st['device_id']}.local.{st['root_domain']}", MUTED, True))
        if st["lan_ip"]:
            lines.append((f"https://{st['lan_ip']}", MUTED, True))
        return self._panel("Vipcon is ready", lines, qr_text=local_link(st), foot=foot)


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

    mode, mode_since, last_poll = "saver", time.time(), 0
    frames, frames_key, band = [], None, (0, fb.height)
    detail_shown = None
    i = 0
    try:
        while True:
            if time.time() - last_poll >= STATE_POLL:
                renderer.set_state(load_state())
                last_poll = time.time()
            if mode == "saver":
                key = renderer.cache.get("key")
                if key != frames_key:
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
        take_console(False)


def preview(out_dir, width, height):
    os.makedirs(out_dir, exist_ok=True)
    base = {
        "device_id": "dev_3f9a1c2b7d4e", "root_domain": "xrostr.com", "claim_code": "Zk3f-example-claim-code",
        "claimed": False, "license_status": None, "client_name": None, "lan_ip": "192.168.1.23",
        "tagline": os.environ.get("KIOSK_PREVIEW_TAGLINE", ""),
        "logo_path": os.environ.get("KIOSK_PREVIEW_LOGO"), "logo_mtime": 0,
    }
    cases = {
        "saver": {}, "unclaimed": {},
        "claimed": {"claimed": True, "license_status": "active", "client_name": "Example Holdings Pte Ltd"},
        "inactive": {"claimed": True, "license_status": "expired"},
    }
    for name, extra in cases.items():
        r = Renderer(width, height)
        r.set_state(dict(base, **extra))
        imgs = [r.screensaver(0.5)] if name == "saver" else [r.detail(42)]
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
