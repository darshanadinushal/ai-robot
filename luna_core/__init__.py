"""Luna Core: a talking robot face for Raspberry Pi 5 and desktop development.

Apps use one entry point::

    from luna_core import Robot
    Robot().run()

Importing luna_core does not import pygame; that happens when a Robot runs.
"""
from .robot import Robot

__version__ = "1.0.1"
__all__ = ["Robot", "__version__"]
