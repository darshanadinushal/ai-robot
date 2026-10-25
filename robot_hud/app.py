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
from .commands import parse_command, reply_for, route
from . import speech
from .ai import AIWorker, ai_configured
from .voice import VoiceListener
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS, read_preferences, write_preferences

FPS = 30
BACKGROUND = (4, 11, 28)
TEXT = (223, 247, 255)
MUTED = (111, 151, 187)
FAINT = (45, 95, 143)
NAV_NAMES = ("Chat", "Home", "Camera", "Settings")
NAV_RECTS = [pygame.Rect(216 + i * 216, 597, 199, 93) for i in range(4)]
QUIT_RECT = pygame.Rect(1214, 23, 43, 43)
TAB_RECTS = [pygame.Rect(410 + i * 240, 146, 220, 46) for i in range(2)]
COLOR_RECTS = [pygame.Rect(341 + i * 159, 228, 131, 66) for i in range(4)]
LAYOUT_RECTS = [pygame.Rect(320 + i * 220, 332, 200, 52) for i in range(3)]
ANIM_RECT = pygame.Rect(470, 408, 340, 58)
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
        draw_text(surface,name,fonts['nav'],TEXT if active else MUTED,
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
    draw_text(surface,'AI VOICE ASSISTANT',fonts['title'],TEXT,center=(640,205))
    draw_text(surface,'Microphone / AI integration is not connected yet.',fonts['body'],MUTED,
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
    draw_text(surface,'Stop demo' if chat_active else 'Start waveform demo',fonts['body'],TEXT,
              center=CHAT_RECT.center)
    draw_text(surface,'Waveform is simulated: no microphone recording.',fonts['small'],MUTED,
              center=(640,431))


def camera_page(surface, fonts, accent, t):
    panel_base(surface,accent)
    draw_text(surface,'CAMERA',fonts['title'],TEXT,center=(640,182))
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
    draw_text(surface,'NO CAMERA FEED',fonts['body'],TEXT,center=preview.center)
    draw_text(surface,'This is a placeholder, not a live image.',fonts['small'],MUTED,
              center=(640,493))


def settings_page(surface, fonts, accent, prefs):
    panel_base(surface,accent)
    for name,rect in zip(MODES,TAB_RECTS):
        selected = prefs['mode']==name
        pygame.draw.rect(surface,(11,49,84) if selected else (8,32,62),rect,border_radius=12)
        pygame.draw.rect(surface,accent if selected else (33,73,109),rect,3 if selected else 1,
                         border_radius=12)
        draw_text(surface,name+' MODE',fonts['body'],TEXT if selected else MUTED,center=rect.center)
    draw_text(surface,'Theme colour',fonts['small'],MUTED,center=(640,212))
    for name,rect in zip(COLOR_CHOICES,COLOR_RECTS):
        selected = prefs['color']==name
        pygame.draw.rect(surface,(9,39,76),rect,border_radius=13)
        pygame.draw.rect(surface,COLOR_CHOICES[name] if selected else (33,73,109),
                         rect,3 if selected else 1,border_radius=13)
        pygame.draw.circle(surface,COLOR_CHOICES[name],(rect.centerx,rect.top+24),14)
        draw_text(surface,name,fonts['small'],TEXT,center=(rect.centerx,rect.top+51))
    wave = prefs['mode']=='WAVE'
    choices = WAVE_LAYOUTS if wave else tuple(LAYOUT_CHOICES)
    current = prefs['wave_layout'] if wave else prefs['layout']
    draw_text(surface,('Wave' if wave else 'Face')+' layout',fonts['small'],MUTED,center=(640,316))
    for name,rect in zip(choices,LAYOUT_RECTS):
        selected = current==name
        pygame.draw.rect(surface,(9,39,76),rect,border_radius=13)
        pygame.draw.rect(surface,accent if selected else (33,73,109),
                         rect,3 if selected else 1,border_radius=13)
        draw_text(surface,name,fonts['small'],TEXT,center=rect.center)
    pygame.draw.rect(surface,(10,42,76),ANIM_RECT,border_radius=13)
    pygame.draw.rect(surface,accent,ANIM_RECT,2,border_radius=13)
    draw_text(surface,'Animations: '+('ON' if prefs['animations'] else 'OFF'),fonts['body'],TEXT,
              center=ANIM_RECT.center)
    draw_text(surface,'The selected mode is shown on Home. Settings save automatically.',
              fonts['small'],MUTED,center=(640,510))


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
        }
        self.clock=pygame.time.Clock()
        self.prefs=read_preferences()
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
        self.wave_state='listening'
        self.wave_until=0.0
        self.wave_amp=.35
        self.heard=''
        self.heard_until=0.0
        self.heard_kind=''
        self.thinking=False
        self.question=''
        self.answer=''
        self.answer_until=0.0
        self.conversation_until=0.0
        self.followup_pending=False
        speech.enabled=not args.no_speech
        use_ai=not getattr(args,'no_ai',False) and ai_configured()
        self.ai=AIWorker() if use_ai else None
        self.voice=(VoiceListener(muted=speech.is_speaking,free_form=self.ai is not None)
                    if not args.no_voice else None)
        if self.voice:
            self.voice.start()
        self.last_touch=(-100.0, -10000.0,-10000.0)
        pygame.mouse.set_visible(not self.fullscreen)

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
            if speech.is_talking():
                speech.stop()
                self.followup_pending=False
            self.target_x=clamp((x-W/2)/(W/2),-1,1)
            self.target_y=clamp((y-292)/290,-1,1)
            self.touch_time=t
            self.blink_start=t
        elif self.page=='Chat' and CHAT_RECT.collidepoint(x,y):
            self.chat_active=not self.chat_active
        elif self.page=='Settings':
            for name,rect in zip(MODES,TAB_RECTS):
                if rect.collidepoint(x,y):
                    self.prefs['mode']=name
                    self.save()
                    return
            for name,rect in zip(COLOR_CHOICES,COLOR_RECTS):
                if rect.collidepoint(x,y):
                    self.prefs['color']=name
                    self.glow=glow_disc(169,self.accent)
                    self.save()
                    return
            wave=self.prefs['mode']=='WAVE'
            for name,rect in zip(WAVE_LAYOUTS if wave else tuple(LAYOUT_CHOICES),LAYOUT_RECTS):
                if rect.collidepoint(x,y):
                    self.prefs['wave_layout' if wave else 'layout']=name
                    self.save()
                    return
            if ANIM_RECT.collidepoint(x,y):
                self.prefs['animations']=not self.prefs['animations']
                self.save()

    def apply_commands(self,text,t):
        self.heard=text
        self.heard_until=t+6
        self.apply_actions(parse_command(text),t)

    def apply_actions(self,actions,t):
        reply=reply_for(actions)
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

    def handle_speech(self,text,t):
        """Route recognised speech to an offline command or the online AI."""
        self.heard=text
        self.heard_until=t+8
        kind,value=route(text,conversation=self.ai is not None and t<self.conversation_until)
        self.heard_kind=kind
        if kind=='command':
            self.apply_actions(value,t)
        elif kind=='wake':
            speech.speak_async('Yes?')
            self.followup_pending=True
        elif kind=='ai':
            self.ask_ai(value,t)

    def ask_ai(self,question,t):
        if self.ai is None:
            speech.speak_async('My AI brain is not set up yet.')
            return
        if not self.ai.ask(question):
            return  # still answering the previous question
        self.thinking=True
        self.heard_until=t+3600
        self.question=question
        self.answer=''
        self.conversation_until=0.0
        self.mood='neutral'
        self.wave_state='listening'

    def poll_voice(self,t):
        if self.voice:
            while True:
                try:
                    self.handle_speech(self.voice.texts.get_nowait(),t)
                except queue.Empty:
                    break
        if self.ai:
            while True:
                try:
                    kind,text=self.ai.answers.get_nowait()
                except queue.Empty:
                    break
                self.thinking=False
                self.answer=text
                self.answer_until=t+20
                self.heard_until=t+20
                if kind=='error':
                    self.mood='sad'
                    self.mood_until=t+4
                speech.speak_async(text)
                self.followup_pending=kind=='answer'
        if self.followup_pending and not speech.is_speaking():
            # Allow a follow-up question without saying "Luna" again.
            self.followup_pending=False
            self.conversation_until=t+8

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
                    speech.stop()
                    self.followup_pending=False
                elif event.unicode in ('1','2','3','4'):
                    self.page=NAV_NAMES[int(event.unicode)-1]
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
        draw_scifi_chrome(self.canvas,accent,motion_t)
        status_bar(self.canvas,self.fonts,accent)
        talking=speech.is_talking()
        if self.page=='Home':
            if self.thinking:
                # Look up and to the side while waiting for the AI.
                self.target_x=.55+.12*math.sin(t*1.3)
                self.target_y=-.8
            elif t-self.touch_time > 2.7:
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
                self.draw_footer(t,state.upper()+'...')
                self.draw_caption(t)
                self.draw_you(t,y=478)
                nav_bar(self.canvas,self.page,self.fonts,accent)
                return
            dx,ey,sc,sy=LAYOUT_CHOICES[self.prefs['layout']]
            if self.mood=='surprised':
                sc*=1.12
            mood='thinking' if self.thinking else self.mood
            for ex,side in ((640-dx,-1),(640+dx,1)):
                draw_eye(self.canvas,ex,ey,motion_t,self.gaze_x,self.gaze_y,openness,accent,self.glow,sc,side,mood)
            if sy is not None:
                smile(self.canvas,t,accent,sy,mood,talking)
            hint=('SAY "HEY LUNA" AND ASK ANYTHING  •  TAP TO STOP TALKING' if self.ai else
                  'TAP TO LOOK AROUND  •  SAY: HAPPY, SAD, ANGRY, LOOK LEFT...')
            self.draw_footer(t,hint)
            self.draw_caption(t)
            self.draw_you(t)
        elif self.page=='Chat':
            chat_page(self.canvas,self.fonts,accent,t,self.chat_active)
        elif self.page=='Camera':
            camera_page(self.canvas,self.fonts,accent,t)
        elif self.page=='Settings':
            settings_page(self.canvas,self.fonts,accent,self.prefs)
        nav_bar(self.canvas,self.page,self.fonts,accent)

    def draw_footer(self,t,default):
        """Bottom text: Luna's answer while relevant, otherwise the hint."""
        if self.thinking:
            lines=['LUNA IS THINKING...']
        elif self.answer and (speech.is_talking() or t<self.answer_until):
            lines=wrap_text(self.fonts['small'],'LUNA: '+self.answer,1040,2)
        else:
            lines=[default]
        color=MUTED if lines==[default] else TEXT
        y=542-12*(len(lines)-1)
        for i,text in enumerate(lines):
            draw_text(self.canvas,text,self.fonts['small'],color,center=(640,y+i*25))

    def draw_you(self,t,y=132):
        """Speech bubble with what you are saying (live) or what Luna last heard."""
        live=self.voice.partial if self.voice else ''
        if live:
            text,color,note='YOU: '+live+' ...',TEXT,''
        elif t<self.heard_until and self.heard:
            text,color='YOU: '+self.heard,TEXT
            note=('  (say "Hey Luna" first)' if self.heard_kind=='ignore' and self.ai else '')
            if note:
                color=MUTED
        else:
            return
        font=self.fonts['body']
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
            text='THINKING...'
        elif self.ai and t<self.conversation_until:
            text='LISTENING FOR YOUR FOLLOW-UP...'
        else:
            text='MIC: '+(self.voice.status.upper() if self.voice else 'OFF')
        text+='   •   AI: '+('ON' if self.ai else 'OFF')
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
