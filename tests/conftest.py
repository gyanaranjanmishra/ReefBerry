import re

import pytest
from fastapi.testclient import TestClient

from reefberry.app import create_app
from reefberry.config import Config
from reefberry.services.kasa_service import FoundDevice, FoundOutlet, KasaError
from reefberry.users import add_user


class FakeKasa:
    """Stands in for real plugs. Records calls so tests can check nothing was switched."""

    def __init__(self):
        self.found = [
            FoundDevice(
                id="STRIP1", host="192.168.2.20", model="KP303", alias="Strip",
                conn={"host": "192.168.2.20"},
                outlets=[
                    FoundOutlet("STRIP1-0", 0, "Light-Right", True),
                    FoundOutlet("STRIP1-1", 1, "Wavemaker-Left", True),
                    FoundOutlet("STRIP1-2", 2, "Return-DCP5000", False),
                ],
            ),
            FoundDevice(id="PLUG1", host="192.168.2.16", model="HS103", alias="Planted",
                        outlets=[FoundOutlet("PLUG1", 0, "Planted", True)]),
            FoundDevice(id="host:192.168.2.49", host="192.168.2.49", needs_auth=True,
                        error="This device needs your TP-Link account login."),
        ]
        self.discover_calls = []
        self.switch_calls = []
        self.offline: set[str] = set()
        self.states = {o.id: o.is_on for d in self.found for o in d.outlets}

    simulated = False

    async def discover(self, extra_hosts=None):
        self.discover_calls.append(list(extra_hosts or []))
        return self.found

    async def read_states(self, devices):
        out = {}
        for d in devices:
            src = next((f for f in self.found if f.id == d["id"]), None)
            online = bool(src and not src.needs_auth and d["id"] not in self.offline)
            out[d["id"]] = {
                "online": online,
                "outlets": {o.id: self.states[o.id] for o in (src.outlets if src and online else [])},
                "error": None if online else "TimeoutError",
            }
        return out

    async def set_outlet(self, row, outlet_id, on):
        if row["id"] in self.offline:
            raise KasaError("Can't reach the strip (TimeoutError).")
        self.switch_calls.append((outlet_id, on))
        self.states[outlet_id] = on

    async def close(self):
        pass


@pytest.fixture
def fake_kasa():
    return FakeKasa()


@pytest.fixture
def client(tmp_path, fake_kasa):
    cfg = Config(data_dir=tmp_path / "data", host="127.0.0.1", port=0)
    app = create_app(cfg, kasa=fake_kasa, run_controller=False)
    with TestClient(app) as c:
        yield c


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no CSRF token on page"
    return m.group(1)


def create_account(client, username="reefer", password="coral-reef-1"):
    """Create an account the way the Pi does (command line), then sign in through the web."""
    add_user(client.app.state.db, username, password)
    return sign_in(client, username, password)


def sign_in(client, username="reefer", password="coral-reef-1"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"csrf": csrf_of(page.text), "username": username, "password": password, "next": "/setup"},
        follow_redirects=False,
    )
