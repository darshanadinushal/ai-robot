"""How often the screen is redrawn (pure, no pygame).

Smooth while something moves or happens; slower when nothing on the screen changes much, which
saves CPU (and heat) on the Raspberry Pi. LUNA_FPS / LUNA_IDLE_FPS override the rates.
"""
import os

ACTIVE_FPS = 30
IDLE_FPS = 15
QUIET_SECONDS = 3.0  # after a touch, key or spoken word, stay smooth this long


def _rate(env, name, default, highest):
    try:
        value = int(env.get(name, "") or default)
    except ValueError:
        return default
    return max(1, min(highest, value))


def frame_rates(env=None):
    """(active, idle) frames per second from LUNA_FPS (1-60) and LUNA_IDLE_FPS (1-active)."""
    env = os.environ if env is None else env
    active = _rate(env, "LUNA_FPS", ACTIVE_FPS, 60)
    return active, _rate(env, "LUNA_IDLE_FPS", min(IDLE_FPS, active), active)


def frame_rate(rates, busy, animated, quiet_for):
    """The rate for the next frame.

    `busy`: Luna listens, thinks or talks; `animated`: the page shows the moving face or wave;
    `quiet_for`: seconds since the last touch, key or heard word.
    """
    active, idle = rates
    return active if busy or animated or quiet_for < QUIET_SECONDS else idle
