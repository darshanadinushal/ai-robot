"""`python -m luna_core.agents`: the task service process Luna starts (luna_core/tasks/process.py).

stdin and stdout carry the protocol, one JSON line per message. stdout is kept for that alone:
`print` and library output go to stderr, which Luna discards. Its log is luna-agents.log next
to Luna's own log. The service ends on `shutdown` or when Luna closes stdin.
"""
import asyncio
import logging
import os
import sys
import threading

from .. import ai, logs
from ..tasks.protocol import (FROM_SERVICE, MAX_PAUSED, TO_SERVICE, ProtocolError, decode,
                              encode)

log = logging.getLogger("luna.agents")


def _log_path():
    path = logs.log_file()
    return None if path is None else path.with_name("luna-agents.log")


def _quieter():
    try:
        os.nice(10)   # the face and listening come first on the Pi
    except (AttributeError, OSError):
        pass


async def serve(stdin, stdout):
    from .service import TaskService   # imports the SDK: only in this process

    loop = asyncio.get_running_loop()
    lock = threading.Lock()

    def write(message):
        line = encode(message, FROM_SERVICE).encode("utf-8")
        with lock:
            stdout.write(line)
            stdout.flush()

    service = TaskService(write)

    def received(line):
        try:
            message = decode(line, TO_SERVICE)
        except ProtocolError as error:
            log.warning("Luna agents: dropped a message (%s)", error)
            return
        service.handle(message)

    def reader():
        try:
            while True:
                line = stdin.readline(MAX_PAUSED + 2)
                if not line:
                    break
                if line.strip():
                    loop.call_soon_threadsafe(received, line)
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(service.stop)

    threading.Thread(target=reader, name="luna-agents-in", daemon=True).start()
    service.ready()
    await service.stopped.wait()
    await asyncio.sleep(0)


def main():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    _quieter()
    ai.load_env_file()
    logs.setup_logging(path=_log_path(), console=None)
    log.info("Luna agents: started")
    try:
        asyncio.run(serve(stdin, stdout))
    except KeyboardInterrupt:
        pass
    log.info("Luna agents: stopped")


if __name__ == "__main__":
    main()
