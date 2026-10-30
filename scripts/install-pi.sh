#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
printf 'Installing Luna on Raspberry Pi OS...\n'
sudo apt update
sudo apt install -y python3 python3-pygame python3-venv libportaudio2 espeak-ng git
# Optional voice commands: venv that can still see the system pygame
python3 -m venv --system-site-packages .venv
source scripts/deploy-common.sh
install_packages  # Luna (the `luna` command) + the [voice] and [ai] packages from pyproject.toml
.venv/bin/python scripts/download-voice-model.py en sv
python3 -c 'import pygame; print("Pygame:", pygame.version.ver)'
printf '\nInstalled. Start from the Raspberry Pi desktop with: bash scripts/run-pi.sh\n'
printf 'Start Luna with the desktop: bash scripts/install-service.sh\n'
