"""Fullscreen, touch-friendly robot HUD built with pygame only.

All artwork is drawn procedurally; no proprietary icons, images or fonts needed.
"""
import argparse
import math
import os
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
from .preferences import COLOR_CHOICES, read_preferences, write_preferences

FPS = 30
BACKGROUND = (4, 11, 28)
TEXT = (223, 247, 255)
MUTED = (111, 151, 187)
FAINT = (45, 95, 143)
NAV_NAMES = ("Chat", "Home", "Camera", "Settings")
NAV_RECTS = [pygame.Rect(216 + i * 216, 597, 199, 93) for i in range(4)]
QUIT_RECT = pygame.Rect(1214, 23, 43, 43)
COLOR_RECTS = [pygame.Rect(341 + i * 159, 340, 131, 81) for i in range(4)]
ANIM_RECT = pygame.Rect(470, 445, 340, 67)
CHAT_RECT = pygame.Rect(485, 460, 310, 63)


def draw_text(surface, text, font, color, center=None, topleft=None):
    glyphs = font.render(text, True, color)
    if center is not None:
        surface.blit(glyphs, glyphs.get_rect(center=center))
    else:
        surface.blit(glyphs, topleft)


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
    return tuple(round(i * (1 - weight) + j * weight) for i, j in zip(a, b))


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


def draw_eye(surface, cx, cy, t, gaze_x, gaze_y, openness, color, glow):
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

    if openness < .14:
        line(surface, mix(color, BACKGROUND, .55), (cx-111,cy), (cx+111,cy), 11)
        line(surface, color, (cx-98,cy), (cx+98,cy), 4)
    else:
        h = max(2, int(size * openness))
        surface.blit(pygame.transform.smoothscale(eye, (size,h)),
                     (cx-size//2, cy-h//2))


def smile(surface, t, accent):
    dots=[]
    for i in range(41):
        a = i/40
        dots.append((int(640 - 46 + 92*a),
                     int(474 + 17*math.sin(math.pi*a) + 1.5*math.sin(t*2))))
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
    draw_text(surface,'AI ROBOT   /   HUD 01', fonts['header'], MUTED,center=(640,43))
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
    draw_text(surface,'FACE SETTINGS',fonts['title'],TEXT,center=(640,198))
    draw_text(surface,'Choose a theme colour',fonts['body'],MUTED,center=(640,273))
    for name,rect in zip(COLOR_CHOICES,COLOR_RECTS):
        selected = prefs['color']==name
        pygame.draw.rect(surface,(9,39,76),rect,border_radius=13)
        pygame.draw.rect(surface,COLOR_CHOICES[name] if selected else (33,73,109),
                         rect,3 if selected else 1,border_radius=13)
        pygame.draw.circle(surface,COLOR_CHOICES[name],(rect.centerx,rect.top+28),16)
        draw_text(surface,name,fonts['small'],TEXT,center=(rect.centerx,rect.top+64))
    pygame.draw.rect(surface,(10,42,76),ANIM_RECT,border_radius=13)
    pygame.draw.rect(surface,accent,ANIM_RECT,2,border_radius=13)
    draw_text(surface,'Animations: '+('ON' if prefs['animations'] else 'OFF'),fonts['body'],TEXT,
              center=ANIM_RECT.center)
    draw_text(surface,'Settings save automatically to your home folder.',fonts['small'],MUTED,
              center=(640,527))


class RobotHUD:
    def __init__(self, args):
        pygame.init()
        pygame.font.init()
        self.fullscreen = args.fullscreen or (not args.windowed and os.name != 'nt')
        self.screen = self.make_screen()
        pygame.display.set_caption('AI Robot - Futuristic HUD Eyes')
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
            self.target_x=clamp((x-W/2)/(W/2),-1,1)
            self.target_y=clamp((y-292)/290,-1,1)
            self.touch_time=t
            self.blink_start=t
        elif self.page=='Chat' and CHAT_RECT.collidepoint(x,y):
            self.chat_active=not self.chat_active
        elif self.page=='Settings':
            for name,rect in zip(COLOR_CHOICES,COLOR_RECTS):
                if rect.collidepoint(x,y):
                    self.prefs['color']=name
                    self.glow=glow_disc(169,self.accent)
                    self.save()
                    return
            if ANIM_RECT.collidepoint(x,y):
                self.prefs['animations']=not self.prefs['animations']
                self.save()

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
        accent=self.accent
        self.canvas.blit(self.background,(0,0))
        motion_t = t if self.prefs['animations'] else 0.0
        draw_scifi_chrome(self.canvas,accent,motion_t)
        status_bar(self.canvas,self.fonts,accent)
        if self.page=='Home':
            if t-self.touch_time > 2.7:
                self.target_x=.36*math.sin(t*.42) if self.prefs['animations'] else 0
                self.target_y=.2*math.sin(t*.29) if self.prefs['animations'] else 0
            self.gaze_x+=(self.target_x-self.gaze_x)*.13
            self.gaze_y+=(self.target_y-self.gaze_y)*.13
            if self.prefs['animations'] and t>=self.next_blink:
                self.blink_start=t
                self.next_blink=t+random.uniform(3.0,5.0)
            openness=blink_openness(t,self.blink_start)
            draw_eye(self.canvas,416,296,motion_t,self.gaze_x,self.gaze_y,openness,accent,self.glow)
            draw_eye(self.canvas,864,296,motion_t,self.gaze_x,self.gaze_y,openness,accent,self.glow)
            smile(self.canvas,motion_t,accent)
            draw_text(self.canvas,'TAP TO LOOK AROUND  •  SPACE TO BLINK',self.fonts['small'],MUTED,
                      center=(640,542))
        elif self.page=='Chat':
            chat_page(self.canvas,self.fonts,accent,t,self.chat_active)
        elif self.page=='Camera':
            camera_page(self.canvas,self.fonts,accent,t)
        elif self.page=='Settings':
            settings_page(self.canvas,self.fonts,accent,self.prefs)
        nav_bar(self.canvas,self.page,self.fonts,accent)

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
            pygame.quit()


def main():
    parser=argparse.ArgumentParser(description='AI Robot HUD (Raspberry Pi + Windows)')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--windowed',action='store_true',help='Use a resizable desktop window')
    mode.add_argument('--fullscreen',action='store_true',help='Use current display fullscreen')
    parser.add_argument('--screenshot',metavar='FILE',help='Save first frame as PNG and exit')
    args=parser.parse_args()
    RobotHUD(args).run(screenshot=args.screenshot)


if __name__=='__main__':
    main()
