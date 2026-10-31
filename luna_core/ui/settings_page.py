"""The Settings page: section menu, controls, status rows, memory list, name keyboard.

`settings_page` draws the selected section and returns [(rect, action)] for taps; the robot
(app.RobotHUD.settings_choice) carries the actions out.
"""
import math

import pygame

from .. import i18n, languages, naming
from ..i18n import tr
from ..preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS
from ..settings import KEYBOARD_ROWS, SECTIONS
from ..tasks.budget import BUDGET_CHOICES, WARN_AT
from ..voices import LANGUAGES as VOICE_LANGUAGES, get_voice, voices_for
from .draw import draw_arc, draw_text, line, mix, wrap_text
from .theme import (BACKGROUND, CONTENT_CLIP, CONTENT_RECT, CONTROL_RIGHT, CONTROL_X, LABEL_X,
                    MUTED, SECTION_RECTS, SIDEBAR_RECT, TEXT, WARNING)


def section_icon(surface, name, x, y, color):
    """Small line icons for the Settings sections."""
    if name == 'Display & Theme':
        pygame.draw.circle(surface,color,(x,y),15,2)
        for dx,dy,col in ((-6,-6,(34,216,255)),(4,-8,(184,113,255)),(8,2,(67,235,179))):
            pygame.draw.circle(surface,col,(x+dx,y+dy),3)
        pygame.draw.circle(surface,color,(x-5,y+6),4)
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
    elif name == 'AI':
        pygame.draw.rect(surface,color,(x-10,y-10,20,20),2,border_radius=4)
        for d in (-4,4):
            line(surface,color,(x+d,y-15),(x+d,y-11),2)
            line(surface,color,(x+d,y+11),(x+d,y+15),2)
            line(surface,color,(x-15,y+d),(x-11,y+d),2)
            line(surface,color,(x+11,y+d),(x+15,y+d),2)
    elif name == 'Plugins':
        for dx,dy in ((-7,-7),(7,-7),(-7,7),(7,7)):
            pygame.draw.rect(surface,color,(x+dx-6,y+dy-6,12,12),2,border_radius=3)
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


def voice_status(voice, speech_state):
    """(state, translated status line) for the selected voice.

    `speech_state`: speech.voice_state() as (state, status), or None when speech is off.
    """
    if speech_state is None:
        return 'off',tr('Speech is off (--no-speech)')
    state,status = speech_state
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


def budget_status(spent, daily, ai_on):
    """(state, translated line) for today's AI use in Settings."""
    if not ai_on:
        return 'basic',tr('Needs the AI (an OpenAI API key)')
    if spent>=daily:
        return 'basic',tr('Used up - AI answers return at midnight')
    state = 'loading' if spent>=WARN_AT*daily else 'natural'
    return state,tr('About ${spent} of ${budget} today',spent='%.2f'%spent,budget='%.2f'%daily)


def audio_status(audio, mic_on):
    """(state, translated line) for the microphone/speaker pair in Settings."""
    if not mic_on:
        return 'basic',tr('Microphone off')
    if audio.echo_cancelling:
        return 'natural',tr('Echo cancelling: {name}',name=audio.mic or tr('headset'))
    if audio.mic:
        return 'basic',tr('No echo cancelling: {name}',name=audio.mic)
    return 'basic',tr('No echo cancelling detected')


def toggle(surface, fonts, accent, on, y, hits, action, x=CONTROL_X):
    track = pygame.Rect(x,y,76,38)
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


PLUGIN_ROWS = 4  # plugins per page in Settings > Plugins
PLUGIN_DOTS = {'running':(67,235,179),'permission':(255,185,67),'off':MUTED}


