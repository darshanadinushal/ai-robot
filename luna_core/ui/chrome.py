"""Screen chrome shared by every page: status bar, navigation bar, panels."""
import math
from datetime import datetime

import pygame

from .. import naming
from ..i18n import tr
from .draw import draw_arc, draw_text, line, mix, wrap_text
from .theme import (BACKGROUND, CONFIRM_NO_RECT, CONFIRM_RECT, CONFIRM_YES_RECT, H, MUTED,
                    QUIT_RECT, TEXT, W, nav_rects)


def nav_icon(surface, name, x, y, color=TEXT):
    if name == "Chat":
        pygame.draw.ellipse(surface, color, (x-21,y-16,42,30),3)
        pygame.draw.polygon(surface, color, [(x-11,y+10),(x-16,y+22),(x+1,y+13)])
        for offset in [-9, 0, 9]:
            pygame.draw.circle(surface, color, (x+offset,y-2), 2)
    elif name == "Home":
        pygame.draw.lines(surface, color, False, [(x-23,y),(x,y-22),(x+23,y)],4)
        pygame.draw.lines(surface, color, False,
                          [(x-16,y-4),(x-16,y+21),(x+16,y+21),(x+16,y-4)],3)
        pygame.draw.rect(surface, color, (x-5,y+7,10,14), 2)
    elif name == "Camera":
        pygame.draw.rect(surface, color, (x-24,y-13,48,34),3,border_radius=6)
        pygame.draw.polygon(surface, color, [(x-15,y-13),(x-9,y-22),(x+7,y-22),(x+13,y-13)])
        pygame.draw.circle(surface, color, (x,y+4), 10,3)
    elif name == "Settings":
        pygame.draw.circle(surface,color,(x,y+1),19,3)
        pygame.draw.circle(surface,color,(x,y+1),7,3)
        for i in range(8):
            a = i*math.tau/8
            line(surface,color,(x+20*math.cos(a),y+1+20*math.sin(a)),
                 (x+27*math.cos(a),y+1+27*math.sin(a)),4)
    else:  # a page without its own icon
        pygame.draw.rect(surface,color,(x-20,y-18,40,40),3,border_radius=9)
        pygame.draw.circle(surface,color,(x,y+2),5)


def nav_bar(surface, pages, page, fonts, accent):
    """`pages`: the robot's pages in order (ui.pages.Page); `page`: the shown page's name."""
    for item, rect in zip(pages, nav_rects(len(pages))):
        name = item.name
        active = name == page
        pygame.draw.rect(surface, (10,43,77) if active else (9,29,61), rect, border_radius=15)
        pygame.draw.rect(surface, accent if active else mix(accent, BACKGROUND, .66),
                         rect, 3 if active else 2, border_radius=15)
        if active:
            line(surface, accent, (rect.left+24,rect.top+2), (rect.right-24,rect.top+2), 3)
        item.draw_icon(surface,rect.centerx,rect.top+34,TEXT)
        draw_text(surface,tr(name),fonts['nav'],TEXT if active else MUTED,
                  center=(rect.centerx,rect.top+73))


def wifi_icon(surface, x, y, color):
    for radius in (7,14,21):
        draw_arc(surface,color,x,y,radius,math.pi/4,3*math.pi/4,2)
    pygame.draw.circle(surface,color,(x,y+6),3)


def status_bar(surface, fonts, accent):
    draw_text(surface,datetime.now().strftime('%H:%M'), fonts['header'], TEXT,
              topleft=(42,26))
    draw_text(surface,naming.name.upper()+' 1.0', fonts['header'], MUTED,center=(640,43))
    color = (115,153,187)
    wifi_icon(surface,994,42,color)
    # Visual icons only: not network, audio or battery readings.
    pygame.draw.rect(surface,color,(1025,36,11,15),2)
    pygame.draw.polygon(surface,color,[(1036,36),(1046,31),(1046,56),(1036,51)])
    line(surface,color,(1068,26),(1068,58),2)
    line(surface,color,(1068,26),(1080,38),2)
    line(surface,color,(1080,38),(1065,55),2)
    line(surface,color,(1065,29),(1080,45),2)
    pygame.draw.rect(surface,color,(1104,33,40,21),2,border_radius=3)
    pygame.draw.rect(surface,color,(1144,39,4,9))
    draw_text(surface,'--',fonts['mini'],color,center=(1123,43))
    pygame.draw.rect(surface,(10,38,75),QUIT_RECT,border_radius=6)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.5),QUIT_RECT,2,border_radius=6)
    line(surface,TEXT,(1227,37),(1243,53),2)
    line(surface,TEXT,(1243,37),(1227,53),2)
    draw_text(surface,'DEMO',fonts['mini'],MUTED,topleft=(1160,34))


def panel_base(surface, accent):
    box = pygame.Rect(169,131,942,405)
    pygame.draw.rect(surface,(6,26,52),box,border_radius=24)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.38),box,2,border_radius=24)


_SHADE = []


def confirm_dialog(surface, fonts, accent, confirmation):
    """Luna's open Yes/No question over the page (taps: CONFIRM_YES_RECT / CONFIRM_NO_RECT)."""
    if not _SHADE:
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        _SHADE.append(shade)
    surface.blit(_SHADE[0], (0, 0))
    box = CONFIRM_RECT
    pygame.draw.rect(surface, (6, 22, 46), box, border_radius=18)
    pygame.draw.rect(surface, accent, box, 2, border_radius=18)
    draw_text(surface, tr("Please confirm"), fonts["heading"], TEXT, center=(box.centerx, box.top + 42))
    y = box.top + 92
    for item in confirmation.items:
        text = tr(item.label) + (": " + item.detail if item.detail else "")
        for row in wrap_text(fonts["body"], text, box.width - 80, 2 if len(confirmation.items) < 3 else 1):
            draw_text(surface, row, fonts["body"], TEXT, center=(box.centerx, y))
            y += 32
        y += 6
    hint = "Tap Yes to confirm." if confirmation.touch else "Say yes or no, or tap."
    draw_text(surface, tr(hint), fonts["small"], MUTED, center=(box.centerx, CONFIRM_YES_RECT.top - 24))
    for rect, label, strong in ((CONFIRM_YES_RECT, "Yes", True), (CONFIRM_NO_RECT, "No", False)):
        pygame.draw.rect(surface, (10, 48, 86) if strong else (8, 30, 58), rect, border_radius=12)
        pygame.draw.rect(surface, accent if strong else mix(accent, BACKGROUND, .45), rect,
                         2, border_radius=12)
        draw_text(surface, tr(label), fonts["heading"], TEXT, center=rect.center)
