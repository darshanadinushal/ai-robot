"""Luna's tools as seen by a specialist: proxies that ask Luna (the service never runs them).

For every tool Luna offers a task (the `context.tools` schemas in `start`), the agent gets a
FunctionTool. Before a call the SDK asks `needs_approval`: the proxy sends `check` and Luna
answers allow, refuse or confirm (confirm pauses the run until the user answers). The call
itself sends `tool_call`; Luna checks again, runs the tool and sends `tool_result`. Luna's
code decides every time: a refusal, an unanswered question or a missing tool is just text for
the model.
"""
import json

from agents import FunctionTool

NOT_ALLOWED = "Not allowed: %s"


def _args(value):
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def proxy(schema):
    """A FunctionTool that forwards `schema` (Responses API function format) to Luna."""
    name = schema["name"]

    async def needs_approval(context, params, call_id):
        job = context.context
        reply = await job.ask("check", "check_result", call=call_id, name=name, args=_args(params))
        reply = reply or {"verdict": "refuse", "reason": "no answer"}
        job.verdicts[call_id] = reply
        return reply["verdict"] == "confirm"

    async def invoke(context, arguments):
        job = context.context
        call = context.tool_call_id
        verdict = job.verdicts.pop(call, None)
        if verdict and verdict["verdict"] == "refuse":
            return NOT_ALLOWED % (verdict.get("reason") or "refused")
        reply = await job.ask("tool_call", "tool_result", call=call, name=name,
                              args=_args(arguments))
        if reply is None:
            return "No answer from Luna."
        return reply["output"] if reply["ok"] else "Failed: " + reply["output"]

    return FunctionTool(name=name, description=str(schema.get("description", "")),
                        params_json_schema=schema.get("parameters") or {"type": "object",
                                                                         "properties": {}},
                        on_invoke_tool=invoke, strict_json_schema=bool(schema.get("strict", True)),
                        needs_approval=needs_approval)


def proxies(context):
    tools = context.get("tools") if isinstance(context, dict) else None
    found = []
    for schema in tools if isinstance(tools, list) else ():
        if isinstance(schema, dict) and isinstance(schema.get("name"), str):
            found.append(proxy(schema))
    return found
