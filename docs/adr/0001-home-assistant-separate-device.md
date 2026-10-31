# ADR-0001: Home Assistant runs on a separate device

- Status: Accepted (2026-10-31)

## Context
Luna will control lights, devices and sensors through Home Assistant. Home Assistant could run
on the robot's Raspberry Pi 5 or on its own device (Home Assistant Green, another Pi, a NAS).

The robot Pi already runs the face, Vosk, Piper and the AI client, and will later run vision
and navigation. The robot moves, runs on a battery, restarts after upgrades and may be switched
off. A home must keep working when the robot is charging, upgrading or broken.

## Decision
Home Assistant runs on a separate, always-on device on the local network. Luna is a client:
- actions through Home Assistant's MCP Server (`/api/mcp`), live state through the WebSocket API;
- a non-admin long-lived token in `~/.luna.env` (never in the repository or logs);
- only entities exposed to Luna are reachable; the connection stays on the local network;
- the integration is a plugin (`luna-home`), not part of Luna Core.

## Consequences
- The robot's CPU, RAM and SD card are left for Luna; Home Assistant upgrades never stop Luna.
- Lights and automations keep working when Luna is off, and Luna degrades to "Home Assistant is
  not reachable" when it is down.
- One more device to buy and maintain (if none exists yet).
- Robot Setup gets a module that asks for the Home Assistant address and token and checks them.
