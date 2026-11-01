"""The task service: runs specialist agents for Luna, one task at a time (asyncio).

`TaskService.handle` takes Luna's checked messages; `write` sends a message dict back. The
service keeps no state between runs: a paused run goes to Luna as a string (`paused`) and comes
back with the user's answer (`resume`). Limits come with every task: turns (`max_turns`) and
time (`max_seconds`) are enforced here, money by Luna from the `usage` messages. Errors are
reported by class name only (an API error text may hold private data). Tracing to OpenAI is
off unless LUNA_AGENT_TRACING=openai; Luna's own steps come from the run hooks.
"""
import asyncio
import logging
import os

from agents import RunConfig, RunHooks, RunState, Runner, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded

from ..tasks.catalog import catalog, describe
from ..tasks.model import Limits
from ..tasks.protocol import MAX_PAUSED, PROTOCOL_VERSION
from . import models, specialists

log = logging.getLogger("luna.agents")

ASK_WAIT = 10.0          # seconds Luna has to answer a check or a tool call
SUMMARY_CHARS = 300
DETAILS_CHARS = 16000
REJECTED = "The user said no."
OFFLINE = ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout")


def tracing():
    return os.environ.get("LUNA_AGENT_TRACING", "").strip().lower() == "openai"


def split_answer(text):
    """(summary to speak, details for the screen): the first paragraph, then the rest."""
    text = str(text or "").strip()
    first, _, rest = text.partition("\n\n")
    summary = " ".join(first.split())
    if len(summary) > SUMMARY_CHARS:
        cut = summary[:SUMMARY_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        summary = cut[:end + 1] if end > 80 else cut.rstrip() + "…"
        rest = text
    return summary, rest.strip()[:DETAILS_CHARS]


def error_code(error):
    name = type(error).__name__
    if name in OFFLINE:
        return "offline"
    return name[:40]


class Job:
    """One task's run: what it was started with, and its open questions to Luna."""

    def __init__(self, service, message, spec):
        self.service = service
        self.id = message["id"]
        self.spec = spec
        self.language = message["language"]
        self.context = message["context"]
        limits = message["limits"]
        try:
            self.limits = Limits(int(limits.get("max_turns", spec.limits.max_turns)),
                                 float(limits.get("max_seconds", spec.limits.max_seconds)),
                                 float(limits.get("max_usd", spec.limits.max_usd))
                                 ).within(spec.limits)
        except (TypeError, ValueError):
            self.limits = spec.limits
        self.model = service.model_for(spec.model_role)
        self.model_name = self.model if isinstance(self.model, str) else "scripted"
        self.agent = specialists.build(spec, self.language, self.context,
                                       model_for=lambda role: self.model)
        self.verdicts = {}
        self.waiting = {}
        self.task = None

    def send(self, t, **fields):
        self.service.send(dict(t=t, id=self.id, **fields))

    async def ask(self, kind, reply, call, **fields):
        """Send a check/tool_call and wait for Luna's reply (None if she does not answer)."""
        future = asyncio.get_running_loop().create_future()
        self.waiting[(reply, call)] = future
        self.send(kind, call=call, **fields)
        try:
            return await asyncio.wait_for(future, ASK_WAIT)
        except asyncio.TimeoutError:
            return None
        finally:
            self.waiting.pop((reply, call), None)

    def answer(self, message):
        future = self.waiting.get((message["t"], message["call"]))
        if future is not None and not future.done():
            future.set_result(message)


class Hooks(RunHooks):
    def __init__(self, job):
        self.job = job

    async def on_llm_end(self, context, agent, response):
        usage = response.usage
        details = getattr(usage, "input_tokens_details", None)
        self.job.send("usage", model=self.job.model_name, input=int(usage.input_tokens or 0),
                      cached=int(getattr(details, "cached_tokens", 0) or 0),
                      output=int(usage.output_tokens or 0))
        self.job.send("step", kind="model", name=self.job.model_name, summary="")

    async def on_tool_start(self, context, agent, tool):
        self.job.send("step", kind="tool", name=str(getattr(tool, "name", "tool"))[:80],
                      summary="")


class TaskService:
    def __init__(self, write, specialists=None, model_for=models.model_for, concurrency=1):
        self.write = write
        self.specialists = specialists or catalog()
        self.model_for = model_for
        self.jobs = {}
        self.slots = asyncio.Semaphore(concurrency)
        self.stopped = asyncio.Event()
        set_tracing_disabled(not tracing())
        self.config = RunConfig(tracing_disabled=not tracing(), trace_include_sensitive_data=False,
                                workflow_name="Luna task")

    def send(self, message):
        try:
            self.write(message)
        except Exception as error:
            log.warning("Luna agents: could not send %s (%s)", message.get("t"),
                        type(error).__name__)

    def ready(self):
        self.send({"t": "ready", "version": PROTOCOL_VERSION,
                   "specialists": [describe(spec) for spec in self.specialists.values()]})

    def handle(self, message):
        kind = message["t"]
        if kind in ("start", "resume"):
            self._begin(message)
        elif kind == "cancel":
            job = self.jobs.get(message["id"])
            if job is not None and job.task is not None:
                job.task.cancel()
        elif kind in ("check_result", "tool_result"):
            job = self.jobs.get(message["id"])
            if job is not None:
                job.answer(message)
        elif kind == "shutdown":
            self.stop()

    def stop(self):
        for job in list(self.jobs.values()):
            if job.task is not None:
                job.task.cancel()
        self.stopped.set()

    def _begin(self, message):
        task_id = message["id"]
        if task_id in self.jobs:
            return
        spec = self.specialists.get(message["specialist"])
        if spec is None:
            self.send({"t": "failed", "id": task_id, "error": "specialist"})
            return
        try:
            job = Job(self, message, spec)
        except Exception as error:
            log.warning("Luna agents: could not build %s (%s)", spec.name, type(error).__name__)
            self.send({"t": "failed", "id": task_id, "error": error_code(error)})
            return
        self.jobs[task_id] = job
        if message["t"] == "start":
            job.send("accepted")
            job.task = asyncio.ensure_future(self._run(job, message["goal"]))
        else:
            job.task = asyncio.ensure_future(self._resume(job, message))

    async def _resume(self, job, message):
        try:
            state = await RunState.from_string(job.agent, message["state"], context_override=job)
        except Exception as error:
            log.warning("Luna agents: paused run unreadable (%s)", type(error).__name__)
            return self._finish(job, failed="paused")
        found = [item for item in state.get_interruptions()
                 if getattr(item.raw_item, "call_id", None) == message["item"]]
        if not found:
            return self._finish(job, failed="paused")
        for item in found:
            if message["approved"]:
                state.approve(item)
            else:
                state.reject(item, rejection_message=REJECTED)
        await self._run(job, state)

    async def _run(self, job, start):
        try:
            async with self.slots:
                result = await asyncio.wait_for(
                    Runner.run(job.agent, start, context=job, max_turns=job.limits.max_turns,
                               hooks=Hooks(job), run_config=self.config),
                    job.limits.max_seconds)
        except asyncio.CancelledError:
            self.jobs.pop(job.id, None)   # Luna already stopped it: nothing to report
            return
        except asyncio.TimeoutError:
            return self._finish(job, failed="time")
        except MaxTurnsExceeded:
            return self._finish(job, failed="turns")
        except Exception as error:
            log.warning("Luna agents: task failed (%s)", type(error).__name__)
            return self._finish(job, failed=error_code(error))
        if result.interruptions:
            return self._pause(job, result)
        summary, details = split_answer(result.final_output)
        if not summary:
            return self._finish(job, failed="empty")
        self._finish(job, done=(summary, details))

    def _pause(self, job, result):
        item = result.interruptions[0]
        call = getattr(item.raw_item, "call_id", None)
        state = result.to_state().to_string(context_serializer=lambda job: {})  # nothing of Luna's
        self.jobs.pop(job.id, None)
        if not call or len(state.encode("utf-8")) > MAX_PAUSED - 4096:
            return self.send({"t": "failed", "id": job.id, "error": "paused"})
        job.send("paused", item=call, summary="", touch=False, state=state)

    def _finish(self, job, done=None, failed=None):
        self.jobs.pop(job.id, None)
        if done:
            job.send("done", summary=done[0], details=done[1])
        else:
            job.send("failed", error=failed)
