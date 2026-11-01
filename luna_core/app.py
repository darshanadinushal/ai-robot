"""The robot: RobotHUD ties the screen, listening, speech and the conversation together.

Drawing lives in luna_core.ui (theme, draw, chrome, pages, settings_page, touch) and the face
state in face_state; this module keeps the running robot and its main loop.
All artwork is drawn procedurally; no proprietary icons, images or fonts needed.
"""
import logging
import math
import os
import queue
import time

try:
    import pygame
except ImportError as exc:
    raise SystemExit(
        "Pygame isn't installed. On Raspberry Pi: sudo apt install -y python3-pygame\n"
        "On Windows: py -m pip install -r requirements.txt"
    ) from exc

from .geometry import Viewport
from .commands import parse_command, reply_for, route, utterance_end
from .listening import UtteranceAssembler, continue_seconds
from . import audio_devices, hardware, i18n, naming, pacing, speech
from .i18n import tr
from .ai import AIWorker, ai_configured
from .assistant_core.conversation import Conversation
from .assistant_core.context import settings_from_env
from .assistant_core.events import Accepted, Act, Announce, Log, Mood, Speak, StopSpeech, TaskStarted
from .assistant_core.expression import face_motion
from .assistant_core.memory import ERRORS as MEMORY_ERRORS, MemoryStore
from .tasks.announce import Announcer
from .tasks.budget import CostGuard, UsageMeter
from .tasks.client import TaskClient, demo_client
from .tasks.catalog import catalog as task_catalog
from .tasks import process as task_process
from . import agents as task_agents
from .tasks.store import TaskStore
from .transcription import PREROLL_SECONDS, AudioRing, OnlineListening
from .voice import VoiceListener
from .face_state import FaceState
from .plugins.manager import PluginManager
from .plugins.store import PluginStore
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, read_preferences, write_preferences
from .settings import SECTIONS, NameEditor, apply_choice, voice_interrupt_wanted
from .voices import get_voice, sample_text
from .ui import chrome, draw, pages as ui_pages, settings_page as ui_settings, theme
from .ui.chrome import confirm_dialog, nav_bar, status_bar
from .ui.draw import (background_surface, draw_eye, draw_mic, draw_scifi_chrome, draw_text, draw_wave,
                      draw_wave_bars, draw_wave_circle, glow_disc, mix, smile, wrap_text)
from .ui.pages import Page, default_pages
from .ui.tasks_page import TasksPage
from .ui.settings_page import (audio_status, listening_status, memory_pages, mic_status, online_status,
                               plugin_pages, settings_page)
from .ui.theme import (BACKGROUND, CONFIRM_NO_RECT, CONFIRM_RECT, CONFIRM_YES_RECT, H, MUTED, QUIT_RECT,
                       TEXT, W, nav_rects)
from .ui.touch import TouchInput

log=logging.getLogger('luna.app')


def __getattr__(name):
    """Old names: the drawing code moved to luna_core.ui in 1.0 (app.listening_status etc. still work)."""
    for module in (theme,draw,chrome,ui_settings,ui_pages):
        if not name.startswith('_') and hasattr(module,name):
            return getattr(module,name)
    raise AttributeError("module %r has no attribute %r" % (__name__,name))


class _Face:
    """A RobotHUD attribute kept in its FaceState (hud.mood is hud.face.mood)."""

    def __set_name__(self,owner,name):
        self.name=name

    def __get__(self,hud,owner=None):
        return self if hud is None else getattr(hud.face,self.name)

    def __set__(self,hud,value):
        setattr(hud.face,self.name,value)

