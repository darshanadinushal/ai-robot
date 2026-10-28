"""Fullscreen, touch-friendly robot HUD built with pygame only.

All artwork is drawn procedurally; no proprietary icons, images or fonts needed.
"""
import argparse
import math
import os
import queue
import random
import sys
import time
from datetime import datetime

try:
    import pygame
except ImportError as exc:
    raise SystemExit(
        "Pygame isn't installed. On Raspberry Pi: sudo apt install -y python3-pygame\n"
        "On Windows: py -m pip install -r requirements.txt"
    ) from exc

from .geometry import CANVAS_WIDTH as W, CANVAS_HEIGHT as H, Viewport, blink_openness, clamp
from .commands import parse_command, reply_for, route, utterance_end
from .listening import UtteranceAssembler, continue_seconds
from . import i18n, languages, speech
from .i18n import tr
from .ai import AIWorker, ai_configured
from .assistant_core.conversation import Conversation
from .assistant_core.context import settings_from_env
from .assistant_core.events import Accepted, Act, Log, Mood, Speak, StopSpeech
from .assistant_core.expression import face_motion
from .assistant_core.memory import ERRORS as MEMORY_ERRORS, MemoryStore
from .transcription import PREROLL_SECONDS, AudioRing, OnlineListening
from .voice import VoiceListener
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS, read_preferences, write_preferences
from .settings import SECTIONS, apply_choice
from .voices import LANGUAGES as VOICE_LANGUAGES, get_voice, sample_text, voices_for

FPS = 30
BACKGROUND = (4, 11, 28)
TEXT = (223, 247, 255)
MUTED = (111, 151, 187)
FAINT = (45, 95, 143)
NAV_NAMES = ("Chat", "Home", "Camera", "Settings")
NAV_RECTS = [pygame.Rect(216 + i * 216, 597, 199, 93) for i in range(4)]
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


def draw_text(surface, text, font, color, center=None, topleft=None):
    glyphs = font.render(text, True, color)
    if center is not None:
        surface.blit(glyphs, glyphs.get_rect(center=center))
    else:
        surface.blit(glyphs, topleft)


def wrap_text(font, text, width, max_lines=2):
    """Split text into at most `max_lines` lines that fit `width`; adds '...' if cut."""
    lines, current = [], ''
    words = str(text).split()
    for word in words:
        trial = (current + ' ' + word).strip()
        if font.size(trial)[0] <= width:
            current = trial
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            lines[-1] = lines[-1].rstrip('.,;:') + '...'
            return lines
    if current:
        lines.append(current)
    return lines


def line(surface, color, a, b, thickness=2):
    pygame.draw.line(surface, color, (int(a[0]), int(a[1])),
                     (int(b[0]), int(b[1])), thickness)


def draw_frame(surface, rect, color, thickness=2, cutoff=16):
    x, y, width, height = rect
    verts = [(x + cutoff, y), (x + width - cutoff, y),
             (x + width, y + cutoff), (x + width, y + height - cutoff),
             (x + width - cutoff, y + height), (x + cutoff, y + height),
             (x, y + height - cutoff), (x, y + cutoff)]
    pygame.draw.lines(surface, color, True, verts, thickness)


def draw_arc(surface, color, cx, cy, radius, start, end, thickness=3):
    pygame.draw.arc(surface, color,
                    pygame.Rect(cx-radius, cy-radius, 2*radius, 2*radius),
                    start, end, thickness)


def mix(a, b, weight):
    return tuple(max(0, min(255, round(i * (1 - weight) + j * weight))) for i, j in zip(a, b))


def background_surface():
    surface = pygame.Surface((W, H)).convert()
    for y in range(H):
        r = y / H
        line(surface, (int(3 + r*5), int(11 + r*15), int(29 + r*23)), (0, y), (W, y))
    for x in range(31, W, 44):
        for y in range(98, 573, 44):
            pygame.draw.circle(surface, (14, 37, 69), (x, y), 1)
    return surface


def draw_scifi_chrome(surface, accent, t):
    dim = mix(accent, BACKGROUND, .73)
    draw_frame(surface, (22, 80, 1236, 480), dim, 2)
    draw_frame(surface, (35, 96, 1210, 452), (12, 40, 79), 1)
    for x, d in [(35, 1), (1245, -1)]:
        for y, dy in [(117, -1), (521, 1)]:
            line(surface, accent, (x,y), (x+24*d,y+24*dy), 3)
        line(surface, accent, (x,117), (x,158), 3)
        line(surface, accent, (x,479), (x,521), 3)
    for x in [74, 1136]:
        for i in range(10):
            yy = 237 + i * 17
            col = mix(accent, BACKGROUND, .35 if (i + int(t*3)) % 6 == 0 else .76)
            line(surface, col, (x,yy), (x+31,yy), 2)
    line(surface, dim, (33, 575), (1247,575))


