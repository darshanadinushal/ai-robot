"""The Tasks page: Luna's background tasks (shown only when tasks are set up).

Lists the newest tasks with their state and result, and a Stop button for the newest running
one. Reads the TaskStore at most once a second (the page is not animated).
"""
import pygame

from ..i18n import tr
from ..tasks.model import CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, WAITING
from .chrome import panel_base
from .draw import draw_text, wrap_text
from .pages import Page
from .theme import MUTED, TEXT

ROWS = 5
STOP_RECT = pygame.Rect(930, 158, 150, 46)
STATES = {QUEUED: 'Waiting', RUNNING: 'Working', WAITING: 'Needs your OK', DONE: 'Done',
          FAILED: 'Failed', CANCELLED: 'Stopped', INTERRUPTED: 'Interrupted'}
ERRORS = {'budget': 'Daily AI budget used up', 'time': 'Took too long',
          'cost': 'Cost limit reached', 'interrupted': 'Luna was interrupted',
          'user': 'Stopped by you', 'off': 'Tasks were switched off'}
DOTS = {DONE: (67, 235, 179), RUNNING: (255, 185, 67), QUEUED: (255, 185, 67),
        WAITING: (255, 185, 67)}


def task_status(client):
    """The line under the title: what the task service is doing."""
    if not client.enabled:
        return tr('Off - switch tasks on in Settings > AI')
    if client.disabled:
        return tr('Not available - restart Luna to try again')
    if not client.available:
        return tr('Starting the helpers...')
    return tr('Say "Luna, what are you working on?" or "Luna, cancel the task".')


def task_line(task):
    """The second line of a row: state, and the result or why it stopped."""
    state = tr(STATES.get(task.state, task.state))
    if task.state == DONE and task.summary:
        return state + ' - ' + task.summary
    if task.state in (FAILED, CANCELLED, INTERRUPTED):
        return state + ' - ' + tr(ERRORS.get(task.error, 'Something went wrong'))
    return state


class TasksPage(Page):
    name = 'Tasks'
    animated = False

    def __init__(self):
        self.shown = []
        self.read_at = None

    def draw_icon(self, surface, x, y, color):
        pygame.draw.rect(surface, color, (x-18, y-20, 36, 42), 3, border_radius=6)
        for i in range(3):
            row = y-8+i*11
            pygame.draw.lines(surface, color, False, [(x-11, row), (x-8, row+3), (x-3, row-3)], 2)
            pygame.draw.line(surface, color, (x+1, row), (x+11, row), 2)

    def tasks(self, client, t):
        if self.read_at is None or t - self.read_at >= 1.0 or t < self.read_at:
            self.shown = client.recent(ROWS)
            self.read_at = t
        return self.shown

    def draw(self, hud, surface, t):
        panel_base(surface, hud.accent)
        client = hud.tasks
        fonts = hud.fonts
        draw_text(surface, tr('TASKS'), fonts['title'], TEXT, topleft=(210, 150))
        draw_text(surface, wrap_text(fonts['mini'], task_status(client), 700, 1)[0], fonts['mini'],
                  MUTED, topleft=(212, 204))
        tasks = self.tasks(client, t)
        if any(task.active for task in tasks):
            pygame.draw.rect(surface, (8, 32, 62), STOP_RECT, border_radius=10)
            pygame.draw.rect(surface, hud.accent, STOP_RECT, 2, border_radius=10)
            draw_text(surface, tr('Stop task'), fonts['small'], TEXT, center=STOP_RECT.center)
        if not tasks:
            draw_text(surface, tr('No tasks yet. Ask Luna to research something for you.'),
                      fonts['small'], MUTED, center=(640, 360))
            return
        for i, task in enumerate(tasks):
            y = 248 + i*56
            pygame.draw.circle(surface, DOTS.get(task.state, (255, 110, 110)), (222, y+12), 7)
            draw_text(surface, wrap_text(fonts['small'], task.goal, 820, 1)[0], fonts['small'],
                      TEXT, topleft=(242, y))
            draw_text(surface, wrap_text(fonts['mini'], task_line(task), 820, 1)[0],
                      fonts['mini'], MUTED, topleft=(244, y+28))

    def touch(self, hud, x, y, t):
        if STOP_RECT.collidepoint(x, y) and any(task.active for task in self.shown):
            hud.run_effects(hud.conversation.control('cancel_task', hud.talk_language), t)
            self.read_at = None
