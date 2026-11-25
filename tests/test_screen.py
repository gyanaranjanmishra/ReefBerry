from fastapi.testclient import TestClient

from conftest import create_account, csrf_of
from reefberry.__main__ import main


def _finish_setup(client):
    create_account(client)
    page = client.get("/setup/tank")
    client.post("/setup/tank", data={"csrf": csrf_of(page.text), "tank_name": "Reef", "tank_volume_l": "250"})
    page = client.get("/setup/devices")
    client.post("/devices/scan", data={"csrf": csrf_of(page.text), "back": "/setup/devices"})
    page = client.get("/setup/alerts")
    client.post("/setup/finish", data={"csrf": csrf_of(page.text)})
    client.post("/logout", data={"csrf": csrf_of(client.get("/").text)})


def _pi(client):
    """A browser running on the Pi itself."""
    return TestClient(client.app, client=("127.0.0.1", 50000))


def test_pi_screen_is_off_until_turned_on(client):
    _finish_setup(client)
    r = _pi(client).get("/screen", follow_redirects=False)
    assert r.status_code == 403 and "reefberry screen on" in r.text
    assert _pi(client).get("/api/outlets").status_code == 401


def test_pi_screen_when_on_is_read_only(client, fake_kasa):
    _finish_setup(client)
    client.app.state.db.set_setting("screen_enabled", True)
    pi = _pi(client)
    page = pi.get("/screen")
    assert page.status_code == 200
    assert "readOnly: true" in page.text and 'name="csrf-token"' not in page.text
    assert 'data-action="feed-start"' not in page.text
    data = pi.get("/api/outlets").json()
    assert len(data["outlets"]) >= 3 and data["version"]
    # Nothing can be switched from the screen.
    assert pi.post("/api/outlets/STRIP1-0/switch", json={"on": False}).status_code == 401
    assert pi.post("/api/mode/feed-start").status_code == 401
    assert pi.get("/settings", follow_redirects=False).status_code == 303
    assert fake_kasa.switch_calls == []


def test_other_devices_always_need_to_sign_in(client):
    _finish_setup(client)
    client.app.state.db.set_setting("screen_enabled", True)
    r = client.get("/screen", follow_redirects=False)  # a phone or laptop on the network
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/api/outlets").status_code == 401


def test_screen_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REEFBERRY_DATA_DIR", str(tmp_path))
    assert main(["screen", "on"]) == 0
    assert "on" in capsys.readouterr().out
    assert main(["screen"]) == 0
    assert "Pi screen view is on" in capsys.readouterr().out
    assert main(["screen", "off"]) == 0
    assert "Pi screen view is off" in capsys.readouterr().out
