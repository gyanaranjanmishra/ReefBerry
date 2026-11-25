from datetime import datetime, timedelta

import pytest

from conftest import create_account, csrf_of
from reefberry.services import params as P
from reefberry.services.charts import line_chart


def _ready(client):
    create_account(client)
    db = client.app.state.db
    db.set_settings({"setup_complete": True, "tank_name": "Reef", "tank_volume_l": 250})
    return db


def _when(days_ago=0):
    return (P.now_local() - timedelta(days=days_ago)).strftime(P.TIME_FORMAT)


def test_default_parameters(client):
    db = _ready(client)
    keys = [p.key for p in P.list_params(db)]
    assert keys == ["alk", "ca", "mg", "no3", "po4"]
    alk = P.list_params(db)[0]
    assert (alk.unit, alk.low, alk.high) == ("dKH", 7.5, 9.0)


def test_add_test_and_latest(client):
    db = _ready(client)
    P.add_test(db, _when(3), {"alk": 8.0, "ca": 420, "mg": None})
    P.add_test(db, _when(0), {"alk": 7.1, "po4": 0.2}, "new kit")
    latest = {i["param"].key: i for i in P.latest(db)}
    assert latest["alk"]["value"] == 7.1 and latest["alk"]["status"] == "low"
    assert latest["alk"]["change"] == pytest.approx(-0.9)
    assert latest["po4"]["status"] == "high" and latest["po4"]["text"] == "0.20"
    assert latest["ca"]["status"] == "ok" and latest["ca"]["age"] == "3 days ago"
    assert latest["mg"]["value"] is None and latest["mg"]["age"] == "never tested"
    assert [len(t["values"]) for t in P.recent_tests(db)] == [2, 2]


def test_typos_and_empty_tests_are_refused(client):
    db = _ready(client)
    with pytest.raises(P.ParamError, match="typo"):
        P.add_test(db, _when(), {"ca": 4200})
    with pytest.raises(P.ParamError, match="at least one"):
        P.add_test(db, _when(), {"ca": None})
    with pytest.raises(P.ParamError):
        P.parse_number("eight")
    assert P.parse_number(" 8,4 ") == 8.4
    with pytest.raises(P.ParamError, match="future"):
        P.parse_time((P.now_local() + timedelta(days=2)).strftime(P.TIME_FORMAT))
    assert db.one("SELECT COUNT(*) n FROM tests")["n"] == 0


def test_parameters_page_flow(client):
    _ready(client)
    page = client.get("/parameters")
    assert page.status_code == 200 and "No tests yet" in page.text
    r = client.post("/parameters", data={
        "csrf": csrf_of(page.text), "taken_at": _when(1), "alk": "8,2", "no3": "5", "note": "weekly",
    }, follow_redirects=True)
    assert r.status_code == 200 and "Saved Alkalinity, Nitrate" in r.text
    assert '<svg class="chart"' in r.text and "weekly" in r.text
    # A bad value re-shows the form with what was typed.
    r = client.post("/parameters", data={"csrf": csrf_of(r.text), "taken_at": _when(), "ca": "4500"})
    assert r.status_code == 400 and "looks like a typo" in r.text and 'value="4500"' in r.text
    # Ranges
    assert client.get("/parameters?range=30").status_code == 200
    assert client.get("/parameters?range=nonsense").status_code == 200
    # Dashboard shows the latest results.
    dash = client.get("/")
    assert "8.2" in dash.text and "Add a test" in dash.text


def test_delete_test(client):
    db = _ready(client)
    test_id = P.add_test(db, _when(), {"alk": 8.0})
    page = client.get("/parameters")
    client.post(f"/parameters/{test_id}/delete", data={"csrf": csrf_of(page.text)})
    assert db.one("SELECT COUNT(*) n FROM readings")["n"] == 0
    r = client.post(f"/parameters/{test_id}/delete", data={"csrf": csrf_of(page.text)}, follow_redirects=True)
    assert "already gone" in r.text


def test_green_bands_in_settings(client):
    db = _ready(client)
    page = client.get("/settings")
    assert "Green bands" in page.text
    data = {"csrf": csrf_of(page.text)}
    for p in P.list_params(db):
        data[f"{p.key}_low"] = P.Param.fmt(p, p.low)
        data[f"{p.key}_high"] = P.Param.fmt(p, p.high)
    data.update({"alk_low": "8", "alk_high": "9.5", "no3_low": ""})
    client.post("/settings/parameters", data=data)
    params = {p.key: p for p in P.list_params(db)}
    assert (params["alk"].low, params["alk"].high) == (8, 9.5)
    assert params["no3"].low is None and params["no3"].band_text == "Up to 10.0 ppm"
    # Low above high: nothing changes.
    data.update({"ca_low": "460", "ca_high": "450", "alk_low": "7"})
    r = client.post("/settings/parameters", data=data, follow_redirects=True)
    assert "lower limit" in r.text
    assert P.list_params(db)[0].low == 8


def test_pages_need_sign_in(client):
    _ready(client)
    client.post("/logout", data={"csrf": csrf_of(client.get("/").text)})
    assert client.get("/parameters", follow_redirects=False).status_code == 303
    assert client.post("/parameters", data={"alk": "8"}, follow_redirects=False).status_code in (303, 400)


def test_chart_svg():
    t0 = datetime(2026, 11, 1, 9, 0)
    pts = [(t0 + timedelta(days=i), v) for i, v in enumerate([8.0, 8.3, 9.4])]
    svg = str(line_chart(pts, low=7.5, high=9.0, unit="dKH", decimals=1, label="Alkalinity"))
    assert svg.startswith('<svg class="chart"') and 'class="chart-band"' in svg
    assert svg.count("<circle") == 3 and svg.count('class="chart-dot out"') == 1
    assert "9.4 dKH on 03 Nov 2026" in svg
    assert str(line_chart([], low=None, high=None, unit="", decimals=0, label="x")) == ""
    one = str(line_chart(pts[:1], low=None, high=None, unit="ppm", decimals=0, label="Ca"))
    assert one.count("<circle") == 1 and "chart-line" not in one
