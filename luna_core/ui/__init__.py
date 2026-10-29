"""Luna's screen, split by job (all pygame; the rest of luna_core stays pygame-free).

- theme: colours and the fixed rectangles of the 1280x720 logical canvas
- draw: drawing primitives and the face/wave artwork
- chrome: status bar, navigation bar, panels
- pages: the Page base class and Luna's pages (apps add their own)
- settings_page: the Settings page drawing
- touch: taps from mouse/touch events

The robot itself (state, listening, conversation) is app.RobotHUD; face state is face_state.
"""
from .pages import Page

__all__ = ["Page"]