class RobotHUD:
    # Face state lives in self.face (face_state.FaceState); these names still work.
    gaze_x,gaze_y,target_x,target_y,touch_time,blink_start,next_blink=(_Face() for _ in range(7))
    mood,mood_until,look_held,wave_state,wave_until,wave_amp=(_Face() for _ in range(6))

    def __init__(self, args):
        # Only the parts Luna uses: pygame.init() would also open the sound device for the
        # unused mixer (Luna plays speech with pw-play/aplay) and start joystick scanning,
        # which costs start time, memory and threads on the Pi.
        pygame.display.init()
        pygame.font.init()
        self.fullscreen = args.fullscreen or (not args.windowed and os.name != 'nt')
        self.screen = self.make_screen()
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
        self.frame_rates=pacing.frame_rates()  # (active, idle): LUNA_FPS, LUNA_IDLE_FPS
        self.last_input=0.0  # when the user last touched, typed or was heard
        self._scaled=None    # reused surface when the screen is not 1280x720
        self.prefs=read_preferences()
        naming.set_name(self.prefs['robot_name'])  # before any text: tr() and the wake word use it
        pygame.display.set_caption(naming.name+' 1.0')
        i18n.set_language(self.prefs['language'])
        self.name_editor=None  # NameEditor while the robot name is typed
        self.restart=False     # main() starts the app again (after a new name)
        self.glow=glow_disc(169,self.accent)
        self.page='Home'
        self.pages=[]  # ui.pages.Page objects, in navigation-bar order
        for page in default_pages()+list(getattr(args,'pages',None) or ()):
            self.add_page(page)
        self.face=FaceState()  # gaze, blinking, mood, wave
        self.start_time=time.monotonic()
        self.chat_active=False
        self.running=True
        self.conversation=Conversation()  # routing, AI answers, follow-ups (assistant core)
        # AI cost: usage of every paid call (~/.luna/tasks.db) and the daily budget (Settings > AI).
        self.usage=UsageMeter()
        self.budget=self.conversation.budget=CostGuard(self.usage,self.prefs['ai_daily_budget'])
        self.core_pages=4  # Chat, Home, Camera, Settings (and Tasks) cannot be removed
        # Background tasks (tasks/client.py), opt-in: LUNA_TASKS=service runs the specialist
        # agents in their own process (needs the [agents] extra and the AI), started only when
        # a task needs it; LUNA_TASKS=demo uses a pretend helper.
        self.tasks=None
        task_mode=os.environ.get('LUNA_TASKS','').strip().lower()
        if task_mode=='service':
            if getattr(args,'no_ai',False) or not ai_configured():
                log.info('Luna tasks: off (they need the AI)')
            elif not task_agents.available():
                log.info('Luna tasks: off (install the agents extra: pip install -e ".[agents]")')
            else:
                self.tasks=TaskClient(task_process.connect,TaskStore(),self.usage,self.budget,
                                      enabled=self.prefs['tasks'],
                                      history_days=self.prefs['task_history_days'],
                                      catalog=task_catalog(),lazy=True)
                log.info('Luna tasks: on (the helper process starts when a task needs it)')
        elif task_mode=='demo':
            self.tasks=demo_client(TaskStore(),self.usage,self.budget,enabled=self.prefs['tasks'],
                                   history_days=self.prefs['task_history_days'])
            log.info('Luna tasks: demo mode (a pretend helper finishes every task after 8 s)')
        if self.tasks is not None:
            self.conversation.set_tasks(self.tasks,Announcer(quiet=self.prefs['quiet_hours']))
            self.pages.insert(self.core_pages,TasksPage())
            self.core_pages+=1
        self.memory=MemoryStore()  # facts the user asked Luna to remember (~/.luna-memory.db)
        self.memory_page=0
        self.forget_all_until=0.0  # "Forget everything" waits for a second tap until then
        self.conversation.set_memory(self.memory if self.prefs['memory'] else None)
        self.utterance=UtteranceAssembler(self.utterance_end)  # joins Vosk's pieces
        self.online=OnlineListening(meter=self.usage)  # optional online transcription of questions
        self.ring=AudioRing()          # recent mic audio in memory, only while online listening is on
        self.mic_overruns=0
        self.decode_mark=0.0
        self.settings_section=0
        self.settings_hits=[]
        self.preview=''  # '' | 'waiting' (voice still loading) | 'playing'
        self.plugin_page=0
        self.plugin_open=None  # the plugin shown in Settings > Plugins
        self.voice=None
        # The devices (hardware.create): real ones, mocks, or an app's own Hardware.
        chosen=getattr(args,'hardware',None)
        self.hardware=(chosen if isinstance(chosen,hardware.Hardware)
                       else hardware.create(chosen,detect_audio=not args.no_voice))
        speech.enabled=not args.no_speech
        speech.use_speaker(self.hardware.speaker)
        # The voice is a preference; LUNA_VOICE overrides it for testing without saving it.
        self.voice_key=os.environ.get('LUNA_VOICE') or self.prefs['voice']
        speech.set_voice(self.voice_key)
        use_ai=not getattr(args,'no_ai',False) and ai_configured()
        self.ai=AIWorker(meter=self.usage) if use_ai else None
        if use_ai:
            self.ai.warm_up()  # load the AI client now, not when the first question comes  # name, location, turn limit, idle reset (from ~/.luna.env or the environment)
            self.conversation.settings=settings_from_env()
        # Microphone + speech speaker; an echo-cancelling pair turns on interrupt by voice.
        self.audio=self.hardware.audio or audio_devices.AudioSetup()
        if not args.no_voice:
            log.info('Luna audio (%s hardware): mic %r, speaker %r, echo cancelling: %s',
                     self.hardware.mode,self.audio.mic or 'default',self.audio.speaker or 'default',
                     self.audio.echo_cancelling)
        # Plugins (app ones, installed ones unless --no-plugins) start before listening, so
        # their command words are in the grammar from the start.
        self.plugins=PluginManager(host=self,store=getattr(args,'plugin_store',None) or PluginStore())
        if not getattr(args,'no_plugins',False):
            for plugin in getattr(args,'plugins',None) or ():
                self.plugins.add(plugin)
            if getattr(args,'discover_plugins',False):
                self.plugins.discover()
            self.plugins.start_all()
        self.voice=(VoiceListener(muted=self.mic_muted,free_form=self.ai is not None,
                                  language=self.talk_language,microphone=self.hardware.microphone)
                    if not args.no_voice else None)
        if self.voice:
            self.voice.start()
        self.touch=TouchInput()
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

    def add_page(self,page):
        """Add a page (ui.pages.Page) with its own navigation button; names must be unique."""
        if not isinstance(page,Page):
            raise TypeError('a page must be a luna_core.ui.Page, not %s' % type(page).__name__)
        if any(page.name==other.name for other in self.pages):
            raise ValueError('there is already a page called %r' % page.name)
        self.pages.append(page)

    def remove_page(self,page):
        """Remove an added page (a plugin stopped); Home is shown if it was on screen."""
        if page in self.pages and self.pages.index(page)>=self.core_pages:
            self.pages.remove(page)
            if self.page==page.name:
                self.page='Home'

    # --- what plugins use (plugins.manager.PluginContext) -----------------------------------
    def say(self,text):
        speech.speak_async(text)

    def face_actions(self,actions):
        self.apply_actions(list(actions),time.monotonic()-self.start_time,quiet=True)

    def memories(self):
        memory=self.conversation.memory
        return [fact.text for fact in memory.facts()] if memory is not None else []

    def plugins_changed(self,words=False):
        """The running plugins changed: give the AI their tools and personality, hear new words."""
        plugins=self.plugins
        if self.tasks is not None:
            self.tasks.set_plugin_specialists(plugins.specialists())
        self.conversation.set_plugins(plugins.tools(),plugins.tool_risks(),plugins.instructions())
        if words and self.voice:
            self.voice.reload_words()

    def current_page(self):
        for page in self.pages:
            if page.name==self.page:
                return page
        return self.pages[1]  # Home

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
            log.warning('Could not save face settings: %s',exc)

    def handle_touch(self,x,y,t):
        """A tap on the logical canvas: Luna's Yes/No question, quit, a navigation button, or the
        shown page."""
        self.conversation.touched(t)
        if self.conversation.confirmation is not None and CONFIRM_RECT.collidepoint(x,y):
            for rect,yes in ((CONFIRM_YES_RECT,True),(CONFIRM_NO_RECT,False)):
                if rect.collidepoint(x,y):
                    self.run_effects(self.conversation.answer_touch(yes,t),t)
            return
        if QUIT_RECT.collidepoint(x,y):
            self.running=False
            return
        for page,rect in zip(self.pages,nav_rects(len(self.pages))):
            if rect.collidepoint(x,y):
                self.page=page.name
                return
        self.current_page().touch(self,x,y,t)

    def touch_home(self,x,y,t):
        """A tap on the face stops Luna talking, and she looks at it."""
        if speech.is_talking() or self.conversation.streaming or self.online.busy:
            self.interrupt_answer()
        self.face.look_at(x,y,t)

    def touch_settings(self,x,y):
        for rect,(kind,value) in self.settings_hits:
            if rect.collidepoint(x,y):
                self.settings_choice(kind,value)
                return

    def settings_key(self,event):
        """Typing a new robot name, or Up/Down for the sections; True when the key was used."""
        if self.name_editor is not None:
            if event.key==pygame.K_BACKSPACE:
                self.edit_name('name_back',None)
            elif event.key in (pygame.K_RETURN,pygame.K_KP_ENTER):
                self.edit_name('name_save',None)
            elif event.key==pygame.K_ESCAPE:
                self.edit_name('name_cancel',None)
            elif event.unicode:
                self.edit_name('name_key',event.unicode)
            return True
        if event.key in (pygame.K_UP,pygame.K_DOWN):
            step=-1 if event.key==pygame.K_UP else 1
            self.settings_section=(self.settings_section+step)%len(SECTIONS)
            return True
        return False

    def settings_choice(self,kind,value):
        if kind=='section':
            self.settings_section=value
            self.name_editor=None
            self.plugin_open=None
        elif kind.startswith('plugin_'):
            self.plugin_choice(kind,value)
        elif kind.startswith('name_'):
            self.edit_name(kind,value)
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
            changed=apply_choice(self.prefs,kind,value,self.audio.echo_cancelling)
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
            elif changed=='ai_daily_budget':
                self.budget.daily_usd=self.prefs['ai_daily_budget']
            elif changed=='tasks' and self.tasks is not None:
                self.tasks.set_enabled(self.prefs['tasks'])
            elif changed=='quiet_hours' and self.conversation.announcer is not None:
                self.conversation.announcer.quiet=self.prefs['quiet_hours']
            elif changed=='task_history_days' and self.tasks is not None:
                self.tasks.set_history(self.prefs['task_history_days'])
            elif changed=='memory':
                self.conversation.set_memory(self.memory if self.prefs['memory'] else None)
            elif changed=='online_listening' and not self.prefs['online_listening']:
                self.online.cancel()
                self.ring.clear()
            if changed:
                self.save()

    def plugin_choice(self,kind,value):
        """Settings > Plugins: open one, turn it on or off, allow a permission, change a setting."""
        if kind=='plugin_open':
            self.plugin_open=value
        elif kind=='plugin_back':
            self.plugin_open=None
        elif kind=='plugin_page':
            pages=plugin_pages(len(self.plugins.records))
            self.plugin_page=max(0,min(pages-1,self.plugin_page+value))
        elif kind=='plugin_toggle':
            self.plugins.set_enabled(value,not self.plugins.records[value].enabled)
        elif kind=='plugin_grant':
            plugin_id,permission=value
            self.plugins.grant(plugin_id,permission,permission not in self.plugins.records[plugin_id].granted)
        elif kind=='plugin_setting':
            self.plugins.set_setting(*value)

    def edit_name(self,kind,value):
        """The robot-name keyboard; a new name is saved and the app restarts."""
        if kind=='name_edit':
            self.name_editor=NameEditor(naming.name)
            return
        editor=self.name_editor
        if editor is None:
            return
        if kind=='name_key':
            editor.key(value)
        elif kind=='name_back':
            editor.back()
        elif kind=='name_cancel':
            self.name_editor=None
        elif kind=='name_save':
            knows=self.voice.knows if self.voice else (lambda word:None)
            result=editor.save(naming.name,knows)
            if result=='close':
                self.name_editor=None
            elif result=='restart':
                self.prefs['robot_name']=naming.clean_name(editor.text)
                self.save()
                log.info('Luna: new robot name %r, restarting',self.prefs['robot_name'])
                self.restart=True
                self.running=False

    def change_memory(self,change,*args):
        try:
            change(*args)
        except MEMORY_ERRORS as error:
            log.warning('Luna memory: could not be changed (%s)',type(error).__name__)

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
            if kind=='plugin':
                self.plugins.run_command(value,t)
        actions=[action for action in actions if action[0]!='plugin']
        changes=self.face.apply(actions,t)
        self.prefs.update(changes)
        if 'color' in changes:
            self.glow=glow_disc(169,self.accent)
        if actions:
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
        if self.luna_talking():
            self.hear_interruption(t)  # interrupt by voice is on: only "stop" or "Hey Luna"
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
            kind,value=route(done.text,conversation=done.conversation,language=self.heard_language)
            if (online and kind=='ai' and self.budget.allows()  # wake word or follow-up: may be sent
                    and not self.conversation.is_answer(value,self.heard_language)):
                audio=self.ring.since(self.start_time+done.started-PREROLL_SECONDS)
                self.ring.clear()
                self.conversation.heard,self.conversation.heard_kind=done.text,kind
                self.conversation.heard_until=t+3600
                heard=self.online.start(done,audio,self.heard_language,t)
                if heard:
                    self.route_heard(heard,t)
                return
            self.handle_speech(done.text,t,conversation=done.conversation)

    @property
    def voice_interrupt_on(self):
        return voice_interrupt_wanted(self.prefs,self.audio.echo_cancelling) and self.voice is not None

    def luna_talking(self):
        """Sound is playing or an answer is still arriving (not the quiet tail after it)."""
        return speech.is_talking() or self.conversation.streaming

    def hear_interruption(self,t):
        """Luna talks and interrupt by voice is on: only "stop" or the wake word count.

        Partial words are checked too, so she goes quiet as soon as "Luna" is heard; the rest of
        the question is then heard as usual. Anything else said meanwhile is dropped.
        """
        self.utterance.clear()
        texts=[]
        while True:
            try:
                texts.append(self.voice.texts.get_nowait())
            except queue.Empty:
                break
        for text,final in [(text,True) for text in texts]+[(self.voice.partial,False)]:
            kind,effects=self.conversation.barge_in(text,self.heard_language)
            if kind:
                self.online.cancel()
                self.run_effects(effects,t)
                if final and kind=='wake':
                    self.utterance.add_final(text,t,False)
                return

    def route_heard(self,heard,t):
        log.info('Luna online listening: %s; using the %s text (%d chars)',
                 heard.note,'online' if heard.online else 'offline',len(heard.text))
        # Vosk already authorised this question, so the online text needs no wake word.
        self.handle_speech(heard.text,t,conversation=True if heard.online else heard.utterance.conversation)

    def log_utterance(self,done):
        decode=getattr(self.voice,'decode_seconds',0.0)
        log.info('Luna heard: %d chars in %d part(s), %.1f s of speech, handled %.2f s after the '
                 'last words (%s); recogniser %.2f s CPU; dropped audio blocks %d',
                 len(done.text),done.parts,done.last_words-done.started,
                 done.finished-done.last_words,done.ending,decode-self.decode_mark,
                 getattr(self.voice,'dropped_blocks',0))
        self.decode_mark=decode

    def handle_speech(self,text,t,conversation=None):
        """Route one complete utterance to an offline command or the online AI.

        `conversation`: the follow-up window was open when the user started speaking.
        """
        self.last_input=t
        self.run_effects(self.conversation.hear(text,t,self.heard_language,self.talk_language,
                                                conversation),t)
        self.plugins.heard(text)

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
            if isinstance(effect,(Speak,Announce)):
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
                log.info('%s',effect.message)
            elif isinstance(effect,TaskStarted):
                log.info('Luna tasks: %s started by the AI',effect.task_id)

    def mic_muted(self):
        # Stay deaf between streamed sentences too, so Luna never hears herself; with interrupt
        # by voice on, listen while she talks (hear_interruption decides what counts).
        if self.voice_interrupt_on:
            return self.online.busy
        return self.conversation.muted(speech.is_speaking()) or self.online.busy

    def interrupt_answer(self):
        """The user stopped Luna: silence her and drop the unfinished answer (not remembered)."""
        self.forget_utterance()
        self.online.cancel()
        self.run_effects(self.conversation.interrupt(),0.0)

    def events(self,t):
        for event in pygame.event.get():
            if event.type in (pygame.KEYDOWN,pygame.MOUSEBUTTONDOWN,pygame.FINGERDOWN):
                self.last_input=t
            if event.type==pygame.QUIT:
                self.running=False
            elif event.type==pygame.VIDEORESIZE and not self.fullscreen:
                self.screen=pygame.display.set_mode((max(event.w,600),max(event.h,350)),pygame.RESIZABLE)
            elif event.type==pygame.KEYDOWN and self.current_page().key(self,event,t):
                continue
            elif event.type==pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running=False
                elif event.key==pygame.K_F11:
                    self.toggle_fullscreen()
                elif event.key==pygame.K_SPACE:
                    self.blink_start=t
                elif event.key==pygame.K_s:
                    self.interrupt_answer()
                elif event.unicode and event.unicode in '123456789' and int(event.unicode)<=len(self.pages):
                    self.page=self.pages[int(event.unicode)-1].name
            elif event.type in (pygame.MOUSEBUTTONDOWN,pygame.FINGERDOWN):
                point=self.touch.tap(event,self.screen.get_size(),t)
                if point is not None:
                    self.handle_touch(*point,t)

    def frame(self,t):
        self.poll_voice(t)
        self.plugins.tick(t)
        self.face.expire(t)
        self.canvas.blit(self.background,(0,0))
        self.update_preview()
        page=self.current_page()
        if page.chrome:
            draw_scifi_chrome(self.canvas,self.accent,self.motion_time(t))
        status_bar(self.canvas,self.fonts,self.accent)
        page.draw(self,self.canvas,t)
        nav_bar(self.canvas,self.pages,page.name,self.fonts,self.accent)
        if self.conversation.confirmation is not None:
            confirm_dialog(self.canvas,self.fonts,self.accent,self.conversation.confirmation)

    def frame_rate(self,t):
        """Frames per second for now: full speed while anything moves, slower when idle."""
        busy=(self.thinking or self.conversation.streaming or speech.is_talking() or bool(self.preview)
              or (self.voice is not None and self.utterance.pending))
        animated=self.prefs['animations'] and self.current_page().animated
        return pacing.frame_rate(self.frame_rates,busy,animated,t-self.last_input)

    def motion_time(self,t):
        """The animation clock (stands still when animations are off)."""
        return t if self.prefs['animations'] else 0.0

    def draw_home(self,t):
        """The Home page: the face, or the wave in wave mode, with what was heard and said."""
        accent=self.accent
        motion_t=self.motion_time(t)
        talking=speech.is_talking()
        face=self.face
        openness=face.move_eyes(t,self.thinking,self.thinking or self.conversation.streaming or talking,
                                self.prefs['animations'])
        if self.prefs['mode']=='WAVE':
            state,amp=face.wave(t,self.thinking,talking,self.voice.level if self.voice else 0.0)
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
            return
        dx,ey,sc,sy=LAYOUT_CHOICES[self.prefs['layout']]
        # The answer's expression is drawn above the user's mood, which stays as it was.
        mood='thinking' if self.thinking else (self.conversation.expression or face.mood)
        if mood=='surprised':
            sc*=1.12
        listening=bool(self.voice) and self.utterance.pending
        grow,bob=face_motion(motion_t,listening,talking,self.voice.level if self.voice else 0.0)
        sc*=grow
        ey+=bob
        if sy is not None:
            sy+=bob
        for ex,side in ((640-dx,-1),(640+dx,1)):
            draw_eye(self.canvas,ex,ey,motion_t,face.gaze_x,face.gaze_y,openness,accent,self.glow,sc,side,mood)
        if sy is not None:
            smile(self.canvas,t,accent,sy,mood,talking)
        hint=tr('SAY "HEY LUNA" AND ASK ANYTHING  •  TAP TO STOP TALKING' if self.ai else
                'TAP TO LOOK AROUND  •  SAY: HAPPY, SAD, ANGRY, LOOK LEFT...')
        self.draw_footer(t,hint)
        self.draw_caption(t)
        self.draw_you(t)

    def draw_settings(self):
        self.settings_hits=settings_page(self.canvas,self.fonts,self.accent,self.prefs,
                                         self.settings_section,self.preview,
                                         listening_status(self.voice,self.talk_language),
                                         online_status(self.prefs,self.ai is not None,
                                                       self.voice is not None),
                                         self.memory_view(),
                                         voice_interrupt_wanted(self.prefs,self.audio.echo_cancelling),
                                         audio_status(self.audio,self.voice is not None),
                                         self.name_editor,
                                         speech.voice_state() if speech.enabled else None,
                                         (self.plugins.view(),self.plugin_page,self.plugin_open),
                                         (self.usage.spent(),self.ai is not None),
                                         self.tasks is not None)

    def draw_footer(self,t,default):
        """Bottom text: Luna's answer while relevant, otherwise the hint."""
        if self.thinking:
            lines=[tr('LUNA IS STILL THINKING...' if self.conversation.slow else 'LUNA IS THINKING...')]
        elif self.conversation.answer and (speech.is_talking() or t<self.conversation.answer_until):
            lines=wrap_text(self.fonts['small'],naming.name.upper()+': '+self.conversation.answer,1040,2)
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
        size=self.screen.get_size()
        x,y,w,h=Viewport(*size).rectangle
        if (w,h)!=size:
            self.screen.fill((0,0,0))  # black bars around the picture
        if w==W and h==H:
            self.screen.blit(self.canvas,(x,y))
        else:
            if self._scaled is None or self._scaled.get_size()!=(w,h):
                self._scaled=pygame.Surface((w,h)).convert()
            pygame.transform.smoothscale(self.canvas,(w,h),self._scaled)
            self.screen.blit(self._scaled,(x,y))
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
                    log.info('Saved screenshot to %s',screenshot)
                    break
                self.clock.tick(self.frame_rate(t))
        finally:
            if self.restart:  # one frame so the screen does not just go black
                self.canvas.blit(self.background,(0,0))
                draw_text(self.canvas,tr('Restarting...'),self.fonts['title'],self.accent,center=(W//2,H//2))
                self.present()
            speech.stop()
            if self.voice:
                self.voice.stop()
            self.plugins.stop_all()
            if self.tasks is not None:
                self.tasks.close()
            self.hardware.close()
            pygame.quit()


def main():
    """Old start function (main.py now uses luna_core.cli)."""
    from .cli import main as start
    start()


if __name__=='__main__':
    main()
