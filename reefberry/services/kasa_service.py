"""Kasa / Tapo smart plug access through python-kasa.

Discovers devices, reads outlet states and switches single outlets. Only the
controller (services/controller.py) calls set_outlet, and only for a user action,
a rule, a mode or a timer the user started.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from kasa import Credentials, Device, DeviceConfig, Discover
from kasa.exceptions import AuthenticationError

log = logging.getLogger(__name__)

ROLES: dict[str, str] = {
    "return": "Return pump",
    "skimmer": "Skimmer",
    "ato": "ATO",
    "wavemaker": "Wavemaker",
    "light": "Light",
    "reactor": "Reactor",
    "doser": "Doser",
    "heater": "Heater",
    "fan": "Fan / chiller",
    "other": "Other",
}

_ROLE_KEYWORDS = [
    ("return", ("return", "dcp", "main pump")),
    ("skimmer", ("skimmer",)),
    ("ato", ("ato", "top off", "top-off", "topoff")),
    ("wavemaker", ("wave", "powerhead", "gyre", "flow")),
    ("light", ("light", "led", "lamp")),
    ("reactor", ("reactor", "phosban", "gfo", "carbon")),
    ("doser", ("doser", "dosing", "kamoer")),
    ("heater", ("heater",)),
    ("fan", ("fan", "chiller")),
]


def guess_role(name: str | None) -> str:
    text = (name or "").lower()
    for role, words in _ROLE_KEYWORDS:
        if any(w in text for w in words):
            return role
    return "other"


@dataclass
class FoundOutlet:
    id: str
    idx: int
    alias: str
    is_on: bool | None


@dataclass
class FoundDevice:
    id: str
    host: str
    model: str | None = None
    alias: str | None = None
    mac: str | None = None
    firmware: str | None = None
    conn: dict | None = None
    needs_auth: bool = False
    error: str | None = None
    outlets: list[FoundOutlet] = field(default_factory=list)


CredentialsProvider = Callable[[], "tuple[str, str] | None"]


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


class KasaService:
    def __init__(self, credentials_provider: CredentialsProvider, timeout: int = 6):
        self._creds = credentials_provider
        self.timeout = timeout
        self._conns: dict[str, Device] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    simulated = False

    def _credentials(self) -> Credentials | None:
        pair = self._creds()
        if pair and pair[0] and pair[1]:
            return Credentials(pair[0], pair[1])
        return None

    # ---------- discovery ----------
    async def discover(self, extra_hosts: list[str] | None = None) -> list[FoundDevice]:
        """Broadcast discovery, plus direct lookups for any hosts the user typed in."""
        creds = self._credentials()
        devices: dict[str, Device] = {}
        try:
            devices = await Discover.discover(credentials=creds, discovery_timeout=self.timeout)
        except Exception as e:  # network without broadcast, etc.
            log.warning("Broadcast discovery failed: %s", e)

        locked: list[FoundDevice] = []
        for host in extra_hosts or []:
            host = host.strip()
            if not host or host in devices:
                continue
            try:
                dev = await self._lookup_single(host, creds)
            except AuthenticationError:
                locked.append(_locked(host))
                continue
            if dev is not None:
                devices[host] = dev

        results = await asyncio.gather(*(self._describe(h, d) for h, d in devices.items()))
        return sorted([*results, *locked], key=lambda d: d.host)

    async def lookup_host(self, host: str) -> FoundDevice | None:
        host = host.strip()
        try:
            dev = await self._lookup_single(host, self._credentials())
        except AuthenticationError:
            return _locked(host)
        return await self._describe(host, dev) if dev else None

    async def _lookup_single(self, host: str, creds: Credentials | None) -> Device | None:
        try:
            return await Discover.discover_single(
                host, credentials=creds, discovery_timeout=self.timeout
            )
        except AuthenticationError:
            raise
        except Exception as e:
            log.info("Direct discovery of %s failed (%s); trying legacy connect", host, e)
        try:  # older Kasa firmware answers on TCP 9999 even when UDP is blocked
            return await Device.connect(host=host)
        except Exception as e:
            log.info("Could not reach %s: %s", host, e)
            return None

    async def _describe(self, host: str, dev: Device) -> FoundDevice:
        found = FoundDevice(id=f"host:{host}", host=host)
        try:
            await asyncio.wait_for(dev.update(), self.timeout + 4)
        except AuthenticationError:
            found.needs_auth = True
            found.error = "This device needs your TP-Link account login."
        except Exception as e:
            found.error = f"Could not read device: {type(e).__name__}"
        if found.error is None and _looks_like_unsplit_strip(dev):
            # Newer (KLAP) firmware: discovery can't tell a strip from a single plug, so
            # python-kasa builds a plug object with no outlets. Connecting directly reads the
            # device info first and picks the strip class, which exposes each outlet.
            try:
                strip = await asyncio.wait_for(Device.connect(config=dev.config), self.timeout + 4)
                await _close(dev)
                dev = strip
            except Exception as e:
                found.error = f"Could not read the strip's outlets: {type(e).__name__}"
        found.model = _safe(lambda: dev.model)
        found.alias = _safe(lambda: dev.alias)
        found.mac = _safe(lambda: dev.mac)
        dev_id = _safe(lambda: dev.device_id)
        if dev_id:
            found.id = dev_id
        found.firmware = _safe(lambda: dev.hw_info.get("sw_ver"))
        found.conn = _safe(lambda: dev.config.to_dict_control_credentials(exclude_credentials=True))
        if found.error is None:
            children = _safe(lambda: list(dev.children), []) or []
            if children:
                for i, ch in enumerate(children):
                    found.outlets.append(
                        FoundOutlet(
                            id=_safe(lambda ch=ch: ch.device_id, f"{found.id}:{i}"),
                            idx=i,
                            alias=_safe(lambda ch=ch: ch.alias, f"Outlet {i + 1}") or f"Outlet {i + 1}",
                            is_on=_safe(lambda ch=ch: ch.is_on),
                        )
                    )
            else:
                found.outlets.append(
                    FoundOutlet(id=found.id, idx=0, alias=found.alias or "Plug", is_on=_safe(lambda: dev.is_on))
                )
        await _close(dev)
        return found

    # ---------- reading and switching ----------
    # Connections are kept open between polls so newer (KLAP) firmware doesn't need a
    # fresh login handshake every few seconds. A broken connection is dropped and retried once.

    async def _connect(self, row: dict) -> Device:
        if row.get("conn"):
            cfg = DeviceConfig.from_dict(dict(row["conn"], host=row["host"]))
            cfg.credentials = self._credentials()
            return await asyncio.wait_for(Device.connect(config=cfg), self.timeout + 4)
        return await asyncio.wait_for(Device.connect(host=row["host"]), self.timeout + 4)

    async def _fresh(self, row: dict) -> Device:
        """A connected, freshly updated device, reusing the cached connection when it still works."""
        lock = self._locks.setdefault(row["id"], asyncio.Lock())
        async with lock:
            dev = self._conns.get(row["id"])
            if dev is not None and getattr(dev, "host", row["host"]) == row["host"]:
                try:
                    await asyncio.wait_for(dev.update(), self.timeout + 4)
                    return dev
                except AuthenticationError:
                    raise
                except Exception:
                    await _close(dev)
                    self._conns.pop(row["id"], None)
            dev = await self._connect(row)  # Device.connect already runs update()
            self._conns[row["id"]] = dev
            return dev

    async def read_states(self, devices: list[dict]) -> dict[str, dict]:
        """Read outlet states for saved devices.

        `devices` are rows with keys id, host, conn. Returns
        {device_id: {"online": bool, "outlets": {outlet_id: is_on}, "error": str|None}}.
        """
        results = await asyncio.gather(*(self._read_one(d) for d in devices))
        return {d["id"]: r for d, r in zip(devices, results)}

    async def _read_one(self, row: dict) -> dict:
        try:
            dev = await self._fresh(row)
        except AuthenticationError:
            return {"online": False, "outlets": {}, "error": "needs TP-Link login"}
        except Exception as e:
            return {"online": False, "outlets": {}, "error": type(e).__name__}
        children = list(dev.children)
        if children:
            states = {_safe(lambda c=c: c.device_id, f"{row['id']}:{i}"): c.is_on for i, c in enumerate(children)}
        else:
            states = {row["id"]: dev.is_on}
        return {"online": True, "outlets": states, "error": None}

    async def set_outlet(self, row: dict, outlet_id: str, on: bool) -> None:
        """Switch one outlet. Raises KasaError with a readable message if it can't."""
        try:
            dev = await self._fresh(row)
        except AuthenticationError as e:
            raise KasaError("This strip needs your TP-Link login (Settings → Power devices).") from e
        except Exception as e:
            raise KasaError(f"Can't reach {row.get('alias') or row['host']} ({type(e).__name__}).") from e
        children = list(dev.children)
        target = dev
        if children:
            target = next((c for c in children if _safe(lambda c=c: c.device_id) == outlet_id), None)
            if target is None:
                raise KasaError("That outlet is no longer on this strip. Scan for devices again.")
        try:
            await asyncio.wait_for(target.turn_on() if on else target.turn_off(), self.timeout + 4)
        except Exception as e:
            self._conns.pop(row["id"], None)
            await _close(dev)
            raise KasaError(f"The strip didn't accept the command ({type(e).__name__}).") from e

    async def close(self) -> None:
        for dev in list(self._conns.values()):
            await _close(dev)
        self._conns.clear()


