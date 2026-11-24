import re

from conftest import create_account, csrf_of


def _ready(client):
    create_account(client)
    page = client.get("/setup/tank")
    client.post("/setup/tank", data={"csrf": csrf_of(page.text), "tank_name": "Reef", "tank_volume_l": "250"})
    page = client.get("/setup/devices")
    client.post("/devices/scan", data={"csrf": csrf_of(page.text), "back": "/setup/devices"})
    page = client.get("/setup/alerts")
    client.post("/setup/finish", data={"csrf": csrf_of(page.text)})
    dash = client.get("/").text
    return re.search(r'name="csrf-token" content="([^"]+)"', dash).group(1)


def test_switch_needs_sign_in_and_csrf(client, fake_kasa):
    r = client.post("/api/outlets/STRIP1-0/switch", json={"on": False})
    assert r.status_code == 401
    token = _ready(client)
    r = client.post("/api/outlets/STRIP1-0/switch", json={"on": False})
    assert r.status_code == 403
    r = client.post("/api/outlets/STRIP1-0/switch", json={"on": False}, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403
    assert fake_kasa.switch_calls == []

    r = client.post("/api/outlets/STRIP1-0/switch", json={"on": False}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert fake_kasa.switch_calls == [("STRIP1-0", False)]
    data = client.get("/api/outlets").json()
    light = next(o for o in data["outlets"] if o["id"] == "STRIP1-0")
    assert light["is_on"] is False
    assert any("turned off: Switched from the dashboard" in e["text"] for e in data["events"])


def test_switch_rejects_bad_body_and_unknown_outlet(client):
    token = _ready(client)
    h = {"X-CSRF-Token": token}
    assert client.post("/api/outlets/STRIP1-0/switch", json={"on": "yes"}, headers=h).status_code == 400
    r = client.post("/api/outlets/NOPE/switch", json={"on": True}, headers=h)
    assert r.status_code == 409 and "Reload" in r.json()["detail"]


def test_modes_over_the_api(client, fake_kasa):
    token = _ready(client)
    h = {"X-CSRF-Token": token}
    assert client.post("/api/mode/feed-start", headers=h).status_code == 200
    assert ("STRIP1-1", False) in fake_kasa.switch_calls  # the wavemaker
    assert client.get("/api/outlets").json()["mode"]["name"] == "feed"
    r = client.post("/api/mode/feed-start", headers=h)
    assert r.status_code == 409
    assert client.post("/api/mode/feed-end", headers=h).status_code == 200
    assert client.post("/api/mode/explode", headers=h).status_code == 404
    assert client.post("/api/mode/water-change-start", headers=h).status_code == 200
    assert client.get("/api/outlets").json()["mode"]["name"] == "water_change"
    assert client.post("/api/mode/water-change-done", headers=h).status_code == 200


def test_automation_settings_are_validated(client):
    _ready(client)
    page = client.get("/settings")
    assert 'id="feed_minutes"' in page.text
    token = csrf_of(page.text)
    good = {"csrf": token, "feed_minutes": "7", "return_restart_delay_min": "10", "wc_wave_delay_min": "1",
            "wc_skimmer_delay_min": "10", "alert_after_min": "60"}
    client.post("/settings/automation", data=good)
    assert client.app.state.db.get_setting("feed_minutes") == 7
    client.post("/settings/automation", data={**good, "feed_minutes": "999"})
    assert client.app.state.db.get_setting("feed_minutes") == 7
    assert "Wavemakers off 7 min" in client.get("/").text
