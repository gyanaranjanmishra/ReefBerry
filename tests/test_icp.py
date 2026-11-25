from datetime import date, timedelta

import pytest

from conftest import create_account, csrf_of
from reefberry.services import icp as I
from reefberry.services.icp_elements import ELEMENTS

# A made-up Fauna Marin CSV export (same layout as the real one).
CSV = (
    "id;water_type;na;s;k;b;mg;ca;sr;br;i;p;si;zn;v;cu;ni;mo;ba;co;cr;fe;li;mn;se;al;pb;rb;fluoride;tnb;analysis_date;note\n"
    "900001;0;9848;765;360;4.04;1355;407;7.05;72.1;0.019;0.004;0.061;6.84;0;2.83;0.939;6.63;14.6;0;0;0;199;0.325;0;2.69;0;;;;2026-11-19 14:40:21;\n"
).encode()

PDF_TEXT = """REEF ICP
Analysis ID: 900002
Sampling Date: 11-14-2026
measured Reference Range
Sodium Na 9848 9500 - 10700 - 11500
Sulfur S 765 850 -  900  - 950
Sulfate 2292 2550 - 2700 - 2850
Potassium K 360 380 -  395  - 420
Boron B 4,04 3,8 -  4,5  - 5,5
Br 72,1 55 -  67  - 75
I 0,019 0,055 - 0,065 - 0,080
P 0,004 0,006 0,060
Vanadium V n.d. 2 - 6 - 10
Nickel Ni 0,94 3 - 4,5 - 6
Molybdenum Mo 6,6 10 - 15 - 20
Rubidium Rb not measured
"""


def _defaults():
    return {e.sym: (e.min, e.opt, e.max) for e in ELEMENTS}


def _ready(client):
    create_account(client)
    db = client.app.state.db
    db.set_settings({"setup_complete": True, "tank_name": "Reef", "tank_volume_l": 250})
    return db


def test_parse_value():
    assert I.parse_value("") is None and I.parse_value("not measured") is None
    assert I.parse_value("n.d.") == 0 and I.parse_value(" 4,04 ") == 4.04
    with pytest.raises(I.IcpError):
        I.parse_value("abc")
    with pytest.raises(I.IcpError):
        I.parse_value("-3")


def test_parse_csv():
    p = I.parse_csv(CSV)
    assert p.analysis_id == "900001" and p.sampled_on == "2026-11-19" and p.lab == "Fauna Marin"
    assert p.values["Na"] == 9848 and p.values["V"] == 0 and "Rb" not in p.values
    with pytest.raises(I.IcpError):
        I.parse_csv(b"hello;world\n1;2\n")


def test_parse_pdf_text():
    p = I.parse_pdf_text(PDF_TEXT)
    assert p.analysis_id == "900002" and p.sampled_on == "2026-11-14"
    assert p.values["B"] == 4.04 and p.values["I"] == 0.019 and p.values["Br"] == 72.1
    assert p.values["V"] == 0 and "Rb" not in p.values
    with pytest.raises(I.IcpError):
        I.parse_upload("report.pdf", b"%PDF-1.4 broken")
    with pytest.raises(I.IcpError):
        I.parse_upload("empty.csv", b"")


def test_dosing_matches_fauna_marin_report():
    """At 60 L these are the doses on the lab's own report for the same values."""
    values = I.parse_csv(CSV).values
    rows, salinity_low = I.dosing(values, _defaults(), 60)
    got = {r.el.dose.product: (round(r.total_ml, 1), r.days, round(r.per_day_ml, 1)) for r in rows}
    assert got == {
        "Elementals S": (115.7, 7, 16.5), "Elementals K": (21.0, 1, 21.0),
        "Trace I": (2.8, 2, 1.4), "Elementals P": (1.7, 2, 0.9),
        "Trace V": (0.7, 3, 0.2), "Trace Ni": (0.5, 1, 0.5), "Trace Mo": (0.8, 2, 0.4),
        "Trace Co": (0.1, 1, 0.1), "Trace Cr": (1.4, 3, 0.5), "Trace Fe": (0.2, 2, 0.1),
        "Trace Se": (3.8, 4, 1.0),
    }
    assert [r.el.dose.priority for r in rows] == sorted(r.el.dose.priority for r in rows)
    assert salinity_low
    # Scales with the tank: 250 L needs 250/60 times as much.
    big = {r.el.sym: r.total_ml for r in I.dosing(values, _defaults(), 250)[0]}
    assert big["S"] == pytest.approx(115.714 * 250 / 60, rel=1e-3)


def test_no_salinity_warning_when_sodium_is_fine():
    assert I.dosing({"Na": 10800, "S": 800}, _defaults(), 250)[1] is False


