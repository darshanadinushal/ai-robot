"""The task service as a child process (no SDK here: the child imports it).

`ProcessTransport` starts `python -m luna_core.agents` and speaks JSON lines over its stdin and
stdout. Two small threads move the lines, so the UI thread never waits on a pipe: `send` only
queues a line and `receive` only takes the lines that have arrived. Its stderr is discarded
(the service writes its own log file). Closing asks the service to stop and kills it if it does
not exit within a few seconds, in the background. On Linux the child gets at most 2 malloc
arenas unless MALLOC_ARENA_MAX is set (as scripts/run-pi.sh does for Luna): less memory on the
Pi, and the helper waits on the network most of the time anyway.
"""
import logging
import os
import queue
import subprocess
import sys
import threading

from .protocol import MAX_PAUSED

log = logging.getLogger("luna.tasks")

COMMAND = (sys.executable, "-m", "luna_core.agents")
EXIT_WAIT = 3.0


def child_environment(env=None, platform=sys.platform):
    env = dict(os.environ if env is None else env)
    if platform.startswith("linux"):
        env.setdefault("MALLOC_ARENA_MAX", "2")
    return env


class ProcessTransport:
    def __init__(self, command=COMMAND, env=None):
        env = child_environment(env)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(list(command), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env=env, creationflags=flags)
        self._lines = queue.SimpleQueue()
        self._out = queue.SimpleQueue()
        self._broken = False
        threading.Thread(target=self._reader, name="luna-tasks-in", daemon=True).start()
        threading.Thread(target=self._writer, name="luna-tasks-out", daemon=True).start()

    def _reader(self):
        stream = self.process.stdout
        try:
            while True:
                line = stream.readline(MAX_PAUSED + 2)  # a longer line arrives in pieces: dropped
                if not line:
                    break
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._broken = True

    def _writer(self):
        stream = self.process.stdin
        try:
            while True:
                line = self._out.get()
                if line is None:
                    break
                stream.write(line)
                stream.flush()
        except (OSError, ValueError):
            self._broken = True
        try:
            stream.close()
        except (OSError, ValueError):
            pass

    @property
    def alive(self):
        """Running, or it still has lines to read."""
        return not self._lines.empty() or (not self._broken and self.process.poll() is None)

    def send(self, line):
        if self._broken or self.process.poll() is not None:
            raise OSError("the task service is not running")
        self._out.put(line.encode("utf-8"))

    def receive(self):
        lines = []
        while True:
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                return lines

    def close(self):
        self._out.put(None)  # closes its stdin: the service stops
        threading.Thread(target=self._reap, name="luna-tasks-stop", daemon=True).start()

    def _reap(self):
        try:
            self.process.wait(EXIT_WAIT)
        except subprocess.TimeoutExpired:
            log.info("Luna tasks: the service did not stop; ending it")
            self.process.kill()
            try:
                self.process.wait(EXIT_WAIT)
            except subprocess.TimeoutExpired:
                pass
        for stream in (self.process.stdout,):
            try:
                stream.close()
            except OSError:
                pass


def connect():
    """The TaskClient's connect function for the real service."""
    return ProcessTransport()
