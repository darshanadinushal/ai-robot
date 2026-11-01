"""Which model each specialist uses, and the scripted test model.

`LUNA_AGENT_MODEL_<ROLE>` (e.g. LUNA_AGENT_MODEL_RESEARCH) picks the model for one role, else
`LUNA_AI_MODEL`, else Luna's default model. Cost settings are Luna's own:
LUNA_AI_MAX_OUTPUT_TOKENS caps each answer, LUNA_AI_REASONING sets the reasoning effort.

Web search (OpenAI-hosted, low context, billed per search) follows Luna's own switch:
LUNA_AI_WEB_SEARCH=off turns it off for helpers too.

Tests: `LUNA_AGENT_SCRIPT` holds a JSON list of model turns, each a list of
{"say": text}, {"call": tool, "args": {...}, "id": call id} or {"search": query} (a finished
hosted web search); every role then uses one shared scripted model (no network), so a paused
task resumes in the same script.
"""
import json
import os

from agents import ModelSettings

from .. import ai

_scripted = None


def model_name(role):
    if role:
        chosen = os.environ.get("LUNA_AGENT_MODEL_" + role.upper(), "").strip()
        if chosen:
            return chosen
    return os.environ.get("LUNA_AI_MODEL", "").strip() or ai.DEFAULT_MODEL


def settings():
    effort = ai.reasoning_effort()
    reasoning = None
    if effort:
        from openai.types.shared import Reasoning
        reasoning = Reasoning(effort=effort)
    return ModelSettings(max_tokens=ai.max_output_tokens(), reasoning=reasoning)


def web_search_on():
    return os.environ.get("LUNA_AI_WEB_SEARCH", "on").strip().lower() not in (
        "off", "0", "no", "false")


def hosted_tools(names):
    """The SDK tools for a specialist's `hosted` names (unknown names are skipped)."""
    from agents import WebSearchTool

    found = []
    if "web_search" in names and web_search_on():
        found.append(WebSearchTool(search_context_size="low"))
    return found


def web_search_call(query, item_id):
    from openai.types.responses import ResponseFunctionWebSearch
    from openai.types.responses.response_function_web_search import ActionSearch

    return ResponseFunctionWebSearch(id=item_id, action=ActionSearch(type="search", query=query),
                                     status="completed", type="web_search_call")


def scripted(script):
    """A ScriptedModel from the LUNA_AGENT_SCRIPT format."""
    from agents.testing import ScriptedModel, assistant_message, function_call
    from agents.usage import Usage

    turns = []
    for number, turn in enumerate(json.loads(script)):
        items = []
        for part in turn:
            if "say" in part:
                items.append(assistant_message(str(part["say"]), item_id="say-%d" % number))
            elif "search" in part:
                items.append(web_search_call(str(part["search"]), "search-%d" % number))
            else:
                items.append(function_call(part["call"], part.get("args", {}),
                                           call_id=part.get("id", "call-%d" % number)))
        turns.append(items)
    return ScriptedModel(turns, default_usage=Usage(requests=1, input_tokens=100,
                                                    output_tokens=20, total_tokens=120))


def model_for(role):
    """The model for `role`: a model name, or the shared scripted model in tests."""
    global _scripted
    script = os.environ.get("LUNA_AGENT_SCRIPT", "")
    if script:
        if _scripted is None:
            _scripted = scripted(script)
        return _scripted
    return model_name(role)
