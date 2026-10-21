#!/usr/bin/env bash
set -euo pipefail
printf 'Installing AI Robot HUD requirements on Raspberry Pi OS...\n'
sudo apt update
sudo apt install -y python3 python3-pygame git
python3 -c 'import pygame; print("Pygame:", pygame.version.ver)'
printf '\nInstalled. Start from the Raspberry Pi desktop with: python3 main.py\n'
