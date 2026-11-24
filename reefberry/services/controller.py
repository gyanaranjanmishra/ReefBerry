"""Outlet automation: manual switching, the return-pump rule, feed and water change
modes, delayed restarts and the "off for too long" alert.

Everything that has to survive a restart lives in the database:
- outlet_status.held_by says who turned an outlet off and is responsible for turning it
  back on: 'rule' (return pump off), 'feed', 'water_change', or NULL (nobody: it stays
  as it is until you switch it). Only held outlets are ever switched back on.
- timers are the pending "turn back on" steps.
- settings["mode"] is the current mode.

The Kasa strips keep their own state, so if the Pi stops nothing changes on the tank.
When it starts again it reads the strips, finishes any steps that were due while it was
down, and keeps the return-pump safety rule (skimmer and ATO off while the return is off).
It never turns anything on that nobody asked for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ..db import Database
from .devices import list_devices
from .kasa_service import KasaError
from .notify import send_alert

log = logging.getLogger(__name__)

FOLLOW_RETURN = ("skimmer", "ato")  # switched off whenever a return pump is off
WATER_CHANGE_ORDER = ("return", "skimmer", "ato", "wavemaker")

DEFAULTS = {
    "feed_minutes": 5,
    "return_restart_delay_min": 10,
    "wc_wave_delay_min": 1,
    "wc_skimmer_delay_min": 10,
    "alert_after_min": 60,
}
LIMITS = {  # (min, max) accepted from the Settings form
    "feed_minutes": (1, 60),
    "return_restart_delay_min": (0, 120),
    "wc_wave_delay_min": (0, 60),
    "wc_skimmer_delay_min": (0, 120),
    "alert_after_min": (5, 24 * 60),
}
HOLD_REASONS = {
    "rule": "Waiting for the return pump",
    "feed": "Off for feeding",
    "water_change": "Off for water change",
}
RETRY_S = 30
FAIL_ALERT_EVERY_S = 3600


class ControlError(Exception):
    """A user action that can't be done; the message is shown on the dashboard."""


