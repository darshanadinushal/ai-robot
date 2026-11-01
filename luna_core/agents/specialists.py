"""Agents built from Luna's catalogue (luna_core/tasks/catalog.py) for one task."""
from agents import Agent

from .. import languages
from ..tasks.catalog import ANSWER_FORMAT
from . import bridge, models


def language_name(code):
    try:
        return languages.get(code).name
    except Exception:
        return "English"


def build(spec, language, context, model_for=models.model_for):
    """The Agent for `spec`, answering in `language`, with proxies for Luna's offered tools."""
    instructions = (spec.instructions or spec.description) + "\n\n" + ANSWER_FORMAT.format(
        language=language_name(language))
    return Agent(name=spec.name, instructions=instructions, model=model_for(spec.model_role),
                 model_settings=models.settings(), tools=bridge.proxies(context))
