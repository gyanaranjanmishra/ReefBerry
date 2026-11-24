from reefberry.services.icons import BUILTIN_DIR, IconLibrary

from conftest import create_account, csrf_of

ROLE_ICONS = ["ato", "doser", "fan", "heater", "light", "other", "reactor", "return", "skimmer", "wavemaker"]


def test_every_role_has_a_builtin_icon():
    from reefberry.services.kasa_service import ROLES

    lib = IconLibrary()
    for role in ROLES:
        assert lib.path_for(role) is not None, role
        assert lib.svg(role).startswith("<svg")


def test_override_folder_wins_and_adds_new_names(tmp_path):
    (tmp_path / "skimmer.svg").write_text('<?xml version="1.0"?>\n<svg id="mine"></svg>', encoding="utf-8")
    (tmp_path / "kalk-stirrer.svg").write_text("<svg id='kalk'></svg>", encoding="utf-8")
    lib = IconLibrary(tmp_path)
    assert lib.svg("skimmer") == '<svg id="mine"></svg>'  # XML prolog stripped for inlining
    assert lib.path_for("return").parent == BUILTIN_DIR
    assert "kalk-stirrer" in lib.names()
    assert set(ROLE_ICONS) <= set(lib.names())


def test_bad_names_and_scripts_are_refused(tmp_path):
    (tmp_path / "evil.svg").write_text('<svg onload="alert(1)"></svg>', encoding="utf-8")
    lib = IconLibrary(tmp_path)
    assert lib.path_for("../secret") is None
    assert lib.path_for("Skimmer") is None
    assert lib.svg("evil") == ""
    # Unknown names fall back to the generic icon.
    assert lib.svg("does-not-exist") == lib.svg("other")


def _finish_setup(client):
    create_account(client)
    page = client.get("/setup/tank")
    client.post("/setup/tank", data={"csrf": csrf_of(page.text), "tank_name": "Reef", "tank_volume_l": "250"})
    page = client.get("/setup/devices")
    token = csrf_of(page.text)
    client.post("/devices/scan", data={"csrf": token, "back": "/setup/devices"})
    return token


def test_outlet_icon_choice_is_saved_and_shown(client):
    token = _finish_setup(client)
    page = client.get("/setup/devices")
    assert 'name="icon__STRIP1-1"' in page.text
    client.post(
        "/devices/save",
        data={
            "csrf": token, "back": "/setup/devices",
            "name__STRIP1-1": "Wave L", "role__STRIP1-1": "wavemaker", "icon__STRIP1-1": "fan",
            "name__STRIP1-2": "Return", "role__STRIP1-2": "return", "icon__STRIP1-2": "../../etc/passwd",
        },
    )
    rows = {r["id"]: r["icon"] for r in client.app.state.db.query("SELECT id, icon FROM outlets")}
    assert rows["STRIP1-1"] == "fan"
    assert rows["STRIP1-2"] is None  # invalid choice falls back to "same as type"

    page = client.get("/setup/alerts")
    client.post("/setup/finish", data={"csrf": csrf_of(page.text)})
    dash = client.get("/").text
    assert dash.count('class="outlet-art"><svg') == 4  # every visible outlet (3 on the strip + the plant plug) gets an inline drawing
    assert "a-spin-fast" in dash  # Wave L now uses the fan drawing
    assert "a-blade" not in dash  # ...so the wavemaker drawing is not on the page
