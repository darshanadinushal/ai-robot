"""Colours, frame rate and the fixed rectangles of the 1280x720 logical canvas.

Drawing and touch handling use the same rectangles, so a tap always matches what is shown.
"""
import pygame

from ..geometry import CANVAS_WIDTH as W, CANVAS_HEIGHT as H
from ..settings import SECTIONS

FPS = 30
BACKGROUND = (4, 11, 28)
TEXT = (223, 247, 255)
MUTED = (111, 151, 187)
FAINT = (45, 95, 143)
QUIT_RECT = pygame.Rect(1214, 23, 43, 43)
# Settings page: section menu on the left, the selected section on the right.
SIDEBAR_RECT = pygame.Rect(22, 86, 280, 498)
SECTION_RECTS = [pygame.Rect(36, 98 + i * 68, 252, 58) for i in range(len(SECTIONS))]
CONTENT_RECT = pygame.Rect(316, 86, 942, 498)
CONTENT_CLIP = pygame.Rect(330, 98, 900, 474)
LABEL_X = 362
CONTROL_X = 545
CONTROL_RIGHT = 1205
CHAT_RECT = pygame.Rect(485, 460, 310, 63)
WARNING = (255, 185, 67)
NAV_TOP = 597
NAV_HEIGHT = 93
# Yes/No question (tasks/confirm.py), drawn over any page.
CONFIRM_RECT = pygame.Rect(340, 150, 600, 360)
CONFIRM_YES_RECT = pygame.Rect(380, 424, 250, 62)
CONFIRM_NO_RECT = pygame.Rect(650, 424, 250, 62)


def nav_rects(count):
    """One navigation button per page along the bottom (4 pages: the original layout)."""
    if count <= 4:
        left = 216 + (4 - count) * 108
        return [pygame.Rect(left + i * 216, NAV_TOP, 199, NAV_HEIGHT) for i in range(count)]
    step = (W - 80) / count
    return [pygame.Rect(round(40 + i * step), NAV_TOP, round(step) - 17, NAV_HEIGHT)
            for i in range(count)]


__all__ = ["W", "H", "FPS", "BACKGROUND", "TEXT", "MUTED", "FAINT", "WARNING", "QUIT_RECT",
           "SIDEBAR_RECT", "SECTION_RECTS", "CONTENT_RECT", "CONTENT_CLIP", "LABEL_X", "CONTROL_X",
           "CONTROL_RIGHT", "CHAT_RECT", "NAV_TOP", "NAV_HEIGHT", "CONFIRM_RECT", "CONFIRM_YES_RECT",
           "CONFIRM_NO_RECT", "nav_rects"]