def glow_disc(radius, color):
    s = pygame.Surface((radius*2 + 8, radius*2 + 8), pygame.SRCALPHA)
    center = (radius+4, radius+4)
    # Subtle coloured halo, transparent outside the ring.
    for step in range(24, 0, -1):
        rr = radius - step*2
        if rr > 0:
            pygame.draw.circle(s, (*color, 4 + (24-step)//2), center, rr)
    return s


def draw_eye(surface, cx, cy, t, gaze_x, gaze_y, openness, color, glow, scale=1.0, side=-1, mood='neutral'):
    # Eye contents are drawn on a 340px layer, then vertically scaled to blink.
    size = 352
    eye = pygame.Surface((size, size), pygame.SRCALPHA)
    x = y = size // 2
    eye.blit(glow, (x - glow.get_width()//2, y - glow.get_height()//2))
    pygame.draw.circle(eye, (2, 19, 44), (x,y), 144)
    pygame.draw.circle(eye, mix(color, (0, 13, 36), .58), (x,y), 145, 3)
    pygame.draw.circle(eye, (3, 32, 69), (x,y), 127)

    # Rotating HUD arcs, precision dashes, layered iris.
    for i in range(5):
        a = (t * (.12 if i % 2 else -.09)) + i * math.tau/5
        draw_arc(eye, color if i % 2 else mix(color, (0, 29, 88), .35), x, y, 153,
                 a, a + .58, 4 if i == 0 else 2)
    for i in range(64):
        a = math.tau * i / 64
        active = (i + int(t*6)) % 17 < 5
        radius = 114 if i % 8 else 107
        outer = 127 if i % 8 else 133
        col = mix(color, (4,31,67), .42 if active else .82)
        p1 = (x + radius*math.cos(a), y + radius*math.sin(a))
        p2 = (x + outer*math.cos(a), y + outer*math.sin(a))
        line(eye, col, p1, p2, 3 if active else 2)
    pygame.draw.circle(eye, mix(color, (4,26,58), .3), (x,y), 104, 2)
    pygame.draw.circle(eye, color, (x,y), 95, 4)
    pygame.draw.circle(eye, mix(color, (0,25,78), .67), (x,y), 88)

    ix = x + int(gaze_x * 21)
    iy = y + int(gaze_y * 17)
    for r, col in [(74, mix(color, (7,45,107), .56)),
                   (67, mix(color, (5,47,133), .33)),
                   (57, mix(color, (5,52,115), .15)),
                   (49, mix(color, (0,26,91), .63))]:
        pygame.draw.circle(eye, col, (ix, iy), r)
    for k in range(32):
        a = k*math.tau/32 + t*.1
        p1 = (ix+53*math.cos(a), iy+53*math.sin(a))
        p2 = (ix+67*math.cos(a), iy+67*math.sin(a))
        line(eye, mix(color, TEXT, .26), p1, p2, 2)
    pygame.draw.circle(eye, (0, 14, 42), (ix, iy), 38)
    pygame.draw.circle(eye, (4, 35, 75), (ix+7,iy+6), 24)
    pygame.draw.circle(eye, (253,253,255), (ix-21,iy-27), 16)
    pygame.draw.circle(eye, (164,244,255), (ix+24,iy+19), 6)

    if mood == 'happy':
        pygame.draw.circle(eye, (0,0,0,0), (x, y+215), 150)
    elif mood in ('sad', 'angry'):
        inner_x = x - side*size//2
        outer_x = x + side*size//2
        yi, yo = (y+30, y-130) if mood == 'angry' else (y-130, y+30)
        pygame.draw.polygon(eye, (0,0,0,0), [(inner_x,yi),(outer_x,yo),(outer_x,0),(inner_x,0)])

    if openness < .14:
        hw = 111*scale
        line(surface, mix(color, BACKGROUND, .55), (cx-hw,cy), (cx+hw,cy), 11)
        line(surface, color, (cx-hw*.88,cy), (cx+hw*.88,cy), 4)
    else:
        h = max(2, int(size * openness))
        sw, sh = int(size*scale), int(h*scale)
        surface.blit(pygame.transform.smoothscale(eye, (sw,sh)),
                     (cx-sw//2, cy-sh//2))


def draw_mic(surface, accent, cx, cy, t):
    glow = pygame.Surface((160,160), pygame.SRCALPHA)
    for r in range(70, 0, -3):
        pygame.draw.circle(glow, (*accent, 3 + (70-r)//5), (80,80), r)
    surface.blit(glow, (cx-80, cy-80))
    pygame.draw.rect(surface, accent, (cx-14,cy-34,28,50), 4, border_radius=14)
    pygame.draw.arc(surface, accent, (cx-27,cy-28,54,50), math.pi, math.tau, 4)
    line(surface, accent, (cx,cy+22), (cx,cy+36), 4)
    pygame.draw.ellipse(surface, accent, (cx-17,cy+33,34,10), 2)


def draw_wave(surface, t, accent, amp=1.0):
    # Mesh of sine ribbons: many offset curves share lobed envelopes and cross each other.
    other = (226, 70, 255)
    cy, x0, x1, step = 360, 40, 1240, 10
    layer = pygame.Surface((1280, 420), pygame.SRCALPHA)
    ly = 210
    xs = list(range(x0, x1 + 1, step))
    for wave in range(2):
        for k in range(26):
            frac = k / 25 * 2 - 1
            pts = []
            for x in xs:
                u = (x - x0) / (x1 - x0)
                env = (math.sin(u*math.pi*4 + t*1.3 + wave*1.7)**2
                       * (.7 + .3*math.sin(u*math.pi*2 - t*.7)))
                amp_px = 215 * amp * env * math.sin(math.pi*(.5 + .5*frac))
                y = ly + frac*amp_px*(.6 + .4*wave) * math.sin(u*math.pi*9 + t*2.2 + k*.05)
                pts.append((x, y))
            for i in range(len(pts)-1):
                u = i / len(pts)
                col = mix(mix(accent, (235,250,255), .35), other, u)
                a = 110 if wave == 0 else 170
                pygame.draw.line(layer, (*col, a), pts[i], pts[i+1], 1)
    surface.blit(layer, (0, cy - ly))


def draw_wave_bars(surface, t, accent, amp=1.0):
    other = (226, 70, 255)
    n = 61
    for i in range(n):
        u = i / (n - 1)
        x = 130 + i * 17
        env = .35 + .65 * abs(math.sin(u*math.pi*3 + t*1.4) * math.cos(u*math.pi*1.3 - t*.6))
        h = 8 + 190 * amp * env * math.sin(math.pi * u) ** .6
        col = mix(accent, other, u)
        line(surface, mix(col, BACKGROUND, .55), (x, 360 - h/2 - 6), (x, 360 + h/2 + 6), 9)
        line(surface, col, (x, 360 - h/2), (x, 360 + h/2), 5)


def draw_wave_circle(surface, t, accent, amp=1.0, cx=640, cy=330):
    other = (226, 70, 255)
    for k in range(16):
        base = 82 + k * 6
        pts = []
        for j in range(121):
            a = math.tau * j / 120
            r = base + amp * (10 + k*1.6) * math.sin(a*5 + t*1.6 + k*.25) * math.sin(a*3 - t*.9)
            pts.append((cx + r*math.cos(a), cy + r*math.sin(a)))
        col = mix(accent, other, k / 15)
        pygame.draw.lines(surface, mix(col, BACKGROUND, .15), True, pts, 1)


def smile(surface, t, accent, y=474, mood='neutral', talking=False):
    if talking:
        h = 8 + int(20 * abs(math.sin(t * 9.0) * math.sin(t * 3.7 + 1)))
        rect = pygame.Rect(640 - 34, y + 8 - h // 2, 68, h)
        pygame.draw.ellipse(surface, mix(accent, BACKGROUND, .76), rect.inflate(12, 12))
        pygame.draw.ellipse(surface, accent, rect, 4)
        return
    if mood == 'surprised':
        pygame.draw.circle(surface, mix(accent, BACKGROUND, .76), (640, y+8), 24, 12)
        pygame.draw.circle(surface, accent, (640, y+8), 24, 4)
        return
    if mood == 'thinking':
        for i in range(3):
            phase = (t * 2.5 - i * .35) % 1.0
            r = 5 + int(3 * math.sin(math.pi * phase))
            pygame.draw.circle(surface, mix(accent, BACKGROUND, .2 + .5 * (1 - phase)),
                               (616 + i * 24, y + 10), r)
        return
    curve = {'happy': 36, 'sad': -20, 'angry': -6, 'sleepy': 3}.get(mood, 17)
    dots=[]
    for i in range(41):
        a = i/40
        dots.append((int(640 - 46 + 92*a),
                     int(y + curve*math.sin(math.pi*a) + 1.5*math.sin(t*2))))
    pygame.draw.lines(surface, mix(accent, BACKGROUND, .76), False, dots, 13)
    pygame.draw.lines(surface, accent, False, dots, 5)


def nav_icon(surface, name, x, y, accent):
    color = TEXT
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
    else:
        pygame.draw.circle(surface,color,(x,y+1),19,3)
        pygame.draw.circle(surface,color,(x,y+1),7,3)
        for i in range(8):
            a = i*math.tau/8
            line(surface,color,(x+20*math.cos(a),y+1+20*math.sin(a)),
                 (x+27*math.cos(a),y+1+27*math.sin(a)),4)


def nav_bar(surface, page, fonts, accent):
    for name, rect in zip(NAV_NAMES, NAV_RECTS):
        active = name == page
        pygame.draw.rect(surface, (10,43,77) if active else (9,29,61), rect, border_radius=15)
        pygame.draw.rect(surface, accent if active else mix(accent, BACKGROUND, .66),
                         rect, 3 if active else 2, border_radius=15)
        if active:
            line(surface, accent, (rect.left+24,rect.top+2), (rect.right-24,rect.top+2), 3)
        nav_icon(surface,name,rect.centerx,rect.top+34,accent)
        draw_text(surface,tr(name),fonts['nav'],TEXT if active else MUTED,
                  center=(rect.centerx,rect.top+73))


def wifi_icon(surface, x, y, color):
    for radius in (7,14,21):
        draw_arc(surface,color,x,y,radius,math.pi/4,3*math.pi/4,2)
    pygame.draw.circle(surface,color,(x,y+6),3)


def status_bar(surface, fonts, accent):
    draw_text(surface,datetime.now().strftime('%H:%M'), fonts['header'], TEXT,
              topleft=(42,26))
    draw_text(surface,'LUNA 1.0', fonts['header'], MUTED,center=(640,43))
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


def section_icon(surface, name, x, y, color):
    """Small line icons for the Settings sections."""
    if name == 'Display & Theme':
        pygame.draw.circle(surface,color,(x,y),15,2)
        for dx,dy,col in ((-6,-6,(34,216,255)),(4,-8,(184,113,255)),(8,2,(67,235,179))):
            pygame.draw.circle(surface,col,(x+dx,y+dy),3)
        pygame.draw.circle(surface,color,(x-5,y+6),4)
    elif name == 'Language':
        pygame.draw.circle(surface,color,(x,y),15,2)
        pygame.draw.ellipse(surface,color,(x-7,y-15,14,30),2)
        line(surface,color,(x-15,y),(x+15,y),2)
        line(surface,color,(x-12,y-8),(x+12,y-8),1)
        line(surface,color,(x-12,y+8),(x+12,y+8),1)
    elif name == 'Voice':
        pygame.draw.rect(surface,color,(x-6,y-16,12,21),2,border_radius=6)
        draw_arc(surface,color,x,y-3,12,math.pi,2*math.pi,2)
        line(surface,color,(x,y+9),(x,y+16),2)
        line(surface,color,(x-7,y+16),(x+7,y+16),2)
    elif name == 'Listening':
        pygame.draw.circle(surface,color,(x-6,y),4)
        for radius in (9,15):
            draw_arc(surface,color,x-6,y,radius,-math.pi/3,math.pi/3,2)
    elif name == 'Memory':
        pygame.draw.rect(surface,color,(x-12,y-15,24,30),2,border_radius=4)
        for dy in (-7,0,7):
            line(surface,color,(x-6,y+dy),(x+6,y+dy),2)
    elif name == 'Animations':
        pygame.draw.ellipse(surface,color,(x-16,y-9,32,18),2)
        pygame.draw.circle(surface,color,(x,y),5)
        for dx in (-9,0,9):
            line(surface,color,(x+dx,y-11),(x+dx*1.2,y-16),2)
    else:
        pygame.draw.circle(surface,color,(x,y),15,2)
        pygame.draw.circle(surface,color,(x,y-7),2)
        line(surface,color,(x,y-2),(x,y+8),3)


def settings_sidebar(surface, fonts, accent, active):
    pygame.draw.rect(surface,(6,26,52),SIDEBAR_RECT,border_radius=18)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.45),SIDEBAR_RECT,2,border_radius=18)
    for i,(name,rect) in enumerate(zip(SECTIONS,SECTION_RECTS)):
        selected = i==active
        if selected:
            pygame.draw.rect(surface,(11,49,84),rect,border_radius=12)
            pygame.draw.rect(surface,mix(accent,BACKGROUND,.6),rect.inflate(6,6),2,border_radius=14)
            pygame.draw.rect(surface,accent,rect,2,border_radius=12)
        else:
            line(surface,(20,52,88),(rect.left+6,rect.bottom+5),(rect.right-6,rect.bottom+5),1)
        section_icon(surface,name,rect.left+30,rect.centery,TEXT if selected else MUTED)
        draw_text(surface,tr(name),fonts['side'],TEXT if selected else MUTED,
                  topleft=(rect.left+58,rect.centery-13))
        chevron=MUTED if not selected else TEXT
        line(surface,chevron,(rect.right-24,rect.centery-7),(rect.right-17,rect.centery),2)
        line(surface,chevron,(rect.right-17,rect.centery),(rect.right-24,rect.centery+7),2)


def control_rects(y, count, height, max_width, gap=14, per_row=None):
    """`count` buttons starting at CONTROL_X, each at most `max_width` wide.

    With `per_row`, longer lists wrap onto more rows (see rows_height)."""
    columns = min(count, per_row or count) or 1
    width = min(max_width, (CONTROL_RIGHT-CONTROL_X-gap*(columns-1))//columns)
    return [pygame.Rect(CONTROL_X+(i%columns)*(width+gap), y+(i//columns)*(height+gap), width, height)
            for i in range(count)]


CHOICES_PER_ROW = 4


def extra_rows_height(count, height=46, gap=14, per_row=CHOICES_PER_ROW):
    """Extra height used when `count` choices wrap onto more than one row."""
    return max(0, (count-1)//per_row)*(height+gap)


def add_hit(hits, rect, action):
    visible = rect.clip(CONTENT_CLIP)
    if visible.width > 0 and visible.height > 0:
        hits.append((visible, action))


def choice_button(surface, fonts, accent, rect, text, selected, hits, action, swatch=None):
    pygame.draw.rect(surface,(11,49,84) if selected else (8,32,62),rect,border_radius=10)
    if selected:
        pygame.draw.rect(surface,mix(accent,BACKGROUND,.6),rect.inflate(6,6),2,border_radius=12)
    pygame.draw.rect(surface,accent if selected else (33,73,109),rect,2 if selected else 1,
                     border_radius=10)
    if swatch is not None:
        pygame.draw.circle(surface,swatch,(rect.centerx,rect.top+22),11)
        draw_text(surface,text,fonts['small'],TEXT if selected else MUTED,
                  center=(rect.centerx,rect.bottom-18))
    else:
        draw_text(surface,text,fonts['small'],TEXT if selected else MUTED,center=rect.center)
    add_hit(hits,rect,action)


def section_header(surface, fonts, name, y, subtitle=None):
    section_icon(surface,name,LABEL_X+8,y+32,TEXT)
    draw_text(surface,tr(name),fonts['heading'],TEXT,topleft=(LABEL_X+42,y+14))
    if subtitle:
        draw_text(surface,tr(subtitle),fonts['mini'],MUTED,topleft=(LABEL_X+43,y+52))
    line(surface,(20,52,88),(CONTENT_CLIP.left+10,y+78),(CONTENT_CLIP.right-10,y+78),1)


def row_label(surface, fonts, text, center_y):
    draw_text(surface,tr(text),fonts['small'],MUTED,topleft=(LABEL_X,center_y-12))


def voice_status(voice):
    """(state, translated status line) for the selected voice."""
    if not speech.enabled:
        return 'off',tr('Speech is off (--no-speech)')
    state,status = speech.voice_state()
    if state=='natural':
        return state,tr('Natural voice: {label}',label=voice.label)
    if state=='loading':
        return state,tr('Loading {label}...',label=voice.label)
    return state,tr('Basic voice: {reason}',reason=status.partition(': ')[2] or status)


def mic_status(status):
    prefix = 'microphone error: '
    if status.startswith(prefix):
        return tr(prefix)+status[len(prefix):]
    return tr(status)


def listening_status(listener, language):
    """(state, translated line) for what Luna listens with; the state picks the dot colour."""
    name = tr(languages.get(language).name)
    if listener is None:
        return 'basic',tr('Microphone off')
    if listener.status!='listening':
        state = 'loading' if listener.status in ('starting','loading') else 'basic'
        return state,mic_status(listener.status)
    if listener.active_language!=language:
        return 'basic',tr('English - the {language} listening model is not installed',
                          language=name)
    return 'natural',tr('Listening in {language}',language=name)


def voice_type_label(voice, choices):
    """FEMALE / MALE, plus the voice name when a language has several voices of that type."""
    label = tr(voice.gender.upper())
    if sum(other.gender==voice.gender for other in choices)>1 and voice.name:
        label += ' - '+voice.name.upper()
    return label


def status_row(surface, fonts, label, y, status):
    row_label(surface,fonts,label,y)
    state,text = status
    dot = {'natural':(67,235,179),'loading':(255,185,67)}.get(state,(255,110,110))
    pygame.draw.circle(surface,dot,(CONTROL_X+9,y),7)
    line = wrap_text(fonts['small'],text,CONTROL_RIGHT-CONTROL_X-30,1)[0]
    draw_text(surface,line,fonts['small'],TEXT,topleft=(CONTROL_X+26,y-12))


def online_status(prefs, ai_on, mic_on):
    """(state, translated line) for online listening in Settings."""
    if not prefs.get('online_listening'):
        return 'basic',tr('Off - Luna hears everything offline')
    if not ai_on:
        return 'basic',tr('Needs the AI (an OpenAI API key)')
    if not mic_on:
        return 'basic',tr('Microphone off')
    return 'natural',tr('On - questions to Luna are transcribed online')


def toggle(surface, fonts, accent, on, y, hits, action):
    track = pygame.Rect(CONTROL_X,y,76,38)
    pygame.draw.rect(surface,accent if on else (24,52,84),track,border_radius=19)
    pygame.draw.circle(surface,TEXT,(track.right-19 if on else track.left+19,track.centery),15)
    draw_text(surface,tr('ON' if on else 'OFF'),fonts['small'],TEXT,
              topleft=(track.right+16,track.centery-12))
    add_hit(hits,pygame.Rect(track.left-10,track.top-10,150,58),action)


MEMORY_ROWS = 4  # memories per page in Settings > Memory


def memory_pages(count):
    return max(1,-(-count//MEMORY_ROWS))


def memory_section(surface, fonts, accent, prefs, y, memory, hits):
    """Settings > Memory: the switch, "About you", the saved facts (newest first), delete buttons.

    `memory`: (items, page, confirm, error); an item is (text, delete action).
    """
    facts,page,confirm,error = memory
    row_label(surface,fonts,'Memory',y+130)
    toggle(surface,fonts,accent,prefs['memory'],y+111,hits,('memory',None))
    if error:
        note = tr('Memory could not be read')
    elif prefs['memory']:
        note = tr('On - Luna saves only what you ask her to remember')
    else:
        note = tr('Off - Luna does not use or save memories')
    draw_text(surface,wrap_text(fonts['mini'],note,CONTROL_RIGHT-CONTROL_X-170,1)[0],fonts['mini'],
              MUTED,topleft=(CONTROL_X+170,y+119))
    if not facts:
        draw_text(surface,tr('Nothing saved yet.'),fonts['small'],TEXT,topleft=(LABEL_X,y+200))
        draw_text(surface,tr('Try: "Hey Luna, remember that I like jazz."'),fonts['mini'],MUTED,
                  topleft=(LABEL_X,y+236))
        return
    pages = memory_pages(len(facts))
    page = min(page,pages-1)
    for i,(text,action) in enumerate(facts[page*MEMORY_ROWS:(page+1)*MEMORY_ROWS]):
        row = pygame.Rect(LABEL_X,y+186+i*50,CONTROL_RIGHT-LABEL_X-58,42)
        pygame.draw.rect(surface,(8,32,62),row,border_radius=10)
        pygame.draw.rect(surface,(33,73,109),row,1,border_radius=10)
        draw_text(surface,wrap_text(fonts['small'],text,row.width-28,1)[0],fonts['small'],
                  TEXT,topleft=(row.left+14,row.centery-12))
        delete = pygame.Rect(row.right+12,row.top,46,42)
        pygame.draw.rect(surface,(8,32,62),delete,border_radius=10)
        pygame.draw.rect(surface,(255,110,110),delete,1,border_radius=10)
        line(surface,(255,110,110),(delete.centerx-8,delete.centery-8),(delete.centerx+8,delete.centery+8),2)
        line(surface,(255,110,110),(delete.centerx-8,delete.centery+8),(delete.centerx+8,delete.centery-8),2)
        add_hit(hits,delete,action)
    bottom = y+398
    if pages>1:
        for rect,(text,step) in zip(control_rects(bottom,2,44,60),(('<',-1),('>',1))):
            rect.x -= CONTROL_X-LABEL_X
            choice_button(surface,fonts,accent,rect,text,False,hits,('memory_page',step))
        draw_text(surface,'%d / %d' % (page+1,pages),fonts['small'],MUTED,
                  topleft=(LABEL_X+160,bottom+10))
    button = pygame.Rect(CONTROL_RIGHT-330,bottom,330,44)
    pygame.draw.rect(surface,(60,20,32) if confirm else (8,32,62),button,border_radius=10)
    pygame.draw.rect(surface,(255,110,110),button,2 if confirm else 1,border_radius=10)
    label = ('Tap again to forget everything' if confirm else 'Forget everything')
    draw_text(surface,tr(label),fonts['small'],TEXT,center=button.center)
    add_hit(hits,button,('forget_all',None))


def settings_page(surface, fonts, accent, prefs, active, preview, listening=('basic',''),
                  online=('basic',''), memory=((),0,False,False)):
    """Draw the Settings page (only the selected section) and return [(rect, action)] for taps."""
    hits = [(rect,('section',i)) for i,rect in enumerate(SECTION_RECTS)]
    settings_sidebar(surface,fonts,accent,active)
    pygame.draw.rect(surface,(6,26,52),CONTENT_RECT,border_radius=18)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.6),CONTENT_RECT.inflate(6,6),2,border_radius=20)
    pygame.draw.rect(surface,accent,CONTENT_RECT,2,border_radius=18)
    voice = get_voice(prefs['voice'])
    name = SECTIONS[active]
    y = CONTENT_CLIP.top

    if name=='Display & Theme':
        section_header(surface,fonts,name,y,'How Luna looks on the Home screen')
        row_label(surface,fonts,'Mode',y+130)
        for mode,rect in zip(MODES,control_rects(y+107,2,46,220)):
            choice_button(surface,fonts,accent,rect,tr(mode+' MODE'),prefs['mode']==mode,hits,('mode',mode))
        row_label(surface,fonts,'Theme colour',y+217)
        for color,rect in zip(COLOR_CHOICES,control_rects(y+184,4,66,160)):
            choice_button(surface,fonts,accent,rect,tr(color),prefs['color']==color,hits,('color',color),
                          swatch=COLOR_CHOICES[color])
        wave = prefs['mode']=='WAVE'
        current = prefs['wave_layout'] if wave else prefs['layout']
        choices = WAVE_LAYOUTS if wave else tuple(LAYOUT_CHOICES)
        row_label(surface,fonts,'Wave layout' if wave else 'Face layout',y+304)
        for layout,rect in zip(choices,control_rects(y+281,3,46,220)):
            choice_button(surface,fonts,accent,rect,tr(layout),current==layout,hits,('layout',layout))
    elif name=='Language':
        section_header(surface,fonts,name,y,'The language of the texts on the screen')
        row_label(surface,fonts,'App language',y+130)
        codes = i18n.LANGUAGES
        for (code,label),rect in zip(codes.items(),control_rects(y+107,len(codes),46,220,
                                                                 per_row=CHOICES_PER_ROW)):
            choice_button(surface,fonts,accent,rect,label,prefs['language']==code,hits,
                          ('language',code))
        draw_text(surface,tr('Luna listens, speaks and answers in the Voice language.'),
                  fonts['mini'],MUTED,topleft=(CONTROL_X,y+170+extra_rows_height(len(codes))))
    elif name=='Voice':
        section_header(surface,fonts,name,y,voice.label)
        row_label(surface,fonts,'Voice language',y+130)
        for (code,label),rect in zip(VOICE_LANGUAGES.items(),
                                     control_rects(y+107,len(VOICE_LANGUAGES),46,220,
                                                   per_row=CHOICES_PER_ROW)):
            choice_button(surface,fonts,accent,rect,label.upper(),voice.language==code,hits,
                          ('voice_language',code))
        y += extra_rows_height(len(VOICE_LANGUAGES))
        row_label(surface,fonts,'Voice type',y+197)
        choices = voices_for(voice.language)
        for option,rect in zip(choices,control_rects(y+174,len(choices),46,220,
                                                     per_row=CHOICES_PER_ROW)):
            choice_button(surface,fonts,accent,rect,voice_type_label(option,choices),
                          option.key==voice.key,hits,('voice',option.key))
        y += extra_rows_height(len(choices))
        row_label(surface,fonts,'Test voice',y+264)
        button = pygame.Rect(CONTROL_X,y+241,260,46)
        label = {'waiting':'Loading voice...','playing':'Playing...'}.get(preview,'Play sample')
        pygame.draw.rect(surface,(8,32,62),button,border_radius=10)
        pygame.draw.rect(surface,accent,button,2,border_radius=10)
        pygame.draw.polygon(surface,TEXT,[(button.left+26,button.centery-10),
                                          (button.left+26,button.centery+10),
                                          (button.left+44,button.centery)])
        draw_text(surface,tr(label),fonts['small'],TEXT,center=(button.centerx+18,button.centery))
        add_hit(hits,button,('preview',None))
        status_row(surface,fonts,'Status',y+331,voice_status(voice))
        status_row(surface,fonts,'Listening',y+391,listening)
    elif name=='Animations':
        section_header(surface,fonts,name,y,'Blinking, looking around and moving effects')
        row_label(surface,fonts,'Animations',y+130)
        toggle(surface,fonts,accent,prefs['animations'],y+111,hits,('animations',None))
    elif name=='Listening':
        section_header(surface,fonts,name,y,'Hear long questions more accurately')
        row_label(surface,fonts,'Online listening',y+130)
        toggle(surface,fonts,accent,prefs['online_listening'],y+111,hits,('online_listening',None))
        status_row(surface,fonts,'Status',y+200,online)
        notes = (tr('Commands and "Hey Luna" are always heard offline on the robot.'),
                 tr('When on, only questions to Luna are sent to OpenAI for transcription.'),
                 tr('Audio stays in memory and is never saved. Uses your API key (costs money).'))
        for i,text in enumerate(notes):
            draw_text(surface,wrap_text(fonts['mini'],text,860,1)[0],fonts['mini'],MUTED,
                      topleft=(LABEL_X,y+262+i*28))
    elif name=='Memory':
        section_header(surface,fonts,name,y,'What Luna remembers about you')
        memory_section(surface,fonts,accent,prefs,y,memory,hits)
    else:
        section_header(surface,fonts,name,y)
        about = (tr('Luna 1.0 - personal robot for Raspberry Pi 5.'),
                 tr('Voice: {label} ({note}).',label=voice.label,note=tr(voice.license_note)),
                 tr('Natural voices use Piper. Check licences before sharing Luna.'),
                 tr('Settings save automatically.'))
        for i,text in enumerate(about):
            draw_text(surface,wrap_text(fonts['small'],text,820,1)[0],fonts['small'],
                      TEXT if i==0 else MUTED,topleft=(LABEL_X,y+104+i*32))
    return hits


class RobotHUD:
    def __init__(self, args):
        pygame.init()
        pygame.font.init()
        self.fullscreen = args.fullscreen or (not args.windowed and os.name != 'nt')
        self.screen = self.make_screen()
        pygame.display.set_caption('Luna 1.0')
        self.canvas=pygame.Surface((W,H)).convert()
        self.background=background_surface()
        self.fonts={
            'header':pygame.font.SysFont('dejavusans',25),
            'mini':pygame.font.SysFont('dejavusans',17),
            'nav':pygame.font.SysFont('dejavusans',23),
            'small':pygame.font.SysFont('dejavusans',20),
            'body':pygame.font.SysFont('dejavusans',25),
            'title':pygame.font.SysFont('dejavusans',46,bold=True),
            'heading':pygame.font.SysFont('dejavusans',27,bold=True),
            'side':pygame.font.SysFont('dejavusans',22),
        }
        self.clock=pygame.time.Clock()
        self.prefs=read_preferences()
        i18n.set_language(self.prefs['language'])
        self.glow=glow_disc(169,self.accent)
        self.page='Home'
        self.gaze_x=self.gaze_y=self.target_x=self.target_y=0.0
        self.touch_time=-100.0
        self.start_time=time.monotonic()
        self.blink_start=-10.0
        self.next_blink=random.uniform(2.0,4.0)
        self.chat_active=False
        self.running=True
        self.mood='neutral'
        self.mood_until=0.0
        self.look_held=False  # a requested look stays while Luna answers
        self.wave_state='listening'
        self.wave_until=0.0
        self.wave_amp=.35
        self.conversation=Conversation()  # routing, AI answers, follow-ups (assistant core)
        self.memory=MemoryStore()  # facts the user asked Luna to remember (~/.luna-memory.db)
        self.memory_page=0
        self.forget_all_until=0.0  # "Forget everything" waits for a second tap until then
        self.conversation.set_memory(self.memory if self.prefs['memory'] else None)
        self.utterance=UtteranceAssembler(self.utterance_end)  # joins Vosk's pieces
        self.online=OnlineListening()  # optional online transcription of questions
        self.ring=AudioRing()          # recent mic audio in memory, only while online listening is on
        self.mic_overruns=0
        self.decode_mark=0.0
        self.settings_section=0
        self.settings_hits=[]
        self.preview=''  # '' | 'waiting' (voice still loading) | 'playing'
        speech.enabled=not args.no_speech
        # The voice is a preference; LUNA_VOICE overrides it for testing without saving it.
        self.voice_key=os.environ.get('LUNA_VOICE') or self.prefs['voice']
        speech.set_voice(self.voice_key)
        use_ai=not getattr(args,'no_ai',False) and ai_configured()
        self.ai=AIWorker() if use_ai else None
        if use_ai:  # name, location, turn limit, idle reset (from ~/.luna.env or the environment)
            self.conversation.settings=settings_from_env()
        self.voice=(VoiceListener(muted=self.mic_muted,free_form=self.ai is not None,
                                  language=self.talk_language)
                    if not args.no_voice else None)
        if self.voice:
            self.voice.start()
        self.last_touch=(-100.0, -10000.0,-10000.0)
        pygame.mouse.set_visible(not self.fullscreen)

    @property
    def ai(self):
        return self.conversation.ai

    @ai.setter
    def ai(self,worker):
        self.conversation.ai=worker

    @property
    def online_active(self):
        """Online listening is switched on and can work (AI set up, microphone on)."""
        return bool(self.prefs['online_listening']) and self.ai is not None and self.voice is not None

    @property
    def thinking(self):
        """Luna works on a question: transcribing it online or waiting for the AI."""
        return self.conversation.thinking or self.online.busy

    @property
    def talk_language(self):
        """Luna listens, speaks and answers in the language of the selected voice."""
        return get_voice(self.voice_key).language

    @property
    def heard_language(self):
        """The language Luna actually listens in (English if the voice language's model is missing)."""
        if self.voice and self.voice.active_language:
            return self.voice.active_language
        return self.talk_language

    @property
    def accent(self):
        return COLOR_CHOICES[self.prefs['color']]

    def make_screen(self):
        if self.fullscreen:
            return pygame.display.set_mode((0,0),pygame.FULLSCREEN)
        return pygame.display.set_mode((1100,619),pygame.RESIZABLE)

    def toggle_fullscreen(self):
        self.fullscreen=not self.fullscreen
        self.screen=self.make_screen()
        pygame.mouse.set_visible(not self.fullscreen)

    def save(self):
        try:
            write_preferences(self.prefs)
        except OSError as exc:
            print('Could not save face settings: '+str(exc),file=sys.stderr)

    def handle_touch(self,x,y,t):
        if QUIT_RECT.collidepoint(x,y):
            self.running=False
            return
        for name,rect in zip(NAV_NAMES,NAV_RECTS):
            if rect.collidepoint(x,y):
                self.page=name
                return
        if self.page=='Home':
            if speech.is_talking() or self.conversation.streaming or self.online.busy:
                self.interrupt_answer()
            self.target_x=clamp((x-W/2)/(W/2),-1,1)
            self.target_y=clamp((y-292)/290,-1,1)
            self.touch_time=t
            self.blink_start=t
        elif self.page=='Chat' and CHAT_RECT.collidepoint(x,y):
            self.chat_active=not self.chat_active
        elif self.page=='Settings':
            for rect,(kind,value) in self.settings_hits:
                if rect.collidepoint(x,y):
                    self.settings_choice(kind,value)
                    return

    def settings_choice(self,kind,value):
        if kind=='section':
            self.settings_section=value
        elif kind=='preview':
            self.preview_voice()
        elif kind=='forget_memory':
            self.change_memory(self.memory.forget,value)
        elif kind=='forget_profile':
            self.change_memory(self.memory.forget_profile,value)
        elif kind=='forget_all':
            now=time.monotonic()
            if now<self.forget_all_until:
                self.forget_all_until=0.0
                self.memory_page=0
                self.change_memory(self.memory.forget_all)
            else:
                self.forget_all_until=now+4.0
        elif kind=='memory_page':
            pages=memory_pages(len(self.memory_view()[0]))
            self.memory_page=max(0,min(pages-1,self.memory_page+value))
        else:
            changed=apply_choice(self.prefs,kind,value)
            if changed=='color':
                self.glow=glow_disc(169,self.accent)
            elif changed=='language':
                i18n.set_language(self.prefs['language'])
            elif changed=='voice':
                self.voice_key=self.prefs['voice']
                speech.set_voice(self.voice_key)
                self.online.cancel()
                if self.voice:
                    self.forget_utterance()
                    self.voice.set_language(self.talk_language)
            elif changed=='memory':
                self.conversation.set_memory(self.memory if self.prefs['memory'] else None)
            elif changed=='online_listening' and not self.prefs['online_listening']:
                self.online.cancel()
                self.ring.clear()
            if changed:
                self.save()

    def change_memory(self,change,*args):
        try:
            change(*args)
        except MEMORY_ERRORS as error:
            print('Luna memory: could not be changed (%s)' % type(error).__name__,flush=True)

    def memory_view(self):
        """(items, page, waiting for the second "forget everything" tap, error).

        Items are (text, delete action): "About you" first, then the facts newest first.
        """
        try:
            about=self.memory.profile()
            items=[(tr(label)+': '+(value if field!='birthday' else
                                    '%d/%d' % (int(value[3:]),int(value[:2]))),
                    ('forget_profile',field))
                   for field,label in (('name','Your name'),('home','You live in'),
                                       ('birthday','Your birthday'))
                   for value in [about.get(field)] if value]
            items+=[(fact.text,('forget_memory',fact.id)) for fact in reversed(self.memory.facts())]
            error=False
        except MEMORY_ERRORS:
            items,error=[],True
        return items,self.memory_page,time.monotonic()<self.forget_all_until,error

    def preview_voice(self):
        """Play a sample with the selected voice, once it has finished loading."""
        if not speech.enabled:
            return
        self.interrupt_answer()
        self.preview='waiting'

    def update_preview(self):
        if self.preview=='waiting' and speech.voice_state()[0]!='loading':
            speech.speak_async(sample_text(get_voice(self.prefs['voice'])))
            self.preview='playing'
        elif self.preview=='playing' and not speech.is_talking():
            self.preview=''

    def apply_commands(self,text,t):
        self.conversation.heard=text
        self.conversation.heard_until=t+6
        self.apply_actions(parse_command(text,self.heard_language),t)

    def apply_actions(self,actions,t,quiet=False):
        reply=None if quiet else reply_for(actions,self.talk_language)
        if reply:
            speech.speak_async(reply)
        for kind,value in actions:
            if kind=='expression':
                self.mood=value
                self.mood_until=t+10 if value!='neutral' else 0.0
                self.prefs['mode']='FACE'
            elif kind=='look':
                self.target_x,self.target_y=value
                self.touch_time=t
                self.look_held=True
                self.prefs['mode']='FACE'
            elif kind=='blink':
                self.blink_start=t
            elif kind=='wave':
                self.wave_state=value
                self.wave_until=t+6 if value=='speaking' else 0.0
                self.prefs['mode']='WAVE'
            elif kind=='mode':
                self.prefs['mode']=value
            elif kind=='color':
                self.prefs['color']=value
                self.glow=glow_disc(169,self.accent)
            self.save()

    def utterance_end(self,text,conversation):
        return utterance_end(text,self.heard_language,conversation)

    def in_conversation(self,t):
        return self.conversation.in_followup(t)

    def forget_utterance(self):
        """Drop the unfinished question and its queued pieces."""
        self.utterance.clear()
        if self.voice:
            while True:
                try:
                    self.voice.texts.get_nowait()
                except queue.Empty:
                    break

    def listen(self,t):
        """Join Vosk's pieces into one complete utterance (UtteranceAssembler) and route it once.

        With online listening on, a question for the AI is transcribed online first (the mic is
        muted meanwhile) and routed when that text, or Vosk's as the fallback, is ready.
        """
        voice=self.voice
        heard=self.online.poll(t)
        if heard:
            self.route_heard(heard,t)
        online=self.online_active
        voice.capture=self.ring if online else None
        if not online:
            self.ring.clear()
        elif not self.utterance.pending:
            self.ring.trim(self.start_time+t-PREROLL_SECONDS)  # keep only a short pre-roll
        overruns=getattr(voice,'overruns',0)
        lost=overruns!=self.mic_overruns
        self.mic_overruns=overruns
        if self.mic_muted() or voice.status!='listening':
            self.forget_utterance()  # Luna is talking (never hear herself) or the mic stopped
            return
        if lost:
            self.forget_utterance()
            self.utterance.invalidate(t)  # never answer a question with missing words
            return
        conversation=self.in_conversation(t)
        self.utterance.continue_seconds=continue_seconds(self.heard_language)
        while True:
            try:
                self.utterance.add_final(voice.texts.get_nowait(),t,conversation)
            except queue.Empty:
                break
        self.utterance.add_partial(voice.partial,t,conversation)
        done=self.utterance.poll(t)
        if done:
            self.log_utterance(done)
            kind=route(done.text,conversation=done.conversation,language=self.heard_language)[0]
            if online and kind=='ai':  # wake word or follow-up: authorised to send
                audio=self.ring.since(self.start_time+done.started-PREROLL_SECONDS)
                self.ring.clear()
                self.conversation.heard,self.conversation.heard_kind=done.text,kind
                self.conversation.heard_until=t+3600
                heard=self.online.start(done,audio,self.heard_language,t)
                if heard:
                    self.route_heard(heard,t)
                return
            self.handle_speech(done.text,t,conversation=done.conversation)

    def route_heard(self,heard,t):
        print('Luna online listening: %s; using the %s text (%d chars)'
              % (heard.note,'online' if heard.online else 'offline',len(heard.text)),flush=True)
        # Vosk already authorised this question, so the online text needs no wake word.
        self.handle_speech(heard.text,t,conversation=True if heard.online else heard.utterance.conversation)

    def log_utterance(self,done):
        decode=getattr(self.voice,'decode_seconds',0.0)
        print('Luna heard: %d chars in %d part(s), %.1f s of speech, handled %.2f s after the '
              'last words (%s); recogniser %.2f s CPU; dropped audio blocks %d'
              % (len(done.text),done.parts,done.last_words-done.started,
                 done.finished-done.last_words,done.ending,decode-self.decode_mark,
                 getattr(self.voice,'dropped_blocks',0)),flush=True)
        self.decode_mark=decode

    def handle_speech(self,text,t,conversation=None):
        """Route one complete utterance to an offline command or the online AI.

        `conversation`: the follow-up window was open when the user started speaking.
        """
        self.run_effects(self.conversation.hear(text,t,self.heard_language,self.talk_language,
                                                conversation),t)

    def ask_ai(self,question,t):
        self.run_effects(self.conversation.ask(question,t,self.talk_language),t)

    def poll_voice(self,t):
        if self.voice:
            self.listen(t)
        listening=bool(self.voice) and (self.utterance.pending or self.online.busy)
        self.run_effects(self.conversation.poll(t,speech.is_speaking(),speech.is_talking(),
                                                listening),t)

    def run_effects(self,effects,t):
        """Carry out what the assistant core decided (UI thread)."""
        for effect in effects:
            if isinstance(effect,Speak):
                speech.speak_async(effect.text)
            elif isinstance(effect,Act):
                self.apply_actions(list(effect.actions),t,effect.quiet)
            elif isinstance(effect,StopSpeech):
                speech.stop()
            elif isinstance(effect,Accepted):
                self.mood='neutral'
                self.wave_state='listening'
            elif isinstance(effect,Mood):
                self.mood=effect.mood
                self.mood_until=t+effect.seconds
            elif isinstance(effect,Log):
                print(effect.message,flush=True)

    def mic_muted(self):
        # Stay deaf between streamed sentences too, so Luna never hears herself.
        return self.conversation.muted(speech.is_speaking()) or self.online.busy

    def interrupt_answer(self):
        """The user stopped Luna: silence her and drop the unfinished answer (not remembered)."""
        self.forget_utterance()
        self.online.cancel()
        self.run_effects(self.conversation.interrupt(),0.0)

    def events(self,t):
        for event in pygame.event.get():
            if event.type==pygame.QUIT:
                self.running=False
            elif event.type==pygame.VIDEORESIZE and not self.fullscreen:
                self.screen=pygame.display.set_mode((max(event.w,600),max(event.h,350)),pygame.RESIZABLE)
            elif event.type==pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running=False
                elif event.key==pygame.K_F11:
                    self.toggle_fullscreen()
                elif event.key==pygame.K_SPACE:
                    self.blink_start=t
                elif event.key==pygame.K_s:
                    self.interrupt_answer()
                elif event.unicode in ('1','2','3','4'):
                    self.page=NAV_NAMES[int(event.unicode)-1]
                elif event.key in (pygame.K_UP,pygame.K_DOWN) and self.page=='Settings':
                    step=-1 if event.key==pygame.K_UP else 1
                    self.settings_section=(self.settings_section+step)%len(SECTIONS)
            elif event.type in (pygame.MOUSEBUTTONDOWN,pygame.FINGERDOWN):
                if event.type==pygame.MOUSEBUTTONDOWN:
                    if event.button!=1:
                        continue
                    px,py=event.pos
                else:
                    px=event.x*self.screen.get_width()
                    py=event.y*self.screen.get_height()
                point=Viewport(*self.screen.get_size()).to_logical(px,py)
                if point is None:
                    continue
                x,y=point
                # Some touchscreens emit both FINGERDOWN and MOUSEBUTTONDOWN.
                last_t,last_x,last_y=self.last_touch
                if t-last_t < .18 and abs(x-last_x)<22 and abs(y-last_y)<22:
                    continue
                self.last_touch=(t,x,y)
                self.handle_touch(x,y,t)

    def frame(self,t):
        self.poll_voice(t)
        if self.mood!='neutral' and t>self.mood_until:
            self.mood='neutral'
        if self.wave_state=='speaking' and t>self.wave_until:
            self.wave_state='listening'
        accent=self.accent
        self.canvas.blit(self.background,(0,0))
        motion_t = t if self.prefs['animations'] else 0.0
        self.update_preview()
        if self.page!='Settings':
            draw_scifi_chrome(self.canvas,accent,motion_t)
        status_bar(self.canvas,self.fonts,accent)
        talking=speech.is_talking()
        if self.page=='Home':
            if self.look_held and (self.thinking or self.conversation.streaming or talking):
                self.touch_time=t  # e.g. "look left" by a tool: keep it until Luna has answered
            if self.thinking and not self.look_held:
                # Look up and to the side while waiting for the AI.
                self.target_x=.55+.12*math.sin(t*1.3)
                self.target_y=-.8
            elif t-self.touch_time > 2.7:
                self.look_held=False
                self.target_x=.36*math.sin(t*.42) if self.prefs['animations'] else 0
                self.target_y=.2*math.sin(t*.29) if self.prefs['animations'] else 0
            self.gaze_x+=(self.target_x-self.gaze_x)*.13
            self.gaze_y+=(self.target_y-self.gaze_y)*.13
            if self.prefs['animations'] and t>=self.next_blink:
                self.blink_start=t
                self.next_blink=t+random.uniform(3.0,5.0)
            openness=blink_openness(t,self.blink_start)
            if self.prefs['mode']=='WAVE':
                state=self.wave_state
                if self.thinking:
                    state='thinking'
                elif talking:
                    state='speaking'
                base={'listening':.35,'speaking':.65+.35*abs(math.sin(t*6.5)),'quiet':.08,
                      'thinking':.18+.08*math.sin(t*3)}[state]
                level=self.voice.level if self.voice else 0.0
                self.wave_amp+=(max(base,min(1.0,level*1.6) if state!='quiet' else 0)-self.wave_amp)*.15
                amp=self.wave_amp
                wl=self.prefs['wave_layout']
                if wl=='BARS':
                    draw_mic(self.canvas,accent,640,200,motion_t)
                    draw_wave_bars(self.canvas,motion_t,accent,amp)
                elif wl=='CIRCLE':
                    draw_wave_circle(self.canvas,motion_t,accent,amp)
                    draw_mic(self.canvas,accent,640,328,motion_t)
                else:
                    draw_mic(self.canvas,accent,640,200,motion_t)
                    draw_wave(self.canvas,motion_t,accent,amp)
                self.draw_footer(t,tr(state.upper()+'...'))
                self.draw_caption(t)
                self.draw_you(t,y=478)
                nav_bar(self.canvas,self.page,self.fonts,accent)
                return
            dx,ey,sc,sy=LAYOUT_CHOICES[self.prefs['layout']]
            # The answer's expression is drawn above the user's mood, which stays as it was.
            mood='thinking' if self.thinking else (self.conversation.expression or self.mood)
            if mood=='surprised':
                sc*=1.12
            listening=bool(self.voice) and self.utterance.pending
            grow,bob=face_motion(motion_t,listening,talking,self.voice.level if self.voice else 0.0)
            sc*=grow
            ey+=bob
            if sy is not None:
                sy+=bob
            for ex,side in ((640-dx,-1),(640+dx,1)):
                draw_eye(self.canvas,ex,ey,motion_t,self.gaze_x,self.gaze_y,openness,accent,self.glow,sc,side,mood)
            if sy is not None:
                smile(self.canvas,t,accent,sy,mood,talking)
            hint=tr('SAY "HEY LUNA" AND ASK ANYTHING  •  TAP TO STOP TALKING' if self.ai else
                    'TAP TO LOOK AROUND  •  SAY: HAPPY, SAD, ANGRY, LOOK LEFT...')
            self.draw_footer(t,hint)
            self.draw_caption(t)
            self.draw_you(t)
        elif self.page=='Chat':
            chat_page(self.canvas,self.fonts,accent,t,self.chat_active)
        elif self.page=='Camera':
            camera_page(self.canvas,self.fonts,accent,t)
        elif self.page=='Settings':
            self.settings_hits=settings_page(self.canvas,self.fonts,accent,self.prefs,
                                             self.settings_section,self.preview,
                                             listening_status(self.voice,self.talk_language),
                                             online_status(self.prefs,self.ai is not None,
                                                           self.voice is not None),
                                             self.memory_view())
        nav_bar(self.canvas,self.page,self.fonts,accent)

    def draw_footer(self,t,default):
        """Bottom text: Luna's answer while relevant, otherwise the hint."""
        if self.thinking:
            lines=[tr('LUNA IS STILL THINKING...' if self.conversation.slow else 'LUNA IS THINKING...')]
        elif self.conversation.answer and (speech.is_talking() or t<self.conversation.answer_until):
            lines=wrap_text(self.fonts['small'],'LUNA: '+self.conversation.answer,1040,2)
        else:
            lines=[default]
        color=MUTED if lines==[default] else TEXT
        y=542-12*(len(lines)-1)
        for i,text in enumerate(lines):
            draw_text(self.canvas,text,self.fonts['small'],color,center=(640,y+i*25))

    def draw_you(self,t,y=132):
        """Speech bubble with what you are saying (live) or what Luna last heard."""
        live=self.utterance.display() if self.voice else ''
        if live:
            text,color,note=tr('YOU: ')+live+' ...',TEXT,''
        elif t<self.conversation.heard_until and self.conversation.heard:
            text,color=tr('YOU: ')+self.conversation.heard,TEXT
            note=(tr('  (say "Hey Luna" first)') if self.conversation.heard_kind=='ignore' and self.ai else '')
            if note:
                color=MUTED
        else:
            return
        font=self.fonts['body']
        if live and font.size(text)[0]>820:
            shown=[]  # a long question: show its newest words
            for word in reversed(live.split()):
                if shown and font.size(tr('YOU: ')+'... '+' '.join([word]+shown)+' ...')[0]>820:
                    break
                shown.insert(0,word)
            text=tr('YOU: ')+'... '+' '.join(shown)+' ...'
        line=wrap_text(font,text+note,820,1)[0]
        w=font.size(line)[0]+48
        box=pygame.Rect(0,0,w,46)
        box.center=(640,y)
        accent=self.accent
        edge=mix(accent,BACKGROUND,.25+.2*math.sin(t*6)) if live else mix(accent,BACKGROUND,.45)
        pygame.draw.rect(self.canvas,(8,34,66),box,border_radius=23)
        pygame.draw.rect(self.canvas,edge,box,2,border_radius=23)
        if live:
            pygame.draw.circle(self.canvas,accent,(box.left+18,box.centery),5)
        draw_text(self.canvas,line,font,color,center=(box.centerx+(6 if live else 0),box.centery))

    def draw_caption(self,t):
        if self.voice is None and self.ai is None:
            return
        if self.thinking:
            text=tr('STILL THINKING...' if self.conversation.slow else 'THINKING...')
        elif self.conversation.in_followup(t):
            text=tr('LISTENING FOR YOUR FOLLOW-UP...')
        else:
            text=tr('MIC: ')+(mic_status(self.voice.status).upper() if self.voice else tr('OFF'))
        text+='   •   '+tr('AI: ')+tr('ON' if self.ai else 'OFF')
        if self.online_active:
            text+='   •   '+tr('ONLINE LISTENING')
        if speech.enabled:
            text+='   •   '+tr('VOICE: ')+tr(speech.voice_state()[0].upper())
        draw_text(self.canvas,wrap_text(self.fonts['mini'],text,1100,1)[0],self.fonts['mini'],MUTED,
                  topleft=(60,108))

    def present(self):
        self.screen.fill((0,0,0))
        vp=Viewport(*self.screen.get_size())
        x,y,w,h=vp.rectangle
        if w==W and h==H:
            self.screen.blit(self.canvas,(x,y))
        else:
            self.screen.blit(pygame.transform.smoothscale(self.canvas,(w,h)),(x,y))
        pygame.display.flip()

    def run(self,screenshot=None):
        try:
            while self.running:
                t=time.monotonic()-self.start_time
                self.events(t)
                self.frame(t)
                self.present()
                if screenshot is not None:
                    pygame.image.save(self.screen,str(screenshot))
                    print('Saved screenshot to '+str(screenshot))
                    break
                self.clock.tick(FPS)
        finally:
            speech.stop()
            if self.voice:
                self.voice.stop()
            pygame.quit()


def main():
    parser=argparse.ArgumentParser(description='AI Robot HUD (Raspberry Pi + Windows)')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--windowed',action='store_true',help='Use a resizable desktop window')
    mode.add_argument('--fullscreen',action='store_true',help='Use current display fullscreen')
    parser.add_argument('--no-voice',action='store_true',help='Disable microphone commands')
    parser.add_argument('--no-speech',action='store_true',help='Disable spoken replies')
    parser.add_argument('--no-ai',action='store_true',help='Disable online AI conversation')
    parser.add_argument('--screenshot',metavar='FILE',help='Save first frame as PNG and exit')
    args=parser.parse_args()
    if args.screenshot:
        # One static frame: don't start the microphone, speech or AI threads.
        args.no_voice=args.no_speech=args.no_ai=True
    RobotHUD(args).run(screenshot=args.screenshot)


if __name__=='__main__':
    main()
