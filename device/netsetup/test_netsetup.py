"""netsetup tests with a fake nmcli/ip (no NetworkManager needed).

    pip install pytest && pytest -q device/netsetup
"""

import os
import sys
import urllib.parse
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import netsetup  # noqa: E402


def test_split_terse_handles_escaped_colons():
    assert netsetup.split_terse(r"Cafe\: 2nd floor:82:WPA2") == ["Cafe: 2nd floor", "82", "WPA2"]
    assert netsetup.split_terse(r"a\\b:1") == ["a\\b", "1"]


def test_scan_dedupes_and_sorts(monkeypatch):
    out = "Office:40:WPA2\nOffice:71:WPA2\n:90:WPA2\nGuest:55:--\nLobby:60:\n"
    monkeypatch.setattr(netsetup, "nmcli", lambda *a, **k: (0, out))
    nets = netsetup.scan("wlan0")
    assert [n["ssid"] for n in nets] == ["Office", "Lobby", "Guest"]  # hidden SSID dropped
    assert nets[0]["signal"] == 71 and nets[0]["secure"] and not nets[2]["secure"]


def test_online_ignores_tailscale_and_docker(monkeypatch):
    monkeypatch.setattr(netsetup, "run", lambda args, timeout=30: (0, "default dev tailscale0 scope link\n"))
    assert not netsetup.online()
    monkeypatch.setattr(netsetup, "run", lambda args, timeout=30: (0, "default via 10.0.0.1 dev wlan0 proto dhcp\n"))
    assert netsetup.online()


def test_validate():
    assert netsetup.validate("Arsya", "", secure=True) == "Arsya needs a password."
    assert netsetup.validate("Guest", "", secure=False) == ""
    assert netsetup.validate("", "") and netsetup.validate("x" * 33, "")
    assert netsetup.validate("Office", "short")
    assert netsetup.validate("Office", "") == "" and netsetup.validate("Office", "longenough") == ""


class FakeNM:
    """Records nmcli calls; `connection up <our profile>` succeeds only with the right password."""

    def __init__(self, good_password):
        self.good, self.calls, self.profiles, self.is_online = good_password, [], {}, False

    def nmcli(self, *args, timeout=30):
        self.calls.append(args)
        if args[:2] == ("connection", "add"):
            name = args[args.index("con-name") + 1]
            psk = args[args.index("wifi-sec.psk") + 1] if "wifi-sec.psk" in args else ""
            self.profiles[name] = psk
            return 0, ""
        if args[:2] == ("connection", "up"):
            if self.profiles.get(args[2]) == self.good:
                self.is_online = True
                return 0, ""
            return 4, "Error: Connection activation failed: Secrets were required, but not provided."
        if args[:2] == ("connection", "delete"):
            self.profiles.pop(args[2], None)
        return 0, ""


@pytest.fixture
def fake(monkeypatch):
    nm = FakeNM("correct-horse")
    monkeypatch.setattr(netsetup, "nmcli", nm.nmcli)
    monkeypatch.setattr(netsetup, "online", lambda: nm.is_online)
    monkeypatch.setattr(netsetup, "CONNECT_WAIT", 0)
    monkeypatch.setattr(netsetup.time, "sleep", lambda s: None)
    return nm


def test_connect_wrong_password_removes_profile(fake):
    ok, error = netsetup.connect("wlan0", "Office", "wrong-pass")
    assert not ok and "password" in error
    assert fake.profiles == {}  # a broken profile is not kept


def test_connect_success_keeps_profile(fake):
    ok, error = netsetup.connect("wlan0", "Office: 2F", "correct-horse")
    assert ok and error == ""
    assert list(fake.profiles) == ["vipcon-wifi-Office__2F"]


def test_portal_serves_page_everywhere_and_takes_request():
    portal = netsetup.Portal(port=0)
    port = portal.server.server_address[1]
    try:
        portal.networks = [{"ssid": "Office <5G>", "signal": 80, "secure": True}]
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/generate_204").read().decode()
        assert "Connect Vipcon to Wi-Fi" in page and "Office &lt;5G&gt;" in page  # escaped
        data = urllib.parse.urlencode({"ssid": "Office <5G>", "other": "", "password": "correct-horse"}).encode()
        assert "Connecting to" in urllib.request.urlopen(f"http://127.0.0.1:{port}/connect", data).read().decode()
        assert portal.take_request() == ("Office <5G>", "correct-horse")
        assert portal.take_request() is None
        # A secured network without a password is refused on the page, before the hotspot drops.
        data = urllib.parse.urlencode({"ssid": "Office <5G>", "other": "", "password": ""}).encode()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/connect", data)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400 and "needs a password" in e.read().decode()
        assert portal.take_request() is None
    finally:
        portal.close()


def test_state_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(netsetup, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(netsetup, "STATE_FILE", str(tmp_path / "state.json"))
    netsetup.write_state(mode="setup", ap_password="secret12")
    assert oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600"


def test_hotspot_password_is_readable():
    pw = netsetup.hotspot_password()
    assert len(pw) == 10 and not set(pw) & set("0o1li")
