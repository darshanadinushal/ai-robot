"""UI geometry and animation math, with no pygame dependency."""
from dataclasses import dataclass
import math

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def blink_openness(elapsed: float, start: float, duration: float = 0.28) -> float:
    """Return eye openness from 0.08 (shut) to 1 (open)."""
    progress = (elapsed - start) / duration
    if progress < 0 or progress > 1:
        return 1.0
    return max(0.08, abs(math.cos(math.pi * progress)))


@dataclass(frozen=True)
class Viewport:
    screen_width: int
    screen_height: int
    logical_width: int = CANVAS_WIDTH
    logical_height: int = CANVAS_HEIGHT

    def __post_init__(self):
        if min(self.screen_width, self.screen_height, self.logical_width, self.logical_height) <= 0:
            raise ValueError("All viewport dimensions must be positive")

    @property
    def scale(self):
        return min(self.screen_width / self.logical_width, self.screen_height / self.logical_height)

    @property
    def rectangle(self):
        width = max(1, round(self.logical_width * self.scale))
        height = max(1, round(self.logical_height * self.scale))
        return ((self.screen_width - width) // 2,
                (self.screen_height - height) // 2, width, height)

    def to_logical(self, x: float, y: float):
        left, top, width, height = self.rectangle
        if not (left <= x < left + width and top <= y < top + height):
            return None
        return ((x - left) * self.logical_width / width,
                (y - top) * self.logical_height / height)
