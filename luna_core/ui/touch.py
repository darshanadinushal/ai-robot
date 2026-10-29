"""Touch and mouse input: window pixels -> one tap on the logical canvas."""
import pygame

from ..geometry import Viewport

DOUBLE_TAP_SECONDS = .18
DOUBLE_TAP_PIXELS = 22


class TouchInput:
    """Turns MOUSEBUTTONDOWN / FINGERDOWN events into logical (x, y) taps.

    Some touchscreens emit both events for one tap; the second one is dropped.
    """

    def __init__(self):
        self.last = (-100.0, -10000.0, -10000.0)

    def tap(self, event, screen_size, t):
        """(x, y) on the 1280x720 canvas, or None (not a tap, outside it, or a duplicate)."""
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button != 1:
                return None
            px, py = event.pos
        elif event.type == pygame.FINGERDOWN:
            px, py = event.x * screen_size[0], event.y * screen_size[1]
        else:
            return None
        point = Viewport(*screen_size).to_logical(px, py)
        if point is None:
            return None
        x, y = point
        last_t, last_x, last_y = self.last
        if t - last_t < DOUBLE_TAP_SECONDS and abs(x - last_x) < DOUBLE_TAP_PIXELS and abs(y - last_y) < DOUBLE_TAP_PIXELS:
            return None
        self.last = (t, x, y)
        return x, y
