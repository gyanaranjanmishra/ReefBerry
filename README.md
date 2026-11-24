# ReefBerry

A reef aquarium dashboard that runs on a Raspberry Pi.

- **Power control** for Kasa / Tapo smart plugs and power strips, with feed mode, water-change mode and equipment rules
- **Alerts** by email or phone push (ntfy) when equipment stays off too long
- **Water parameters** with trend charts and your own target ranges *(planned)*
- **ICP results and dosing**: import Fauna Marin ICP reports and get dose suggestions for your system volume *(planned)*
- **pH and ORP** monitoring with Atlas Scientific EZO circuits *(planned)*

Everything runs on your Pi and your home network. No cloud account is needed except
the TP-Link login some newer plugs ask for, which stays on the Pi.

## Status

Working now: sign-in, a first-run setup wizard, live outlet states, switching outlets
from the dashboard, and these automations:

- **Return pump rule.** When a return pump is off (switched from ReefBerry, the Kasa app or
  the button on the strip), the skimmer and ATO turn off. They come back 10 minutes after the
  return is on again, but only if ReefBerry turned them off. Something you switched off
  yourself, such as a skimmer you're cleaning, stays off until you turn it on.
- **Feed mode.** Wavemakers off for 5 minutes, then back on.
- **Water change mode.** Return, wavemakers, skimmer and ATO off, and off-alerts paused.
  Press Done: the return starts at once, the wavemakers 1 minute later, and the skimmer and
  ATO 10 minutes after that.
- **Off alert.** Email or push when an outlet has been off (or unreachable) for an hour, and
  again when it's back on.

All the times are in Settings → Automation.

The strips keep their state if the Pi stops, so nothing changes on the tank. When ReefBerry
starts again it reads the strips, finishes any restart steps that came due while it was
down, and keeps the return pump rule. It never turns on an outlet that nobody asked for.

To try it without touching real equipment, start it with `REEFBERRY_SIMULATE=1`: the
outlets become pretend plugs.

## Install on a Raspberry Pi

Requirements: Raspberry Pi 3, 4 or 5 with Raspberry Pi OS Bookworm or newer (64-bit recommended),
on the same network as your smart plugs.

```bash
git clone <this repository> reefberry
cd reefberry
sudo ./install.sh
```

Then open `http://<your-pi-name>.local` (for example `http://raspberrypi.local`) and
follow the setup wizard. To use another port: `REEFBERRY_PORT=8080 sudo -E ./install.sh`.

Run `sudo ./install.sh` again to update. Your data in `/var/lib/reefberry` is kept.

### Reaching the dashboard away from home

Install [Tailscale](https://tailscale.com) on the Pi and on your phone. You can then
open the dashboard from anywhere without opening ports on your router. Don't
forward the dashboard's port to the internet.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # Windows: .venv\Scripts\pip install -e ".[dev]"
REEFBERRY_DATA_DIR=.data REEFBERRY_PORT=8080 .venv/bin/reefberry
.venv/bin/pytest
```

## Where things are stored

`/var/lib/reefberry/reefberry.db` (SQLite) holds your settings, devices and history.
Mail and TP-Link passwords are stored there in plain text, readable only by the
`reefberry` service user. Back this file up to keep your history.

## Changing the equipment icons

Each dashboard drawing is one SVG file in `reefberry/static/icons/` (`skimmer.svg`,
`wavemaker.svg`, `return.svg` and so on). To replace one without touching the app, put
your own file with the same name in `/var/lib/reefberry/icons/`; it wins over the built-in
one. A file with a new name (for example `kalk-stirrer.svg`) shows up in the **Icon**
picker next to each outlet in Settings. Changes appear on the next page load.

Animations come from shared CSS classes such as `a-spin`, `a-bub`, `a-flow`, `a-drip` and
`a-glow` (see `static/css/app.css`). They pause and the drawing turns grey when the outlet
is off. Icons that contain scripts or event handlers are ignored.

## Credits

- Dosing calculations will follow the
  [Fauna Marin Elementals calculator](https://lab.faunamarin.de/en/calc-elementals-once).
  ReefBerry is an independent project and isn't affiliated with or endorsed by Fauna Marin.
- Smart plug control uses [python-kasa](https://github.com/python-kasa/python-kasa).

## Licence

MIT. See [LICENSE](LICENSE).
