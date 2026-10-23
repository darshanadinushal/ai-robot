#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
printf 'Installing AI Robot HUD requirements on Raspberry Pi OS...\n'
sudo apt update
sudo apt install -y python3 python3-pygame python3-venv libportaudio2 espeak-ng git
# Optional voice commands: venv that can still see the system pygame
python3 -m venv --system-site-packages .venv
.venv/bin/pip install vosk sounddevice
.venv/bin/python scripts/download-voice-model.py
python3 -c 'import pygame; print("Pygame:", pygame.version.ver)'
printf '\nInstalled. Start from the Raspberry Pi desktop with: python3 main.py\n'
