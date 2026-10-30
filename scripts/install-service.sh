#!/usr/bin/env bash
# Start Luna with the Raspberry Pi desktop, and again if she ever crashes (systemd user service).
#   bash scripts/install-service.sh           install and start now
#   bash scripts/install-service.sh --remove  stop and remove
# The desktop starts the service at login (~/.config/autostart/luna.desktop), passing its display
# to it. Logs: journalctl --user -u luna -f   (and ~/.luna/logs/luna.log)
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

if [ "${1:-}" = "--remove" ]; then
  systemctl --user stop luna.service 2>/dev/null || true
  rm -f "$UNITS/luna.service" "$AUTOSTART/luna.desktop"
  systemctl --user daemon-reload
  printf 'Luna no longer starts by herself. Start her with: bash scripts/run-pi.sh\n'
  exit 0
fi

mkdir -p "$UNITS" "$AUTOSTART"
cat > "$UNITS/luna.service" <<EOF
[Unit]
Description=Luna robot face
# Give up after 5 quick crashes in 2 minutes (then: bash scripts/run-pi.sh --no-plugins)
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$REPO/scripts/run-pi.sh
# Leaving with the Quit button exits normally and is not restarted.
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Nice=-2
EOF

cat > "$AUTOSTART/luna.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Luna
Comment=Start the Luna robot service with this desktop's display
Exec=sh -c 'systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR XDG_SESSION_TYPE; systemctl --user restart luna.service'
X-GNOME-Autostart-enabled=true
EOF

chmod +x scripts/run-pi.sh
systemctl --user daemon-reload
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR XDG_SESSION_TYPE 2>/dev/null || true
systemctl --user restart luna.service
printf 'Luna now starts with the desktop. Status: systemctl --user status luna\n'