def test_upload_review_save_flow(client):
    db = _ready(client)
    page = client.get("/icp")
    assert page.status_code == 200 and "No ICP tests yet" in page.text
    r = client.post("/icp/upload", data={"csrf": csrf_of(page.text)},
                    files={"file": ("icp.csv", CSV, "text/csv")})
    assert r.status_code == 200 and "Check before saving" in r.text
    assert 'name="v_Na" value="9848"' in r.text and 'name="v_V" value="n.d."' in r.text
    assert db.one("SELECT COUNT(*) n FROM icp_reports")["n"] == 0  # nothing saved yet
    form = {"csrf": csrf_of(r.text), "sampled_on": "2026-11-14", "lab": "Fauna Marin", "analysis_id": "900001",
            "v_Na": "9848", "v_S": "765", "v_K": "360", "v_I": "0,019", "v_V": "n.d.", "v_Ni": "0.94"}
    r = client.post("/icp/save", data=form, follow_redirects=True)
    assert r.status_code == 200 and "saved (6 elements)" in r.text
    assert "Raise salinity first" in r.text and "Elementals S" in r.text
    assert "482.1 ml" in r.text  # 10/100/7 * 250 L * (900 - 765)
    # The same analysis can't be saved twice.
    r = client.post("/icp/save", data={**form, "csrf": csrf_of(r.text)})
    assert r.status_code == 400 and "already saved" in r.text
    # Bad values keep what was typed.
    r = client.post("/icp/save", data={**form, "csrf": csrf_of(r.text), "analysis_id": "", "v_K": "lots"})
    assert r.status_code == 400 and "Potassium" in r.text and 'value="lots"' in r.text


def test_hand_entry_and_history(client):
    db = _ready(client)
    I.save_report(db, (date.today() - timedelta(days=40)).isoformat(), {"Mg": 1300, "Sr": 6.0})
    rid = I.save_report(db, date.today().isoformat(), {"Mg": 1380, "Sr": 7.5})
    assert client.get("/icp/new").status_code == 200
    page = client.get(f"/icp?report={rid}")
    assert "↑" in page.text and "Magnesium" in page.text
    el = client.get("/icp/element/Mg")
    assert el.status_code == 200 and '<svg class="chart"' in el.text and "1380" in el.text
    assert client.get("/icp/element/Xx").status_code == 404
    with pytest.raises(I.IcpError, match="future"):
        I.save_report(db, (date.today() + timedelta(days=2)).isoformat(), {"Mg": 1300})


def test_targets(client):
    db = _ready(client)
    page = client.get("/icp/targets")
    assert page.status_code == 200
    tg = I.targets(db)
    data = {"csrf": csrf_of(page.text)}
    for e in ELEMENTS:
        for k, v in zip(("min", "opt", "max"), tg[e.sym]):
            data[f"{k}_{e.sym}"] = "" if v is None else str(v)
    data.update({"min_K": "390", "opt_K": "410"})
    client.post("/icp/targets", data=data)
    assert I.targets(db)["K"] == (390, 410, 420)
    assert db.one("SELECT COUNT(*) n FROM icp_targets")["n"] == 1  # only the change is stored
    data.update({"opt_K": "500"})
    r = client.post("/icp/targets", data=data, follow_redirects=True)
    assert "between the minimum and maximum" in r.text and I.targets(db)["K"][1] == 410
    client.post("/icp/targets", data={"csrf": data["csrf"], "reset": "1"})
    assert I.targets(db)["K"] == (380, 395, 420)


def test_dose_log_and_delete(client):
    db = _ready(client)
    rid = I.save_report(db, date.today().isoformat(), {"K": 360})
    csrf = csrf_of(client.get("/icp").text)
    client.post("/icp/doses", data={"csrf": csrf, "product": "Elementals K", "ml": "21",
                                    "dosed_on": date.today().isoformat(), "report_id": str(rid)})
    assert I.recent_doses(db)[0]["ml"] == 21
    r = client.post("/icp/doses", data={"csrf": csrf, "product": "", "ml": "x", "dosed_on": ""}, follow_redirects=True)
    assert "Pick the day" in r.text
    client.post(f"/icp/{rid}/delete", data={"csrf": csrf})
    assert I.reports(db) == [] and I.recent_doses(db)[0]["ml"] == 21  # the log stays


def test_icp_needs_sign_in(client):
    _ready(client)
    client.post("/logout", data={"csrf": csrf_of(client.get("/").text)})
    assert client.get("/icp", follow_redirects=False).status_code == 303


def test_targets_page_keeps_exact_defaults(client):
    """Saving the targets page untouched must not change any target (e.g. Cr 1.175)."""
    import re
    db = _ready(client)
    page = client.get("/icp/targets").text
    data = dict(re.findall(r'name="((?:min|opt|max)_\w+)" value="([^"]*)"', page))
    data["csrf"] = csrf_of(page)
    client.post("/icp/targets", data=data)
    assert db.one("SELECT COUNT(*) n FROM icp_targets")["n"] == 0
