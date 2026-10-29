"""Pages: what fills the screen above the navigation bar.

Luna's own pages are Chat, Home, Camera and Settings. An app adds its own page without
editing the core: subclass Page and pass it to Robot.add_page (or RobotHUD.add_page).
Every page gets a navigation button; the buttons share the bottom bar (theme.nav_rects).

A page draws on the 1280x720 logical canvas only, on the UI thread (never from a worker).
"""
import math

import pygame

from ..i18n import tr
from .chrome import nav_icon, panel_base
from .draw import draw_text, line, mix
from .theme import BACKGROUND, CHAT_RECT, MUTED, TEXT


class Page:
    """Base class for a page. `hud` is the running robot (app.RobotHUD)."""

    name = 'Page'  # unique; shown on the navigation button (translated with tr if known)
    chrome = True  # draw the sci-fi frame behind the page
    plugin = None  # the id of the plugin that added it (set by Luna)

    def draw_icon(self, surface, x, y, color):
        """The navigation-button icon, centred on (x, y)."""
        nav_icon(surface, self.name, x, y, color)

    def draw(self, hud, surface, t):
        """Draw the page; called every frame while it is shown."""

    def touch(self, hud, x, y, t):
        """A tap on the page (logical coordinates); the navigation bar is handled already."""

    def key(self, hud, event, t):
        """A pygame KEYDOWN while the page is shown; return True when the page used it."""
        return False


class HomePage(Page):
    name = 'Home'

    def draw(self, hud, surface, t):
        hud.draw_home(t)

    def touch(self, hud, x, y, t):
        hud.touch_home(x, y, t)


class ChatPage(Page):
    name = 'Chat'

    def draw(self, hud, surface, t):
        chat_page(surface, hud.fonts, hud.accent, t, hud.chat_active)

    def touch(self, hud, x, y, t):
        if CHAT_RECT.collidepoint(x, y):
            hud.chat_active = not hud.chat_active


class CameraPage(Page):
    name = 'Camera'

    def draw(self, hud, surface, t):
        camera_page(surface, hud.fonts, hud.accent, t)


class SettingsPage(Page):
    name = 'Settings'
    chrome = False

    def draw(self, hud, surface, t):
        hud.draw_settings()

    def touch(self, hud, x, y, t):
        hud.touch_settings(x, y)

    def key(self, hud, event, t):
        return hud.settings_key(event)


def default_pages():
    """Luna's own pages, in navigation-bar order."""
    return [ChatPage(), HomePage(), CameraPage(), SettingsPage()]


NAV_NAMES = tuple(page.name for page in default_pages())


def chat_page(surface, fonts, accent, t, chat_active):
    panel_base(surface,accent)
    draw_text(surface,tr('AI VOICE ASSISTANT'),fonts['title'],TEXT,center=(640,205))
    draw_text(surface,tr('Microphone / AI integration is not connected yet.'),fonts['body'],MUTED,
              center=(640,251))
    bar_count=43
    for i in range(bar_count):
        x=375+i*12
        height = 8 + (65*abs(math.sin(t*4 + i*.48)*math.cos(i*.15)) if chat_active else
                      14*abs(math.sin(t*.6+i*.25)))
        col=mix(accent,BACKGROUND,.08 + .58*abs(i-21)/21)
        pygame.draw.line(surface,col,(x,349-height/2),(x,349+height/2),6)
    pygame.draw.rect(surface,(11,49,84),CHAT_RECT,border_radius=13)
    pygame.draw.rect(surface,accent,CHAT_RECT,2,border_radius=13)
    draw_text(surface,tr('Stop demo' if chat_active else 'Start waveform demo'),fonts['body'],TEXT,
              center=CHAT_RECT.center)
    draw_text(surface,tr('Waveform is simulated: no microphone recording.'),fonts['small'],MUTED,
              center=(640,431))


def camera_page(surface, fonts, accent, t):
    panel_base(surface,accent)
    draw_text(surface,tr('CAMERA'),fonts['title'],TEXT,center=(640,182))
    preview=pygame.Rect(359,218,562,236)
    pygame.draw.rect(surface,(3,14,31),preview,border_radius=6)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.38),preview,2,border_radius=6)
    for i in range(1,6):
        x=preview.x + i*preview.width//6
        line(surface,(12,38,66),(x,preview.top),(x,preview.bottom),1)
    for i in range(1,4):
        y=preview.y + i*preview.height//4
        line(surface,(12,38,66),(preview.left,y),(preview.right,y),1)
    for x,dx in [(preview.left+20,1),(preview.right-20,-1)]:
        for y,dy in [(preview.top+20,1),(preview.bottom-20,-1)]:
            line(surface,accent,(x,y),(x+28*dx,y),3)
            line(surface,accent,(x,y),(x,y+23*dy),3)
    draw_text(surface,tr('NO CAMERA FEED'),fonts['body'],TEXT,center=preview.center)
    draw_text(surface,tr('This is a placeholder, not a live image.'),fonts['small'],MUTED,
              center=(640,493))
