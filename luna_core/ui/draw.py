"""Drawing primitives and the face/wave artwork (procedural, no image files)."""
import math
from functools import lru_cache

import pygame

from .theme import BACKGROUND, H, TEXT, W


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


def _mix(a, b, weight):
    return tuple(max(0, min(255, round(i * (1 - weight) + j * weight))) for i, j in zip(a, b))


_mix_cached = lru_cache(maxsize=8192)(_mix)


def mix(a, b, weight):
    # Cached: the face and wave ask for the same few hundred colours every frame.
    try:
        return _mix_cached(a, b, weight)
    except TypeError:  # unhashable colours (lists)
        return _mix(a, b, weight)


_layers = {}


def _layer(size):
    """A reused transparent drawing layer (allocating one every frame is slow on the Pi)."""
    surface = _layers.get(size)
    if surface is None:
        surface = _layers[size] = pygame.Surface(size, pygame.SRCALPHA)
    else:
        surface.fill((0, 0, 0, 0))
    return surface


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
    eye = _layer((size, size))
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


@lru_cache(maxsize=8)
def _mic_glow(accent):
    glow = pygame.Surface((160,160), pygame.SRCALPHA)
    for r in range(70, 0, -3):
        pygame.draw.circle(glow, (*accent, 3 + (70-r)//5), (80,80), r)
    return glow


def draw_mic(surface, accent, cx, cy, t):
    surface.blit(_mic_glow(tuple(accent)), (cx-80, cy-80))
    pygame.draw.rect(surface, accent, (cx-14,cy-34,28,50), 4, border_radius=14)
    pygame.draw.arc(surface, accent, (cx-27,cy-28,54,50), math.pi, math.tau, 4)
    line(surface, accent, (cx,cy+22), (cx,cy+36), 4)
    pygame.draw.ellipse(surface, accent, (cx-17,cy+33,34,10), 2)


def draw_wave(surface, t, accent, amp=1.0):
    # Mesh of sine ribbons: many offset curves share lobed envelopes and cross each other.
    other = (226, 70, 255)
    cy, x0, x1, step = 360, 40, 1240, 10
    layer = _layer((1280, 420))
    ly = 210
    xs = list(range(x0, x1 + 1, step))
    us = [(x - x0) / (x1 - x0) for x in xs]
    light = mix(accent, (235,250,255), .35)
    cols = [mix(light, other, i / len(xs)) for i in range(len(xs) - 1)]
    draw_line = pygame.draw.line
    for wave in range(2):
        a = 110 if wave == 0 else 170
        colours = [(*col, a) for col in cols]
        # The envelope does not depend on the ribbon (k): work it out once per wave.
        base = [215 * amp * (math.sin(u*math.pi*4 + t*1.3 + wave*1.7)**2
                             * (.7 + .3*math.sin(u*math.pi*2 - t*.7))) for u in us]
        for k in range(26):
            frac = k / 25 * 2 - 1
            lobe = math.sin(math.pi*(.5 + .5*frac))
            spin, shift = t*2.2, k*.05
            pts = [(x, ly + frac*(e*lobe)*(.6 + .4*wave) * math.sin(u*math.pi*9 + spin + shift))
                   for x, u, e in zip(xs, us, base)]
            for i, col in enumerate(colours):
                draw_line(layer, col, pts[i], pts[i+1], 1)
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
