"""What the face is doing: gaze, blinking, mood and the wave level (pure, no pygame).

The robot (app.RobotHUD) keeps one FaceState, updates it every frame and draws from it.
"""
import math
import random
from dataclasses import dataclass, field

from .geometry import CANVAS_WIDTH as W, blink_openness, clamp

WAVE_BASE = {'listening': .35, 'quiet': .08}


@dataclass
class FaceState:
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    touch_time: float = -100.0
    blink_start: float = -10.0
    next_blink: float = field(default_factory=lambda: random.uniform(2.0, 4.0))
    mood: str = 'neutral'
    mood_until: float = 0.0
    look_held: bool = False  # a requested look stays while Luna answers
    wave_state: str = 'listening'
    wave_until: float = 0.0
    wave_amp: float = .35

    def expire(self, t):
        """A mood lasts a while; "speaking" in wave mode too."""
        if self.mood != 'neutral' and t > self.mood_until:
            self.mood = 'neutral'
        if self.wave_state == 'speaking' and t > self.wave_until:
            self.wave_state = 'listening'

    def look_at(self, x, y, t):
        """A tap on the face: look at that point and blink."""
        self.target_x = clamp((x - W / 2) / (W / 2), -1, 1)
        self.target_y = clamp((y - 292) / 290, -1, 1)
        self.touch_time = t
        self.blink_start = t

    def move_eyes(self, t, thinking, busy, animations):
        """Move the gaze one frame and return how open the eyes are (0..1).

        `busy`: Luna thinks, streams or talks (a requested look is held until she is done).
        """
        if self.look_held and busy:
            self.touch_time = t  # e.g. "look left" by a tool: keep it until Luna has answered
        if thinking and not self.look_held:
            # Look up and to the side while waiting for the AI.
            self.target_x = .55 + .12 * math.sin(t * 1.3)
            self.target_y = -.8
        elif t - self.touch_time > 2.7:
            self.look_held = False
            self.target_x = .36 * math.sin(t * .42) if animations else 0
            self.target_y = .2 * math.sin(t * .29) if animations else 0
        self.gaze_x += (self.target_x - self.gaze_x) * .13
        self.gaze_y += (self.target_y - self.gaze_y) * .13
        if animations and t >= self.next_blink:
            self.blink_start = t
            self.next_blink = t + random.uniform(3.0, 5.0)
        return blink_openness(t, self.blink_start)

    def wave(self, t, thinking, talking, level):
        """(state, amplitude) of the wave this frame; `level`: the microphone level."""
        state = 'thinking' if thinking else 'speaking' if talking else self.wave_state
        if state == 'speaking':
            base = .65 + .35 * abs(math.sin(t * 6.5))
        elif state == 'thinking':
            base = .18 + .08 * math.sin(t * 3)
        else:
            base = WAVE_BASE[state]
        self.wave_amp += (max(base, min(1.0, level * 1.6) if state != 'quiet' else 0) - self.wave_amp) * .15
        return state, self.wave_amp

    def apply(self, actions, t):
        """Carry out face actions (commands.ACTIONS); returns the preference changes."""
        changes = {}
        for kind, value in actions:
            if kind == 'expression':
                self.mood = value
                self.mood_until = t + 10 if value != 'neutral' else 0.0
                changes['mode'] = 'FACE'
            elif kind == 'look':
                self.target_x, self.target_y = value
                self.touch_time = t
                self.look_held = True
                changes['mode'] = 'FACE'
            elif kind == 'blink':
                self.blink_start = t
            elif kind == 'wave':
                self.wave_state = value
                self.wave_until = t + 6 if value == 'speaking' else 0.0
                changes['mode'] = 'WAVE'
            elif kind in ('mode', 'color'):
                changes[kind] = value
        return changes
