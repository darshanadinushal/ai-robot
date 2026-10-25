"""On-screen text in English and Swedish (Settings → Language).

The English text is the key: `tr("Home")` gives "Hem" when the app language is Swedish.
Only screen labels are translated; voice commands, spoken replies and AI answers stay English.
"""

LANGUAGES = {"en": "ENGLISH", "sv": "SVENSKA"}  # each shown in its own language
DEFAULT_LANGUAGE = "en"
language = DEFAULT_LANGUAGE

SWEDISH = {
    # bottom navigation
    "Chat": "Chatt", "Home": "Hem", "Camera": "Kamera", "Settings": "Inställningar",
    # settings menu
    "Display & Theme": "Skärm & tema", "Language": "Språk", "Voice": "Röst",
    "Animations": "Animationer", "About": "Om Luna",
    # display & theme
    "How Luna looks on the Home screen": "Hur Luna ser ut på hemskärmen",
    "Mode": "Läge", "Theme colour": "Temafärg", "Face layout": "Ansiktslayout",
    "Wave layout": "Våglayout", "FACE MODE": "ANSIKTSLÄGE", "WAVE MODE": "VÅGLÄGE",
    "CYAN": "CYAN", "PURPLE": "LILA", "GREEN": "GRÖN", "AMBER": "BÄRNSTEN",
    "CLASSIC": "KLASSISK", "COMPACT": "KOMPAKT", "MINIMAL": "MINIMAL",
    "MESH": "NÄT", "BARS": "STAPLAR", "CIRCLE": "CIRKEL",
    # language
    "The language of the texts on the screen": "Språket för texterna på skärmen",
    "App language": "Appens språk",
    "Luna listens, speaks and answers in the Voice language.":
        "Luna lyssnar, talar och svarar på röstspråket.",
    # voice
    "Voice language": "Röstens språk", "Voice type": "Rösttyp", "FEMALE": "KVINNA",
    "MALE": "MAN", "Test voice": "Testa rösten", "Play sample": "Spela prov",
    "Loading voice...": "Laddar rösten...", "Playing...": "Spelar...", "Status": "Status",
    "Natural voice: {label}": "Naturlig röst: {label}", "Loading {label}...": "Laddar {label}...",
    "Basic voice: {reason}": "Enkel röst: {reason}",
    "Speech is off (--no-speech)": "Tal är avstängt (--no-speech)",
    # animations
    "Blinking, looking around and moving effects": "Blinkningar, blickar och rörliga effekter",
    "ON": "PÅ", "OFF": "AV",
    # about
    "Luna 1.0 - personal robot for Raspberry Pi 5.": "Luna 1.0 - personlig robot för Raspberry Pi 5.",
    "Voice: {label} ({note}).": "Röst: {label} ({note}).",
    "prototype only: non-commercial training data": "endast prototyp: icke-kommersiell träningsdata",
    "prototype only: CC BY-NC-SA 4.0 training data": "endast prototyp: träningsdata CC BY-NC-SA 4.0",
    "CC BY 4.0: credit required": "CC BY 4.0: ange upphovsperson",
    "CC0 training data": "träningsdata CC0",
    "Natural voices use Piper. Check licences before sharing Luna.":
        "Naturliga röster använder Piper. Kontrollera licenser innan du delar Luna.",
    "Settings save automatically.": "Inställningar sparas automatiskt.",
    # chat and camera pages
    "AI VOICE ASSISTANT": "AI-RÖSTASSISTENT",
    "Microphone / AI integration is not connected yet.": "Mikrofon / AI är inte ansluten här ännu.",
    "Stop demo": "Stoppa demo", "Start waveform demo": "Starta vågformsdemo",
    "Waveform is simulated: no microphone recording.": "Vågformen är simulerad: ingen inspelning.",
    "CAMERA": "KAMERA", "NO CAMERA FEED": "INGEN KAMERABILD",
    "This is a placeholder, not a live image.": "Detta är en platshållare, inte en livebild.",
    # home screen
    'SAY "HEY LUNA" AND ASK ANYTHING  •  TAP TO STOP TALKING':
        'SÄG "HEY LUNA" OCH FRÅGA VAD DU VILL  •  TRYCK FÖR ATT AVBRYTA',
    "TAP TO LOOK AROUND  •  SAY: HAPPY, SAD, ANGRY, LOOK LEFT...":
        "TRYCK FÖR ATT TITTA RUNT  •  SÄG: HAPPY, SAD, ANGRY, LOOK LEFT...",
    "LISTENING...": "LYSSNAR...", "SPEAKING...": "PRATAR...", "QUIET...": "TYST...",
    "THINKING...": "TÄNKER...", "LUNA IS THINKING...": "LUNA TÄNKER...",
    "YOU: ": "DU: ", '  (say "Hey Luna" first)': '  (säg "Hey Luna" först)',
    "LISTENING FOR YOUR FOLLOW-UP...": "LYSSNAR EFTER DIN FÖLJDFRÅGA...",
    "MIC: ": "MIKROFON: ", "AI: ": "AI: ", "VOICE: ": "RÖST: ",
    "NATURAL": "NATURLIG", "LOADING": "LADDAR", "BASIC": "ENKEL",
    # microphone status (voice.VoiceListener.status)
    "Listening": "Lyssnar", "Listening in {language}": "Lyssnar på {language}",
    "Microphone off": "Mikrofonen är av", "English": "engelska", "Swedish": "svenska",
    "English - the {language} listening model is not installed":
        "Engelska - lyssningsmodellen för {language} är inte installerad",
    "loading": "laddar",
    "starting": "startar", "listening": "lyssnar", "voice packages missing": "röstpaket saknas",
    "voice model missing": "röstmodell saknas", "microphone error: ": "mikrofonfel: ",
}


def set_language(code):
    global language
    language = code if code in LANGUAGES else DEFAULT_LANGUAGE


def tr(text, **values):
    """Screen text in the app language; unknown text is shown unchanged."""
    result = SWEDISH.get(text, text) if language == "sv" else text
    return result.format(**values) if values else result