def plugin_pages(count):
    return max(1,-(-count//PLUGIN_ROWS))


def plugin_status(view):
    from ..plugins.manager import STATUS_TEXT
    return tr(STATUS_TEXT.get(view.status,view.status))


def plugins_section(surface, fonts, accent, y, plugins, hits):
    """Settings > Plugins: the list with on/off switches, or one plugin's details.

    `plugins`: (views, page, open id); a view is plugins.manager.PluginView.
    """
    views,page,open_id = plugins
    shown = next((v for v in views if v.id==open_id),None)
    if shown is not None:
        plugin_details(surface,fonts,accent,y,shown,hits)
        return
    section_header(surface,fonts,'Plugins',y,'Apps that add to Luna')
    if not views:
        draw_text(surface,tr('No plugins yet.'),fonts['small'],TEXT,topleft=(LABEL_X,y+110))
        draw_text(surface,tr('Apps built on Luna Core add their plugins here.'),fonts['mini'],MUTED,
                  topleft=(LABEL_X,y+146))
        return
    pages = plugin_pages(len(views))
    page = min(page,pages-1)
    for i,view in enumerate(views[page*PLUGIN_ROWS:(page+1)*PLUGIN_ROWS]):
        row = pygame.Rect(LABEL_X,y+94+i*84,CONTROL_RIGHT-LABEL_X,76)
        pygame.draw.rect(surface,(8,32,62),row,border_radius=10)
        pygame.draw.rect(surface,(33,73,109),row,1,border_radius=10)
        pygame.draw.circle(surface,PLUGIN_DOTS.get(view.status,(255,110,110)),(row.left+20,row.top+24),7)
        draw_text(surface,wrap_text(fonts['small'],view.name,row.width-260,1)[0],fonts['small'],TEXT,
                  topleft=(row.left+38,row.top+12))
        detail = view.version+'  •  '+plugin_status(view)
        draw_text(surface,wrap_text(fonts['mini'],detail,row.width-260,1)[0],fonts['mini'],MUTED,
                  topleft=(row.left+38,row.top+44))
        if view.status not in ('broken','incompatible'):
            toggle(surface,fonts,accent,view.enabled,row.top+19,hits,('plugin_toggle',view.id),
                   x=row.right-180)
        add_hit(hits,row,('plugin_open',view.id))
    if pages>1:
        bottom = y+430
        for rect,(text,step) in zip(control_rects(bottom,2,40,60),(('<',-1),('>',1))):
            rect.x -= CONTROL_X-LABEL_X
            choice_button(surface,fonts,accent,rect,text,False,hits,('plugin_page',step))
        draw_text(surface,'%d / %d' % (page+1,pages),fonts['small'],MUTED,topleft=(LABEL_X+160,bottom+8))


def plugin_details(surface, fonts, accent, y, view, hits):
    """One plugin: status, on/off, its permissions (sensitive ones need a yes) and settings."""
    section_icon(surface,'Plugins',LABEL_X+8,y+32,TEXT)
    draw_text(surface,wrap_text(fonts['heading'],view.name,560,1)[0],fonts['heading'],TEXT,
              topleft=(LABEL_X+42,y+14))
    subtitle = tr('Version {version}',version=view.version)+('  •  '+view.description if view.description else '')
    draw_text(surface,wrap_text(fonts['mini'],subtitle,640,1)[0],fonts['mini'],MUTED,topleft=(LABEL_X+43,y+52))
    back = pygame.Rect(CONTROL_RIGHT-150,y+14,150,46)
    choice_button(surface,fonts,accent,back,tr('Back'),False,hits,('plugin_back',None))
    line(surface,(20,52,88),(CONTENT_CLIP.left+10,y+78),(CONTENT_CLIP.right-10,y+78),1)
    state = {'running':'natural','permission':'loading'}.get(view.status,'basic')
    status_row(surface,fonts,'Status',y+112,(state,plugin_status(view)))
    row_label(surface,fonts,'Use plugin',y+168)
    if view.status in ('broken','incompatible'):
        draw_text(surface,tr('OFF'),fonts['small'],MUTED,topleft=(CONTROL_X,y+156))
    else:
        toggle(surface,fonts,accent,view.enabled,y+149,hits,('plugin_toggle',view.id))
    row_label(surface,fonts,'Permissions',y+222)
    if not view.permissions:
        draw_text(surface,tr('None'),fonts['small'],MUTED,topleft=(CONTROL_X,y+210))
    for i,(name,sensitive,granted,description) in enumerate(view.permissions):
        top = y+204+i*46
        draw_text(surface,wrap_text(fonts['small'],tr(description),420,1)[0],fonts['small'],TEXT,
                  topleft=(CONTROL_X,top+6))
        if sensitive:
            toggle(surface,fonts,accent,granted,top,hits,('plugin_grant',(view.id,name)),x=CONTROL_X+440)
        else:
            draw_text(surface,tr('Allowed'),fonts['small'],MUTED,topleft=(CONTROL_X+440,top+6))
    top = y+214+max(1,len(view.permissions))*46
    if any(sensitive for _,sensitive,_,_ in view.permissions):
        draw_text(surface,tr('Only allow plugins you trust.'),fonts['mini'],WARNING,topleft=(CONTROL_X,top-6))
        top += 26
    for setting,value in view.settings:
        row_label(surface,fonts,setting.label,top+22)
        for choice,rect in zip(setting.choices,control_rects(top,len(setting.choices),44,180)):
            choice_button(surface,fonts,accent,rect,tr(choice),choice==value,hits,
                          ('plugin_setting',(view.id,setting.key,choice)))
        top += 58


def key_button(surface, fonts, accent, rect, text, hits, action, strong=False):
    pygame.draw.rect(surface,(8,32,62) if not strong else mix(accent,BACKGROUND,.55),rect,border_radius=10)
    pygame.draw.rect(surface,accent,rect,2,border_radius=10)
    draw_text(surface,text,fonts['small'],TEXT,center=rect.center)
    add_hit(hits,rect,action)


def name_keyboard(surface, fonts, accent, editor, y, hits):
    """The on-screen keyboard for a new robot name (replaces the section content)."""
    section_header(surface,fonts,'About',y,'Type a new name. Luna restarts to use it.')
    field = pygame.Rect(LABEL_X,y+92,520,54)
    pygame.draw.rect(surface,(8,34,66),field,border_radius=12)
    pygame.draw.rect(surface,accent,field,2,border_radius=12)
    draw_text(surface,editor.text+'|',fonts['body'],TEXT,topleft=(field.left+18,field.top+12))
    if editor.warning:
        kind,value = editor.warning
        text = (tr('Use 2 to 12 letters.') if kind=='letters' else
                tr('The speech model does not know "{name}", so the robot may not hear it. Tap Save again to keep it.',name=value))
        draw_text(surface,wrap_text(fonts['mini'],text,880,1)[0],fonts['mini'],WARNING,
                  topleft=(LABEL_X,y+156))
    for row,letters in enumerate(KEYBOARD_ROWS):
        for i,letter in enumerate(letters):
            rect = pygame.Rect(LABEL_X+i*76,y+186+row*64,66,54)
            key_button(surface,fonts,accent,rect,letter,hits,('name_key',letter))
    key_button(surface,fonts,accent,pygame.Rect(LABEL_X+7*76,y+314,218,54),tr('Delete'),hits,
               ('name_back',None))
    key_button(surface,fonts,accent,pygame.Rect(LABEL_X+446,y+390,180,54),tr('Cancel'),hits,
               ('name_cancel',None))
    key_button(surface,fonts,accent,pygame.Rect(LABEL_X+646,y+390,180,54),tr('Save'),hits,
               ('name_save',None),strong=True)


def settings_page(surface, fonts, accent, prefs, active, preview, listening=('basic',''),
                  online=('basic',''), memory=((),0,False,False), interrupt=False,
                  audio=('basic',''), editor=None, speech_state=None, plugins=((),0,None),
                  budget=(0.0,False)):
    """Draw the Settings page (only the selected section) and return [(rect, action)] for taps."""
    hits = [(rect,('section',i)) for i,rect in enumerate(SECTION_RECTS)]
    settings_sidebar(surface,fonts,accent,active)
    pygame.draw.rect(surface,(6,26,52),CONTENT_RECT,border_radius=18)
    pygame.draw.rect(surface,mix(accent,BACKGROUND,.6),CONTENT_RECT.inflate(6,6),2,border_radius=20)
    pygame.draw.rect(surface,accent,CONTENT_RECT,2,border_radius=18)
    voice = get_voice(prefs['voice'])
    name = SECTIONS[active]
    y = CONTENT_CLIP.top

    if editor is not None:
        name_keyboard(surface,fonts,accent,editor,y,hits)
    elif name=='Display & Theme':
        section_header(surface,fonts,name,y,'How Luna looks and the screen language')
        row_label(surface,fonts,'Mode',y+122)
        for mode,rect in zip(MODES,control_rects(y+99,2,46,220)):
            choice_button(surface,fonts,accent,rect,tr(mode+' MODE'),prefs['mode']==mode,hits,('mode',mode))
        row_label(surface,fonts,'Theme colour',y+200)
        for color,rect in zip(COLOR_CHOICES,control_rects(y+167,4,66,160)):
            choice_button(surface,fonts,accent,rect,tr(color),prefs['color']==color,hits,('color',color),
                          swatch=COLOR_CHOICES[color])
        wave = prefs['mode']=='WAVE'
        current = prefs['wave_layout'] if wave else prefs['layout']
        choices = WAVE_LAYOUTS if wave else tuple(LAYOUT_CHOICES)
        row_label(surface,fonts,'Wave layout' if wave else 'Face layout',y+278)
        for layout,rect in zip(choices,control_rects(y+255,3,46,220)):
            choice_button(surface,fonts,accent,rect,tr(layout),current==layout,hits,('layout',layout))
        row_label(surface,fonts,'App language',y+356)
        codes = i18n.LANGUAGES
        for (code,label),rect in zip(codes.items(),control_rects(y+333,len(codes),46,220,
                                                                 per_row=CHOICES_PER_ROW)):
            choice_button(surface,fonts,accent,rect,label,prefs['language']==code,hits,
                          ('language',code))
        y += extra_rows_height(len(codes))
        draw_text(surface,tr('Luna listens, speaks and answers in the Voice language.'),
                  fonts['mini'],MUTED,topleft=(CONTROL_X,y+385))
        row_label(surface,fonts,'Animations',y+444)
        toggle(surface,fonts,accent,prefs['animations'],y+425,hits,('animations',None))
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
        status_row(surface,fonts,'Status',y+331,voice_status(voice,speech_state))
        status_row(surface,fonts,'Listening',y+391,listening)
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
        row_label(surface,fonts,'Interrupt by voice',y+375)
        toggle(surface,fonts,accent,interrupt,y+356,hits,('voice_interrupt',None))
        draw_text(surface,wrap_text(fonts['mini'],
                                    tr('Say "Luna, stop" while she talks. Needs a headset or an echo-cancelling mic.'),
                                    CONTROL_RIGHT-CONTROL_X-170,1)[0],
                  fonts['mini'],MUTED,topleft=(CONTROL_X+170,y+364))
        status_row(surface,fonts,'Audio',y+440,audio)
    elif name=='Memory':
        section_header(surface,fonts,name,y,'What Luna remembers about you')
        memory_section(surface,fonts,accent,prefs,y,memory,hits)
    elif name=='AI':
        section_header(surface,fonts,name,y,'Daily limit for paid AI use')
        row_label(surface,fonts,'Daily budget',y+122)
        for amount,rect in zip(BUDGET_CHOICES,control_rects(y+99,len(BUDGET_CHOICES),46,120)):
            choice_button(surface,fonts,accent,rect,'$%.2f'%amount,
                          abs(prefs['ai_daily_budget']-amount)<1e-6,hits,('ai_daily_budget',amount))
        spent,ai_on = budget
        status_row(surface,fonts,'Used today',y+200,budget_status(spent,prefs['ai_daily_budget'],ai_on))
        notes = (tr('Counts answers, web searches and online listening (an estimate from list prices).'),
                 tr('When it is used up, Luna still does offline commands until midnight.'))
        for i,text in enumerate(notes):
            draw_text(surface,wrap_text(fonts['mini'],text,860,1)[0],fonts['mini'],MUTED,
                      topleft=(LABEL_X,y+262+i*28))
    elif name=='Plugins':
        plugins_section(surface,fonts,accent,y,plugins,hits)
    else:
        section_header(surface,fonts,name,y)
        about = (tr('Luna 1.0 - personal robot for Raspberry Pi 5.'),
                 tr('Voice: {label} ({note}).',label=voice.label,note=tr(voice.license_note)),
                 tr('Natural voices use Piper. Check licences before sharing Luna.'),
                 tr('Settings save automatically.'))
        for i,text in enumerate(about):
            draw_text(surface,wrap_text(fonts['small'],text,820,1)[0],fonts['small'],
                      TEXT if i==0 else MUTED,topleft=(LABEL_X,y+104+i*32))
        row_label(surface,fonts,'Robot name',y+283)
        draw_text(surface,naming.name,fonts['body'],TEXT,topleft=(CONTROL_X,y+270))
        button = pygame.Rect(CONTROL_X+260,y+260,200,46)
        choice_button(surface,fonts,accent,button,tr('Change'),False,hits,('name_edit',None))
        draw_text(surface,tr('Say the new name to wake the robot. It restarts after a change.'),
                  fonts['mini'],MUTED,topleft=(CONTROL_X,y+322))
    return hits
