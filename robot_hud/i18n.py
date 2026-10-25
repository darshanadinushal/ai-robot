"""On-screen text in the app language (Settings → Language).

The English text is the key: `tr("Home")` gives "Hem" when the app language is Swedish.
Translations live in each language pack's "screen" table (robot_hud/languages/<code>.json);
texts a pack does not translate are shown in English. Only screen labels are translated here;
listening, spoken replies and AI answers follow the voice language.
"""
from . import languages

LANGUAGES = {code: pack.native_name.upper() for code, pack in languages.PACKS.items()}
DEFAULT_LANGUAGE = languages.DEFAULT
language = DEFAULT_LANGUAGE


def set_language(code):
    global language
    language = code if code in LANGUAGES else DEFAULT_LANGUAGE


def translations(code=None):
    """The screen table of a language (English text -> translated text)."""
    return languages.get(code or language).screen


def tr(text, **values):
    """Screen text in the app language; unknown text is shown unchanged."""
    result = translations().get(text, text)
    return result.format(**values) if values else result
