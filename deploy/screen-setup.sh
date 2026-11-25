#!/usr/bin/env bash
# Show the ReefBerry dashboard full screen on a monitor plugged into the Pi.
#
# Needs Raspberry Pi OS with Desktop. Run as your normal user (not with sudo):
#   cd ~/ReefBerry && ./deploy/screen-setup.sh
# Then reboot. To undo:
#   ./deploy/screen-setup.sh --remove
set -euo pipefail

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, without sudo. It asks for sudo itself when needed."

AUTOSTART="$HOME/.config/autostart/reefberry-screen.desktop"
LAUNCHER="$HOME/.local/bin/reefberry-screen"

if [ "${1:-}" = "--remove" ]; then
  rm -f "$AUTOSTART" "$LAUNCHER"
  sudo reefberry screen off || true
  say "Removed. After a reboot the Pi shows its normal desktop again."
  exit 0
fi

command -v reefberry >/dev/null || die "Install ReefBerry first:  sudo ./install.sh"
if ! command -v labwc >/dev/null && ! command -v wayfire >/dev/null && ! command -v startlxde-pi >/dev/null; then
  die "No desktop found: this Pi runs Raspberry Pi OS Lite. A screen needs the Desktop version of Raspberry Pi OS."
fi

say "Checking for the Chromium browser"
BROWSER=$(command -v chromium || command -v chromium-browser || true)
if [ -z "$BROWSER" ]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq chromium || sudo apt-get install -y -qq chromium-browser
  BROWSER=$(command -v chromium || command -v chromium-browser) || die "Couldn't install Chromium."
fi

PORT=$(systemctl show reefberry -p Environment --value | tr ' ' '\n' | sed -n 's/^REEFBERRY_PORT=//p')
PORT=${PORT:-80}
URL="http://localhost/screen"
[ "$PORT" = "80" ] || URL="http://localhost:$PORT/screen"

say "Letting the Pi's own screen show the dashboard (read-only, no sign-in on the Pi)"
sudo reefberry screen on

say "Starting the dashboard automatically on the desktop"
mkdir -p "$(dirname "$LAUNCHER")" "$(dirname "$AUTOSTART")"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Made by ReefBerry's screen-setup.sh. Waits for ReefBerry, then shows it full screen,
# and reopens the browser if it ever closes.
until curl -s -o /dev/null "$URL"; do sleep 3; done
while true; do
  "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \\
    --password-store=basic --disable-session-crashed-bubble --disable-features=Translate \\
    --check-for-update-interval=31536000 --user-data-dir="\$HOME/.config/reefberry-screen" \\
    "$URL"
  sleep 5
done
EOF
chmod +x "$LAUNCHER"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=ReefBerry screen
Comment=Full-screen ReefBerry dashboard
Exec=$LAUNCHER
X-GNOME-Autostart-enabled=true
EOF

if command -v raspi-config >/dev/null; then
  say "Logging in to the desktop automatically at boot"
  sudo raspi-config nonint do_boot_behaviour B4
  say "Keeping the screen from going blank"
  sudo raspi-config nonint do_blanking 1
else
  echo "raspi-config not found: turn on desktop auto-login and turn off screen blanking yourself."
fi

say "Done"
echo "Reboot to start the screen:   sudo reboot"
echo "To undo later:                ./deploy/screen-setup.sh --remove"