class KasaError(Exception):
    pass


class SimulatedKasa:
    """Pretend plugs for trying the app without touching real equipment (REEFBERRY_SIMULATE=1).

    Uses the devices already saved in the database; every outlet starts ON.
    """

    simulated = True

    def __init__(self):
        self.states: dict[str, bool] = {}

    async def discover(self, extra_hosts=None):
        return []

    async def read_states(self, devices: list[dict]) -> dict[str, dict]:
        out = {}
        for d in devices:
            if d.get("needs_auth"):
                out[d["id"]] = {"online": False, "outlets": {}, "error": "needs TP-Link login"}
                continue
            outlets = {o["id"]: self.states.setdefault(o["id"], True) for o in d.get("outlets", [])}
            out[d["id"]] = {"online": True, "outlets": outlets, "error": None}
        return out

    async def set_outlet(self, row: dict, outlet_id: str, on: bool) -> None:
        self.states[outlet_id] = on

    async def close(self) -> None:
        pass


def _looks_like_unsplit_strip(dev) -> bool:
    """True for a multi-outlet device that python-kasa opened as a single plug."""
    if _safe(lambda: list(dev.children), []):
        return False
    sys_info = _safe(lambda: dev.sys_info, {}) or {}
    return isinstance(sys_info, dict) and bool(sys_info.get("children"))


def _locked(host: str) -> FoundDevice:
    return FoundDevice(
        id=f"host:{host}", host=host, needs_auth=True,
        error="This device needs your TP-Link account login.",
    )


async def _close(dev: Device) -> None:
    try:
        await dev.disconnect()
    except Exception:
        pass
