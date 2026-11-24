"""A KP303 on newer (KLAP) firmware is discovered as a single plug; it must still show 3 outlets."""

import asyncio

from reefberry.db import Database
from reefberry.services import kasa_service
from reefberry.services.devices import save_found_devices
from reefberry.services.kasa_service import FoundDevice, FoundOutlet, KasaService


class Child:
    def __init__(self, i):
        self.device_id = f"STRIPID0{i}"
        self.alias = ["Phosban", "Wavemaker-Right", "Kamoer"][i]
        self.is_on = True


class FakeDev:
    def __init__(self, children):
        self.children = children
        self.sys_info = {"children": [{"id": "x"}] * 3}
        self.model = "KP303"
        self.alias = "TP-LINK_Power Strip_C701"
        self.mac = "AA"
        self.device_id = "STRIPID"
        self.hw_info = {"sw_ver": "1.0.21"}
        self.config = object()
        self.closed = False

    async def update(self):
        pass

    async def disconnect(self):
        self.closed = True


def test_strip_seen_as_plug_is_reopened_as_strip(monkeypatch):
    as_plug = FakeDev(children=[])
    as_strip = FakeDev(children=[Child(0), Child(1), Child(2)])
    calls = []

    async def fake_connect(config):
        calls.append(config)
        return as_strip

    monkeypatch.setattr(kasa_service.Device, "connect", staticmethod(fake_connect))
    found = asyncio.run(KasaService(lambda: None)._describe("192.168.1.49", as_plug))
    assert calls == [as_plug.config]
    assert as_plug.closed
    assert [o.alias for o in found.outlets] == ["Phosban", "Wavemaker-Right", "Kamoer"]
    assert found.error is None


def test_real_single_plug_is_left_alone(monkeypatch):
    plug = FakeDev(children=[])
    plug.sys_info = {"alias": "Planted"}

    async def boom(config):
        raise AssertionError("should not reconnect a real single plug")

    monkeypatch.setattr(kasa_service.Device, "connect", staticmethod(boom))
    found = asyncio.run(KasaService(lambda: None)._describe("192.168.1.16", plug))
    assert len(found.outlets) == 1


def test_rescan_replaces_the_wrong_single_outlet(tmp_path):
    db = Database(tmp_path / "t.db")
    save_found_devices(db, [FoundDevice(id="STRIPID", host="h", outlets=[FoundOutlet("STRIPID", 0, "TP-LINK_Power Strip_C701", True)])])
    save_found_devices(db, [FoundDevice(id="STRIPID", host="h", outlets=[
        FoundOutlet(f"STRIPID0{i}", i, n, True) for i, n in enumerate(["Phosban", "Wavemaker-Right", "Kamoer"])
    ])])
    rows = db.query("SELECT display_name, role FROM outlets ORDER BY idx")
    assert [(r["display_name"], r["role"]) for r in rows] == [
        ("Phosban", "reactor"), ("Wavemaker-Right", "wavemaker"), ("Kamoer", "doser"),
    ]
    # A scan that couldn't read the outlets (e.g. login missing) must not delete them.
    save_found_devices(db, [FoundDevice(id="STRIPID", host="h", needs_auth=True)])
    assert len(db.query("SELECT id FROM outlets")) == 3
