#!/usr/bin/env bash
# Make an echo-cancelling USB array (default: ReSpeaker XVF3800) Luna's default microphone and
# speaker, so her speech plays through the array (its echo-cancelling reference) and she can be
# interrupted by voice. Run from the desktop session: bash scripts/setup-audio-pi.sh [name-pattern]
# A music speaker (e.g. Soundcore on Bluetooth/AUX) stays separate and is not changed here.
set -euo pipefail
pattern="${1:-xvf3800|respeaker}"

if ! command -v pactl >/dev/null; then
  echo "pactl not found: sudo apt install pulseaudio-utils" >&2
  exit 1
fi

sink=$(pactl list short sinks | awk '{print $2}' | grep -iE "$pattern" | head -n 1 || true)
source=$(pactl list short sources | awk '{print $2}' | grep -v '\.monitor$' | grep -iE "$pattern" | head -n 1 || true)

if [ -z "$sink" ] || [ -z "$source" ]; then
  echo "No microphone and speaker matching '$pattern'. Is the array plugged in (USB)?" >&2
  echo "Devices seen:" >&2
  pactl list short sinks >&2
  pactl list short sources | grep -v '\.monitor' >&2 || true
  exit 1
fi

pactl set-default-sink "$sink"
pactl set-default-source "$source"
echo "Speaker:    $sink"
echo "Microphone: $source"
echo "Done (remembered after a restart). Restart Luna: Settings > Listening > Audio should say"
echo "'Echo cancelling'. Set the volume with: alsamixer (F6 to pick the array)."
