import pytest
from conftest import create_account, csrf_of, sign_in

from reefberry.services.kasa_service import guess_role
from reefberry.users import UserError, add_user, set_password


def test_fresh_install_cannot_be_claimed_from_the_web(client):
    for path in ["/", "/settings", "/login", "/setup", "/setup/tank"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/setup/no-account"
    page = client.get("/setup/no-account")
    assert "sudo reefberry user add" in page.text
    assert "<form" not in page.text  # no way to create an account here
    # The old web sign-up endpoint is gone.
    assert client.post("/setup/account", data={}).status_code in (404, 405)


def test_add_user_validates(client):
    db = client.app.state.db
    with pytest.raises(UserError):
        add_user(db, "x", "long-enough-pw")
    with pytest.raises(UserError):
        add_user(db, "reefer", "short")
    add_user(db, "reefer", "coral-reef-1")
    with pytest.raises(UserError):
        add_user(db, "Reefer", "coral-reef-2")  # usernames are case-insensitive


def test_first_sign_in_goes_to_setup(client):
    r = create_account(client)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/setup/tank"
    # Once an account exists, the no-account page just points to sign-in.
    assert client.get("/setup/no-account", follow_redirects=False).headers["location"] == "/login"


def test_reset_password_signs_out_everywhere(client):
    create_account(client)
    assert client.get("/setup/tank").status_code == 200
    set_password(client.app.state.db, "reefer", "new-password-9")  # what `reefberry user reset-password` does
    r = client.get("/setup/tank", follow_redirects=False)
    assert r.headers["location"].startswith("/login")
    assert sign_in(client, password="coral-reef-1").status_code == 401
    assert sign_in(client, password="new-password-9").status_code == 303


def test_forms_without_csrf_are_rejected(client):
    create_account(client)
    r = client.post("/setup/tank", data={"tank_name": "x", "tank_volume_l": "100"})
    assert r.status_code == 400
    assert "expired" in r.text


def test_full_wizard_then_dashboard(client, fake_kasa):
    create_account(client)

    page = client.get("/setup/tank")
    r = client.post("/setup/tank", data={"csrf": csrf_of(page.text), "tank_name": "Reef 250", "tank_volume_l": "abc"})
    assert r.status_code == 400
    r = client.post(
        "/setup/tank", data={"csrf": csrf_of(page.text), "tank_name": "Reef 250", "tank_volume_l": "250"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/setup/devices"

    page = client.get("/setup/devices")
    token = csrf_of(page.text)
    r = client.post("/devices/scan", data={"csrf": token, "back": "/setup/devices", "hosts": "192.168.2.49, nope"})
    assert fake_kasa.discover_calls == [["192.168.2.49"]]
    assert "Skipped addresses" in r.text
    assert "Return-DCP5000" in r.text
    assert "need your TP-Link account login" in r.text

    # Roles are guessed from the Kasa names.
    assert 'value="return" selected' in r.text
    assert 'value="wavemaker" selected' in r.text

    # Hide the plant plug, rename one outlet.
    r = client.post(
        "/devices/save",
        data={
            "csrf": token, "back": "/setup/devices", "continue": "1",
            "devhide__PLUG1": "1",
            "name__STRIP1-2": "Return pump", "role__STRIP1-2": "return", "alert__STRIP1-2": "1",
            "name__STRIP1-0": "Light R", "role__STRIP1-0": "light",
            "name__STRIP1-1": "Wave L", "role__STRIP1-1": "wavemaker",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/setup/alerts"

    page = client.get("/setup/alerts")
    r = client.post("/setup/finish", data={"csrf": csrf_of(page.text)}, follow_redirects=False)
    assert r.headers["location"] == "/"

    dash = client.get("/")
    assert dash.status_code == 200
    assert "Reef 250" in dash.text and "250 L system" in dash.text
    assert "Return pump" in dash.text
    assert "Planted" not in dash.text  # hidden device

    states = client.get("/api/outlets").json()["outlets"]
    by_name = {o["name"]: o for o in states}
    assert by_name["Return pump"]["is_on"] is False
    assert by_name["Light R"]["is_on"] is True
    assert "Planted" not in by_name


def test_rescan_keeps_user_names(client, fake_kasa):
    create_account(client)
    token = csrf_of(client.get("/setup/devices").text)
    client.post("/devices/scan", data={"csrf": token, "back": "/setup/devices"})
    client.post("/devices/save", data={"csrf": token, "back": "/setup/devices", "name__STRIP1-2": "My return"})
    fake_kasa.found[0].outlets[2].alias = "Renamed in Kasa"
    r = client.post("/devices/scan", data={"csrf": token, "back": "/setup/devices"})
    assert 'value="My return"' in r.text
    assert "Kasa: Renamed in Kasa" in r.text


def test_login_logout_and_throttle(client):
    create_account(client)
    page = client.get("/setup/tank")
    client.post("/logout", data={"csrf": csrf_of(page.text)})
    assert client.get("/setup/tank", follow_redirects=False).headers["location"].startswith("/login")

    login = client.get("/login")
    token = csrf_of(login.text)
    for _ in range(5):
        r = client.post("/login", data={"csrf": token, "username": "reefer", "password": "wrong-pass"})
        assert r.status_code == 401
    r = client.post("/login", data={"csrf": token, "username": "reefer", "password": "coral-reef-1"})
    assert r.status_code == 429  # locked out even with the right password


def test_login_redirect_stays_on_site(client):
    create_account(client)
    client.post("/logout", data={"csrf": csrf_of(client.get("/setup/tank").text)})
    token = csrf_of(client.get("/login").text)
    r = client.post(
        "/login", data={"csrf": token, "username": "reefer", "password": "coral-reef-1", "next": "//evil.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_api_requires_sign_in(client):
    assert client.get("/api/outlets").status_code == 401


def test_secrets_are_not_echoed_back(client):
    create_account(client)
    token = csrf_of(client.get("/setup/alerts").text)
    client.post("/alerts/save", data={"csrf": token, "back": "/setup/alerts", "smtp_password": "abcd efgh ijkl mnop"})
    page = client.get("/setup/alerts")
    assert "abcdefghijklmnop" not in page.text
    assert "Saved, leave blank to keep" in page.text


def test_role_guessing():
    assert guess_role("Return-DCP5000") == "return"
    assert guess_role("Phosban") == "reactor"
    assert guess_role("Kamoer X1") == "doser"
    assert guess_role("ATO") == "ato"
    assert guess_role("Light-Left") == "light"
    assert guess_role("Something") == "other"
