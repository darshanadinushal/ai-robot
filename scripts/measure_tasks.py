#!/usr/bin/env python3
"""Measure the task helper's cost on this machine (for docs/BASELINE.md, task service step 6).

    .venv/bin/python scripts/measure_tasks.py            # 3 cold starts, scripted tasks
    .venv/bin/python scripts/measure_tasks.py --runs 5

Needs the [agents] extra; never calls the real API (a scripted model: one web search, one
answer). Measures:
- cold start: the helper process from launch to its `ready` line (the SDK import);
- memory of the helper after `ready` and after a task (Linux: /proc; elsewhere psutil if
  installed);
- a task from `begin` to `done` through Luna's real TaskClient: cold (helper not running) and
  warm (helper already running);
- the UI thread: a 30 fps loop that polls the TaskClient like RobotHUD.frame does, idle and while
  the helper starts and works. Late frames (> 1.5 frame times) would mean a stutter on screen.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from luna_core import agents as luna_agents  # noqa: E402
from luna_core.tasks import process  # noqa: E402
from luna_core.tasks.catalog import catalog  # noqa: E402
from luna_core.tasks.client import TaskClient  # noqa: E402
from luna_core.tasks.store import TaskStore  # noqa: E402

FRAME = 1 / 30
SCRIPT = json.dumps([[{"search": "best robot vacuum"}],   # per task; one helper runs several
                     [{"say": "The Roborock S8 is the best robot vacuum.\n\nSources: example"}]] * 4)


def environment():
    env = dict(os.environ, LUNA_AGENT_SCRIPT=SCRIPT, LUNA_AI_WEB_SEARCH="on",
               LUNA_LOG_FILE=str(Path(tempfile.mkdtemp()) / "luna.log"))
    env.pop("OPENAI_API_KEY", None)
    return env


def memory_mb(pid):
    """(resident MB, peak MB) of `pid` and its children (the venv launcher on Windows)."""
    status = Path("/proc/%d/status" % pid)
    if status.exists():
        fields = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
        return tuple(int(fields[name].split()[0]) / 1024 for name in ("VmRSS", "VmHWM"))
    try:
        import psutil
    except ImportError:
        return (float("nan"), float("nan"))
    parent = psutil.Process(pid)
    found = [parent] + parent.children(recursive=True)
    rss = max(p.memory_info().rss for p in found) / 2 ** 20
    peak = max(getattr(p.memory_info(), "peak_wset", p.memory_info().rss) for p in found) / 2 ** 20
    return (rss, peak)


def cold_start(env):
    started = time.perf_counter()
    child = subprocess.Popen([sys.executable, "-m", "luna_core.agents"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=process.child_environment(env))
    line = child.stdout.readline()
    seconds = time.perf_counter() - started
    rss = memory_mb(child.pid)
    child.stdin.close()
    child.wait(10)
    if b'"ready"' not in line:
        raise SystemExit("the helper did not start: is the [agents] extra installed?")
    return seconds, rss


class Frames:
    """A 30 fps loop that polls the client, as RobotHUD.frame does."""

    def __init__(self, client):
        self.client, self.start = client, time.perf_counter()
        self.late = self.frames = 0
        self.poll_ms = []
        self.events = []

    def run(self, until, seconds=60.0):
        end = time.perf_counter() + seconds
        last = time.perf_counter()
        while time.perf_counter() < end:
            begun = time.perf_counter()
            self.events += self.client.poll(begun - self.start)
            self.poll_ms.append((time.perf_counter() - begun) * 1000)
            if until(self.events):
                return True
            time.sleep(max(0.0, FRAME - (time.perf_counter() - begun)))
            now = time.perf_counter()
            self.frames += 1
            self.late += now - last > FRAME * 1.5
            last = now
        return False


def task(client, frames, goal):
    started = time.perf_counter()
    found, _ = client.begin(goal, "research", "en", t=time.perf_counter() - frames.start)
    if found is None:
        raise SystemExit("the task did not start")
    count = len(frames.events)
    ok = frames.run(lambda events: any(e.kind in ("done", "failed")
                                       for e in events[count:]))
    kinds = [e.kind for e in frames.events[count:]]
    if not ok or "done" not in kinds:
        errors = [e.task.error for e in frames.events[count:] if e.kind == "failed"]
        raise SystemExit("the task did not finish: %s %s" % (kinds, errors))
    return time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="cold starts to measure")
    options = parser.parse_args()
    if not luna_agents.available():
        raise SystemExit('Install the agents extra first: pip install -e ".[agents]"')
    env = environment()

    starts = [cold_start(env) for _ in range(options.runs)]
    seconds = [s for s, _ in starts]

    store = TaskStore(Path(tempfile.mkdtemp()) / "tasks.db")
    client = TaskClient(lambda: process.ProcessTransport(env=env), store, None, None,
                        catalog=catalog(), lazy=True)
    frames = Frames(client)
    frames.run(lambda events: False, seconds=3.0)  # idle: no helper process
    idle = (frames.frames, frames.late, statistics.mean(frames.poll_ms))
    frames.frames = frames.late = 0
    frames.poll_ms = []
    cold = task(client, frames, "find the best robot vacuum")
    after = memory_mb(client.transport.process.pid) if client.transport else (float("nan"),) * 2
    warm = task(client, frames, "find the best robot vacuum again")
    busy = (frames.frames, frames.late, statistics.mean(frames.poll_ms), max(frames.poll_ms))
    client.close()
    store.close()

    print("| Measurement | Value |")
    print("|---|---|")
    print("| Helper cold start to `ready` (median of %d) | %.2f s (min %.2f, max %.2f) |"
          % (len(seconds), statistics.median(seconds), min(seconds), max(seconds)))
    print("| Helper memory after `ready` | %.0f MB |" % statistics.median(r[0] for _, r in starts))
    print("| Helper memory after a task (peak) | %.0f MB (%.0f MB) |" % after)
    print("| Scripted task, helper not running (cold) | %.2f s |" % cold)
    print("| Scripted task, helper running (warm) | %.2f s |" % warm)
    print("| UI frames idle: late / total, poll cost | %d / %d, %.3f ms |"
          % (idle[1], idle[0], idle[2]))
    print("| UI frames during tasks: late / total, poll cost (max) | %d / %d, %.3f ms (%.1f ms) |"
          % (busy[1], busy[0], busy[2], busy[3]))


if __name__ == "__main__":
    main()
