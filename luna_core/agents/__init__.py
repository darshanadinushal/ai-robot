"""Luna's task service: specialist agents in their own process (step 4 of docs/design/TASK_SERVICE.md).

This is the only package that imports the OpenAI Agents SDK (`pip install -e ".[agents]"`).
Luna never imports it: she starts `python -m luna_core.agents` when a task needs it
(luna_core/tasks/process.py) and talks to it with the JSON-lines protocol in
luna_core/tasks/protocol.py. Inside this package, `import agents` is the SDK (absolute import).
"""
import importlib.util


def available():
    """The Agents SDK is installed (checked without importing it: Luna's start-up stays fast)."""
    try:
        return importlib.util.find_spec("agents") is not None
    except (ImportError, ValueError):
        return False
