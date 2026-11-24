#!/usr/bin/env bash
# ReefBerry installer for Raspberry Pi OS (Bookworm or newer).
#
# First install:   git clone https://github.com/<you>/ReefBerry.git && cd ReefBerry && sudo ./install.sh
# Update:          cd ReefBerry && git pull && sudo ./install.sh
# Your data in /var/lib/reefberry is kept.
#
# Options (environment variables):
#   REEFBERRY_PORT=8080 sudo -E ./install.sh   # use another port instead of 80
set -euo pipefail

APP_DIR=/opt/reefberry
DATA_DIR=/var/lib/reefberry
SERVICE=reefberry
PORT="${REEFBERRY_PORT:-80}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this with sudo:  sudo ./install.sh"
[ -f "$SRC_DIR/pyproject.toml" ] || die "Run this from the ReefBerry project folder."
command -v apt-get >/dev/null || die "This installer needs Raspberry Pi OS or another Debian-based system."

say "Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null

PYV=$(python3 -c 'import sys; print("%d%02d" % sys.version_info[:2])')
[ "$PYV" -ge 311 ] || die "Python 3.11 or newer is needed (found $(python3 --version)). Update Raspberry Pi OS first."

say "Creating the reefberry system user"
if ! id reefberry >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin reefberry
fi
# I2C access for the pH/ORP probes (added in a later version).
if getent group i2c >/dev/null; then usermod -aG i2c reefberry; fi

say "Copying the app to $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/src"
mkdir -p "$APP_DIR/src"
cp -r "$SRC_DIR/pyproject.toml" "$SRC_DIR/README.md" "$SRC_DIR/LICENSE" "$SRC_DIR/reefberry" "$APP_DIR/src/"

say "Installing Python packages (this can take a few minutes on a Pi)"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet "$APP_DIR/src"
# Always replace the app itself, even when the version number hasn't changed.
"$APP_DIR/venv/bin/pip" install --quiet --force-reinstall --no-deps "$APP_DIR/src"

say "Preparing the data folder $DATA_DIR"
mkdir -p "$DATA_DIR"
chown -R reefberry:reefberry "$DATA_DIR"
chmod 700 "$DATA_DIR"

say "Installing the reefberry command"
install -m 755 "$SRC_DIR/deploy/reefberry-cli" /usr/local/bin/reefberry

say "Installing the background service"
sed "s/^Environment=REEFBERRY_PORT=.*/Environment=REEFBERRY_PORT=$PORT/" \
  "$SRC_DIR/deploy/reefberry.service" > /etc/systemd/system/$SERVICE.service
systemctl daemon-reload
systemctl enable --quiet $SERVICE
systemctl restart $SERVICE

sleep 3
if ! systemctl is-active --quiet $SERVICE; then
  die "The service didn't start. See the log with:  sudo journalctl -u $SERVICE -n 50"
fi

# The owner account is created here, never on the web page, so nobody else on the
# network can claim a fresh install.
if [ -z "$(reefberry user list 2>/dev/null | grep -v '^No accounts')" ]; then
  say "Create your ReefBerry account"
  if [ -t 0 ] || [ -r /dev/tty ]; then
    echo "This is the login for the dashboard (not your Pi or TP-Link password)."
    until reefberry user add </dev/tty; do
      echo "Let's try that again."
    done
  else
    echo "No terminal available. Create the account later with:  sudo reefberry user add"
  fi
fi

if systemctl is-active --quiet $SERVICE; then
  HOST=$(hostname)
  IP=$(hostname -I | awk '{print $1}')
  SUFFIX=""; [ "$PORT" = "80" ] || SUFFIX=":$PORT"
  say "ReefBerry is running"
  echo "Open it in a browser on the same network:"
  echo "   http://$HOST.local$SUFFIX"
  echo "   http://$IP$SUFFIX"
  echo
  echo "Forgot your password?   sudo reefberry user reset-password"
  echo "Logs:                   sudo journalctl -u $SERVICE -f"
  echo "Restart:                sudo systemctl restart $SERVICE"
fi