class Controller:
    def __init__(
        self,
        db: Database,
        kasa,
        clock: Callable[[], float] = time.time,
        notify: Callable[[Database, str, str], list[str]] = send_alert,
        poll_s: float = 5.0,
    ):
        self.db = db
        self.kasa = kasa
        self.clock = clock
        self.notify = notify
        self.poll_s = poll_s
        self.live: dict[str, dict] = {}  # outlet_id -> {"online", "is_on", "error"}
        self.last_poll: float | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._outbox: list[tuple[str, str]] = []
        self._failed_alerted: dict[str, float] = {}

    # ---------- settings ----------
    def setting(self, key: str) -> float:
        try:
            return float(self.db.get_setting(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    def mode(self) -> dict:
        return self.db.get_setting("mode") or {"name": "normal"}

    def _set_mode(self, name: str, **extra) -> None:
        self.db.set_setting("mode", {"name": name, "started": self.clock(), **extra})

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="reefberry-controller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        close = getattr(self.kasa, "close", None)
        if close:
            await close()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Controller tick failed")
            await asyncio.sleep(self.poll_s)

    async def ensure_fresh(self) -> None:
        """Used by the dashboard API so it works even before the first background poll."""
        if self._stale():
            await self.tick()

    def _stale(self) -> bool:
        return self.last_poll is None or self.clock() - self.last_poll > self.poll_s * 3

    async def _refresh_if_stale(self) -> None:
        """Actions decide what to switch from the last reading, so make sure there is a recent one."""
        if self._stale():
            await self._refresh()

    async def tick(self) -> None:
        async with self._lock:
            await self._refresh()
            await self._run_due_timers()
            await self._apply_return_rule()
            self._finish_feed_if_done()
            self._check_alerts()
        await self._flush_outbox()

    # ---------- data helpers ----------
    def _outlets(self) -> list[dict]:
        """Outlets on devices that aren't hidden (hidden outlets still follow the rules)."""
        rows = self.db.query(
            "SELECT o.*, d.hidden AS device_hidden FROM outlets o JOIN devices d ON d.id = o.device_id "
            "WHERE d.hidden = 0 ORDER BY o.sort_order, o.idx"
        )
        return [dict(r) for r in rows]

    def _device_for(self, outlet_id: str) -> dict:
        for d in list_devices(self.db):
            if any(o["id"] == outlet_id for o in d["outlets"]):
                return d
        raise ControlError("That outlet isn't known any more. Scan for devices again.")

    def status(self, outlet_id: str) -> dict:
        row = self.db.one("SELECT * FROM outlet_status WHERE outlet_id = ?", (outlet_id,))
        if row:
            return dict(row)
        self.db.execute("INSERT OR IGNORE INTO outlet_status(outlet_id) VALUES (?)", (outlet_id,))
        return {"outlet_id": outlet_id, "is_on": None, "off_since": None, "alerted": 0, "held_by": None}

    def _update_status(self, outlet_id: str, **values) -> None:
        self.status(outlet_id)
        cols = ", ".join(f"{k} = ?" for k in values)
        self.db.execute(f"UPDATE outlet_status SET {cols} WHERE outlet_id = ?", (*values.values(), outlet_id))

    def _set_hold(self, outlet_id: str, owner: str | None) -> None:
        self._update_status(outlet_id, held_by=owner)

    def _held(self, owner: str) -> set[str]:
        return {r["outlet_id"] for r in self.db.query("SELECT outlet_id FROM outlet_status WHERE held_by = ?", (owner,))}

    def timers(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM timers ORDER BY due_at, id")]

    def _add_timer(self, outlet_id: str, due_at: float, owner: str, reason: str) -> None:
        self.db.execute(
            "INSERT INTO timers(outlet_id, due_at, owner, reason) VALUES (?, ?, ?, ?)",
            (outlet_id, due_at, owner, reason),
        )

    def _cancel_timers(self, *, outlet_id: str | None = None, owner: str | None = None) -> None:
        if outlet_id is not None:
            self.db.execute("DELETE FROM timers WHERE outlet_id = ?", (outlet_id,))
        elif owner is not None:
            self.db.execute("DELETE FROM timers WHERE owner = ?", (owner,))

    def _returns_state(self, outlets: list[dict]) -> str:
        """'none' (no return configured), 'off' (any off), 'on' (all on) or 'unknown'."""
        returns = [o for o in outlets if o["role"] == "return"]
        if not returns:
            return "none"
        states = [self.live.get(o["id"], {}).get("is_on") for o in returns]
        if any(s is False for s in states):
            return "off"
        if all(s is True for s in states):
            return "on"
        return "unknown"

    # ---------- reading ----------
    async def _refresh(self) -> None:
        devices = [d for d in list_devices(self.db) if not d["hidden"]]
        states = await self.kasa.read_states(devices)
        now = self.clock()
        for d in devices:
            st = states.get(d["id"], {})
            for o in d["outlets"]:
                is_on = st.get("outlets", {}).get(o["id"]) if st.get("online") else None
                self.live[o["id"]] = {"online": bool(st.get("online")), "is_on": is_on, "error": st.get("error")}
                self._observe(o, is_on, now)
        self.last_poll = now

    def _observe(self, outlet: dict, is_on: bool | None, now: float) -> None:
        oid = outlet["id"]
        st = self.status(oid)
        values: dict = {"is_on": None if is_on is None else int(is_on)}
        if is_on:
            if st["held_by"]:
                # Turned on by someone else (Kasa app, the button on the strip): they're in charge now.
                values["held_by"] = None
                self._cancel_timers(outlet_id=oid)
                self.db.log_event("released", oid, "Turned on outside ReefBerry")
            if st["alerted"]:
                self._queue_back_on(outlet)
            values.update(off_since=None, alerted=0)
        elif st["off_since"] is None:
            values["off_since"] = now  # off (or unreachable) from now on
        self._update_status(oid, **values)

    # ---------- switching ----------
    async def _switch(self, outlet_id: str, on: bool, reason: str) -> None:
        await self.kasa.set_outlet(self._device_for(outlet_id), outlet_id, on)
        now = self.clock()
        self.live.setdefault(outlet_id, {"online": True, "error": None})
        self.live[outlet_id].update(is_on=on, online=True, error=None)
        st = self.status(outlet_id)
        if on:
            if st["alerted"]:
                outlet = self.db.one("SELECT * FROM outlets WHERE id = ?", (outlet_id,))
                if outlet:
                    self._queue_back_on(dict(outlet))
            self._update_status(outlet_id, is_on=1, off_since=None, alerted=0)
        else:
            self._update_status(outlet_id, is_on=0, off_since=st["off_since"] if st["is_on"] == 0 else now)
        self.db.log_event("on" if on else "off", outlet_id, reason)

    async def _auto_switch(self, outlet: dict, on: bool, reason: str) -> bool:
        """Switch for a rule, mode or timer. On failure, log and alert (at most hourly per outlet)."""
        try:
            await self._switch(outlet["id"], on, reason)
            return True
        except (KasaError, ControlError) as e:
            self.db.log_event("switch_failed", outlet["id"], f"{reason}: {e}")
            now = self.clock()
            if now - self._failed_alerted.get(outlet["id"], 0) >= FAIL_ALERT_EVERY_S:
                self._failed_alerted[outlet["id"]] = now
                verb = "on" if on else "off"
                self._outbox.append((
                    f"{self._tank()}: couldn't turn {outlet['display_name']} {verb}",
                    f"ReefBerry tried to turn {outlet['display_name']} {verb} ({reason.lower()}) but the "
                    f"strip didn't respond: {e}\n\nIt will keep trying. Please check the tank.",
                ))
            return False

    # ---------- the return pump rule ----------
    async def _apply_return_rule(self) -> None:
        if self.mode()["name"] == "water_change":
            return  # the water change owns these outlets until you press Done
        outlets = self._outlets()
        state = self._returns_state(outlets)
        if state == "off":
            self._cancel_timers(owner="rule")
            for o in outlets:
                if o["role"] in FOLLOW_RETURN and self.live.get(o["id"], {}).get("is_on") is True:
                    if await self._auto_switch(o, False, "Return pump is off"):
                        self._set_hold(o["id"], "rule")
        elif state == "on":
            pending = {t["outlet_id"] for t in self.timers() if t["owner"] == "rule"}
            due = self.clock() + self.setting("return_restart_delay_min") * 60
            for oid in self._held("rule") - pending:
                self._add_timer(oid, due, "rule", "Return pump is back on")

    # ---------- timers ----------
    async def _run_due_timers(self) -> None:
        now = self.clock()
        outlets = {o["id"]: o for o in self._outlets()}
        for t in self.db.query("SELECT * FROM timers WHERE due_at <= ? ORDER BY due_at, id", (now,)):
            self.db.execute("DELETE FROM timers WHERE id = ?", (t["id"],))
            o = outlets.get(t["outlet_id"])
            if o is None or self.status(o["id"])["held_by"] != t["owner"]:
                continue  # removed, hidden, or someone switched it themselves
            if o["role"] in FOLLOW_RETURN and self._returns_state(list(outlets.values())) == "off":
                self._set_hold(o["id"], "rule")  # never restart the skimmer/ATO with the return off
                continue
            if await self._auto_switch(o, True, t["reason"]):
                self._set_hold(o["id"], None)
            else:
                self._add_timer(o["id"], now + RETRY_S, t["owner"], t["reason"])

    # ---------- manual switching ----------
    async def user_switch(self, outlet_id: str, on: bool) -> None:
        async with self._lock:
            await self._refresh_if_stale()
            outlets = self._outlets()
            outlet = next((o for o in outlets if o["id"] == outlet_id), None)
            if outlet is None:
                raise ControlError("That outlet isn't on the dashboard any more. Reload the page.")
            if (
                on and outlet["role"] in FOLLOW_RETURN
                and self.mode()["name"] != "water_change"
                and self._returns_state(outlets) == "off"
            ):
                raise ControlError(
                    f"The return pump is off, so {outlet['display_name']} has to stay off to keep the sump "
                    "from overflowing. Turn the return pump on first."
                )
            try:
                await self._switch(outlet_id, on, "Switched from the dashboard")
            except KasaError as e:
                raise ControlError(str(e)) from e
            self._set_hold(outlet_id, None)  # you're in charge of this outlet now
            self._cancel_timers(outlet_id=outlet_id)
            await self._apply_return_rule()
        await self._flush_outbox()

    # ---------- feed mode ----------
    async def start_feed(self) -> None:
        async with self._lock:
            await self._refresh_if_stale()
            if self.mode()["name"] != "normal":
                raise ControlError("Finish the current mode first.")
            ends = self.clock() + self.setting("feed_minutes") * 60
            for o in self._outlets():
                if o["role"] == "wavemaker" and self.live.get(o["id"], {}).get("is_on") is True:
                    if await self._auto_switch(o, False, "Feed mode"):
                        self._set_hold(o["id"], "feed")
                        self._add_timer(o["id"], ends, "feed", "Feed mode finished")
            self._set_mode("feed", ends=ends)
            self.db.log_event("mode", None, "Feed mode started")
        await self._flush_outbox()

    async def end_feed(self) -> None:
        async with self._lock:
            if self.mode()["name"] != "feed":
                raise ControlError("Feed mode isn't running.")
            self.db.execute("UPDATE timers SET due_at = ? WHERE owner = 'feed'", (self.clock(),))
            await self._run_due_timers()
            self._set_mode("normal")
            self.db.log_event("mode", None, "Feed mode ended early")
        await self._flush_outbox()

    def _finish_feed_if_done(self) -> None:
        mode = self.mode()
        if mode["name"] == "feed" and self.clock() >= mode.get("ends", 0):
            if not any(t["owner"] == "feed" for t in self.timers()):
                self._set_mode("normal")
                self.db.log_event("mode", None, "Feed mode finished")

    # ---------- water change mode ----------
    async def start_water_change(self) -> None:
        async with self._lock:
            await self._refresh_if_stale()
            if self.mode()["name"] == "water_change":
                raise ControlError("A water change is already running.")
            # Anything waiting to come back on now waits for the end of the water change.
            for owner in ("feed", "rule", "water_change"):
                self._cancel_timers(owner=owner)
                for oid in self._held(owner):
                    self._set_hold(oid, "water_change")
            outlets = self._outlets()
            for role in WATER_CHANGE_ORDER:
                for o in outlets:
                    if o["role"] == role and self.live.get(o["id"], {}).get("is_on") is True:
                        if await self._auto_switch(o, False, "Water change"):
                            self._set_hold(o["id"], "water_change")
            self._set_mode("water_change")
            self.db.log_event("mode", None, "Water change started; alerts paused")
        await self._flush_outbox()

    async def finish_water_change(self) -> None:
        async with self._lock:
            await self._refresh_if_stale()
            if self.mode()["name"] != "water_change":
                raise ControlError("No water change is running.")
            now = self.clock()
            wave_at = now + self.setting("wc_wave_delay_min") * 60
            skim_at = wave_at + self.setting("wc_skimmer_delay_min") * 60
            held = self._held("water_change")
            for o in self._outlets():
                if o["id"] not in held:
                    continue
                if o["role"] == "return":
                    if await self._auto_switch(o, True, "Water change done"):
                        self._set_hold(o["id"], None)
                    else:
                        self._add_timer(o["id"], now + RETRY_S, "water_change", "Water change done")
                elif o["role"] in FOLLOW_RETURN:
                    self._add_timer(o["id"], skim_at, "water_change", "Water change done")
                elif o["role"] == "wavemaker":
                    self._add_timer(o["id"], wave_at, "water_change", "Water change done")
                else:
                    self._add_timer(o["id"], now, "water_change", "Water change done")
            # Alerts were paused: time spent off during the water change doesn't count.
            self.db.execute(
                "UPDATE outlet_status SET off_since = ?, alerted = 0 WHERE off_since IS NOT NULL", (now,)
            )
            self._set_mode("normal")
            self.db.log_event("mode", None, "Water change done; restarting equipment")
            await self._run_due_timers()
        await self._flush_outbox()

    # ---------- alerts ----------
    def _tank(self) -> str:
        return self.db.get_setting("tank_name") or "ReefBerry"

    def _check_alerts(self) -> None:
        if self.mode()["name"] == "water_change":
            return  # paused during water changes
        now = self.clock()
        after = self.setting("alert_after_min") * 60
        for o in self._outlets():
            if not o["alert_enabled"] or o["hidden"]:
                continue
            st = self.status(o["id"])
            if st["off_since"] is None or st["alerted"] or now - st["off_since"] < after:
                continue
            minutes = int((now - st["off_since"]) // 60)
            duration = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
            name = o["display_name"]
            if self.live.get(o["id"], {}).get("is_on") is False:
                subject = f"{self._tank()}: {name} has been off for {duration}"
                why = HOLD_REASONS.get(st["held_by"] or "", "It was switched off and hasn't been turned back on.")
            else:
                subject = f"{self._tank()}: can't reach {name} for {duration}"
                why = "The power strip isn't answering. It may be unplugged, without Wi-Fi, or without power."
            self._outbox.append((subject, f"{subject}.\n\n{why}\n\nYou'll get another message when it's back on."))
            self._update_status(o["id"], alerted=1)
            self.db.log_event("alert", o["id"], subject)

    def _queue_back_on(self, outlet: dict) -> None:
        subject = f"{self._tank()}: {outlet['display_name']} is back on"
        self._outbox.append((subject, f"{subject}."))

    async def _flush_outbox(self) -> None:
        while self._outbox:
            subject, body = self._outbox.pop(0)
            try:
                problems = await asyncio.to_thread(self.notify, self.db, subject, body)
            except Exception as e:  # never let a mail problem stop the controller
                problems = [str(e)]
            for p in problems:
                self.db.log_event("alert_failed", None, p)

    # ---------- for the dashboard ----------
    def view(self) -> dict:
        now = self.clock()
        mode = self.mode()
        statuses = {r["outlet_id"]: dict(r) for r in self.db.query("SELECT * FROM outlet_status")}
        next_on: dict[str, float] = {}
        for t in self.timers():
            next_on.setdefault(t["outlet_id"], t["due_at"])
        return {
            "now": now,
            "ready": self.last_poll is not None,
            "simulated": bool(getattr(self.kasa, "simulated", False)),
            "mode": mode,
            "statuses": statuses,
            "next_on": next_on,
        }
