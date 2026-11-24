"""Rules, modes, timers and alerts, driven with a fake clock and fake plugs."""

import asyncio

import pytest

from conftest import FakeKasa
from reefberry.db import Database
from reefberry.services.controller import ControlError, Controller
from reefberry.services.devices import save_found_devices
from reefberry.services.kasa_service import FoundDevice, FoundOutlet

MIN = 60

OUTLETS = [  # (id, name, role, starts on)
    ("RET", "Return", "return", True),
    ("SKIM", "Skimmer", "skimmer", True),
    ("ATO", "ATO", "ato", True),
    ("WL", "Wave L", "wavemaker", True),
    ("WR", "Wave R", "wavemaker", True),
    ("LIGHT", "Light", "light", True),
]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Tank:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "t.db")
        self.db.set_setting("tank_name", "Reef 250")
        self.kasa = FakeKasa()
        strip_a = FoundDevice(id="A", host="10.0.0.1", model="KP303", alias="A",
                              outlets=[FoundOutlet(i, n, name, on) for n, (i, name, _, on) in enumerate(OUTLETS[:3])])
        strip_b = FoundDevice(id="B", host="10.0.0.2", model="KP303", alias="B",
                              outlets=[FoundOutlet(i, n, name, on) for n, (i, name, _, on) in enumerate(OUTLETS[3:])])
        self.kasa.found = [strip_a, strip_b]
        self.kasa.states = {i: on for i, _, _, on in OUTLETS}
        save_found_devices(self.db, self.kasa.found)
        for i, _, role, _ in OUTLETS:
            self.db.execute("UPDATE outlets SET role = ? WHERE id = ?", (role, i))
        self.clock = Clock()
        self.sent: list[tuple[str, str]] = []
        self.ctrl = self.new_controller()

    def new_controller(self):
        def notify(db, subject, body):
            self.sent.append((subject, body))
            return []
        return Controller(self.db, self.kasa, clock=self.clock, notify=notify)

    def on(self, oid):
        return self.kasa.states[oid]

    def held(self, oid):
        return self.ctrl.status(oid)["held_by"]


@pytest.fixture
def tank(tmp_path):
    return Tank(tmp_path)


def run(coro):
    return asyncio.run(coro)


# ---------- return pump rule ----------
def test_return_off_turns_skimmer_and_ato_off_and_back_on_10_min_later(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("RET", False)
        assert not tank.on("SKIM") and not tank.on("ATO")
        assert tank.held("SKIM") == "rule"
        assert tank.on("WL") and tank.on("LIGHT")  # nothing else touched

        await c.user_switch("RET", True)
        assert not tank.on("SKIM")  # not straight away
        tank.clock.advance(9 * MIN)
        await c.tick()
        assert not tank.on("SKIM")
        tank.clock.advance(1 * MIN)
        await c.tick()
        assert tank.on("SKIM") and tank.on("ATO")
        assert tank.held("SKIM") is None
    run(go())


def test_skimmer_off_for_cleaning_stays_off(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("SKIM", False)  # cleaning the cup
        await c.user_switch("RET", False)
        await c.user_switch("RET", True)
        tank.clock.advance(15 * MIN)
        await c.tick()
        assert not tank.on("SKIM")
        assert tank.on("ATO")  # the rule turned the ATO off, so it brings it back
    run(go())


def test_return_switched_off_outside_reefberry_is_noticed(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        tank.kasa.states["RET"] = False  # Kasa app or the button on the strip
        await c.tick()
        assert not tank.on("SKIM") and not tank.on("ATO")
        tank.kasa.states["RET"] = True
        await c.tick()
        tank.clock.advance(10 * MIN)
        await c.tick()
        assert tank.on("SKIM")
    run(go())


def test_cannot_turn_skimmer_on_while_return_is_off(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("RET", False)
        with pytest.raises(ControlError, match="return pump is off"):
            await c.user_switch("SKIM", True)
        assert not tank.on("SKIM")
    run(go())


def test_return_off_again_during_delay_restarts_the_wait(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("RET", False)
        await c.user_switch("RET", True)
        tank.clock.advance(8 * MIN)
        await c.user_switch("RET", False)
        await c.user_switch("RET", True)
        tank.clock.advance(5 * MIN)  # 13 min after the first restart, 5 after the second
        await c.tick()
        assert not tank.on("SKIM")
        tank.clock.advance(5 * MIN)
        await c.tick()
        assert tank.on("SKIM")
    run(go())


def test_turning_a_held_outlet_on_elsewhere_releases_it(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("RET", False)
        await c.user_switch("RET", True)
        tank.kasa.states["SKIM"] = True  # you turned it on in the Kasa app
        await c.tick()
        assert tank.held("SKIM") is None
        assert all(t["outlet_id"] != "SKIM" for t in c.timers())
    run(go())


# ---------- feed mode ----------
def test_feed_mode_turns_wavemakers_off_for_5_minutes(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("WR", False)  # already off: must stay off afterwards
        await c.start_feed()
        assert c.mode()["name"] == "feed"
        assert not tank.on("WL") and tank.on("RET") and tank.on("SKIM")
        with pytest.raises(ControlError):
            await c.start_feed()
        tank.clock.advance(4 * MIN)
        await c.tick()
        assert not tank.on("WL")
        tank.clock.advance(1 * MIN)
        await c.tick()
        assert tank.on("WL") and not tank.on("WR")
        assert c.mode()["name"] == "normal"
    run(go())


def test_feed_mode_can_end_early(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.start_feed()
        tank.clock.advance(MIN)
        await c.end_feed()
        assert tank.on("WL") and tank.on("WR")
        assert c.mode()["name"] == "normal"
    run(go())


# ---------- water change ----------
def test_water_change_and_restart_sequence(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("SKIM", False)  # off for cleaning before the water change
        await c.start_water_change()
        assert c.mode()["name"] == "water_change"
        for oid in ("RET", "ATO", "WL", "WR"):
            assert not tank.on(oid), oid
        assert tank.on("LIGHT")

        tank.clock.advance(45 * MIN)
        await c.tick()
        assert not tank.on("RET")  # nothing comes back by itself

        await c.finish_water_change()
        assert tank.on("RET") and not tank.on("WL") and not tank.on("ATO")
        tank.clock.advance(1 * MIN)
        await c.tick()
        assert tank.on("WL") and tank.on("WR") and not tank.on("ATO")
        tank.clock.advance(10 * MIN)
        await c.tick()
        assert tank.on("ATO")
        assert not tank.on("SKIM")  # you had switched it off yourself
    run(go())


def test_water_change_during_feed_takes_over(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.start_feed()
        await c.start_water_change()
        tank.clock.advance(10 * MIN)  # feed would have ended by now
        await c.tick()
        assert not tank.on("WL")
        await c.finish_water_change()
        tank.clock.advance(1 * MIN)
        await c.tick()
        assert tank.on("WL")
    run(go())


def test_restart_steps_survive_a_pi_restart(tank):
    async def go():
        await tank.ctrl.tick()
        await tank.ctrl.start_water_change()
        await tank.ctrl.finish_water_change()
        tank.clock.advance(30 * MIN)  # the Pi was off while the steps came due
        tank.ctrl = tank.new_controller()
        await tank.ctrl.tick()
        assert all(tank.on(o) for o in ("RET", "WL", "WR", "SKIM", "ATO"))
    run(go())


def test_startup_does_not_switch_anything_when_all_is_normal(tank):
    async def go():
        tank.kasa.states["LIGHT"] = False
        await tank.ctrl.tick()
        assert tank.kasa.switch_calls == []
    run(go())


# ---------- alerts ----------
def test_off_for_an_hour_sends_one_alert_then_back_on(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.user_switch("LIGHT", False)
        tank.clock.advance(59 * MIN)
        await c.tick()
        assert tank.sent == []
        tank.clock.advance(1 * MIN)
        await c.tick()
        assert len(tank.sent) == 1 and "Light has been off for 1 h 0 min" in tank.sent[0][0]
        tank.clock.advance(30 * MIN)
        await c.tick()
        assert len(tank.sent) == 1  # once per off period
        await c.user_switch("LIGHT", True)
        assert "Light is back on" in tank.sent[1][0]
    run(go())


def test_alerts_paused_during_water_change_and_reset_after(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.start_water_change()
        tank.clock.advance(90 * MIN)
        await c.tick()
        assert tank.sent == []
        await c.user_switch("LIGHT", False)
        await c.finish_water_change()
        tank.clock.advance(30 * MIN)
        await c.tick()
        assert tank.sent == []  # the light's clock started again at Done
        tank.clock.advance(30 * MIN)
        await c.tick()
        assert [s for s, _ in tank.sent] == ["Reef 250: Light has been off for 1 h 0 min"]
    run(go())


def test_alert_switch_can_be_turned_off_per_outlet(tank):
    async def go():
        tank.db.execute("UPDATE outlets SET alert_enabled = 0 WHERE id = 'LIGHT'")
        await tank.ctrl.tick()
        await tank.ctrl.user_switch("LIGHT", False)
        tank.clock.advance(3 * 60 * MIN)
        await tank.ctrl.tick()
        assert tank.sent == []
    run(go())


def test_unreachable_strip_alerts_after_an_hour(tank):
    async def go():
        await tank.ctrl.tick()
        tank.kasa.offline.add("B")
        await tank.ctrl.tick()
        tank.clock.advance(60 * MIN)
        await tank.ctrl.tick()
        subjects = [s for s, _ in tank.sent]
        assert "Reef 250: can't reach Wave L for 1 h 0 min" in subjects
        assert not any("Return" in s for s in subjects)
    run(go())


def test_failed_automatic_switch_alerts_and_retries(tank):
    async def go():
        c = tank.ctrl
        await c.tick()
        await c.start_feed()
        tank.kasa.offline.add("B")  # wavemaker strip drops off Wi-Fi
        tank.clock.advance(5 * MIN)
        await c.tick()
        assert any("couldn't turn Wave L on" in s for s, _ in tank.sent)
        tank.kasa.offline.discard("B")
        tank.clock.advance(30)
        await c.tick()
        assert tank.on("WL")
    run(go())
