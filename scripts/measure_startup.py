#!/usr/bin/env python3
"""Measure Luna's start-up time, memory and idle CPU (for docs/BASELINE.md).

    .venv/bin/python scripts/measure_startup.py              # 5 starts + 30 s idle
    .venv/bin/python scripts/measure_startup.py --runs 3 --idle 60 --real-hardware

Start-up: wall time of `main.py --headless --screenshot` (import, models start in the
background, first frame drawn, exit) and the peak resident memory of that process.
Idle (Linux): Luna runs headless for --idle seconds; CPU % and resident memory are read from
/proc at the end. Mock hardware by default, so no microphone is needed; voice, speech and AI
are as configured unless switched off with --no-voice/--no-speech/--no-ai.
"""
import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def luna_command(options, screenshot=None):
    command = [sys.executable, str(ROOT / "main.py"), "--headless"]
    if not options.real_hardware:
        command.append("--mock-hardware")
    command += [flag for flag in ("--no-voice", "--no-speech", "--no-ai", "--no-plugins")
                if getattr(options, flag[2:].replace("-", "_"))]
    if screenshot:
        command += ["--screenshot", str(screenshot)]
    return command


def environment():
    env = dict(os.environ, LUNA_LOG_FILE=os.environ.get("LUNA_LOG_FILE", "off"))
    if sys.platform.startswith("linux"):
        env.setdefault("MALLOC_ARENA_MAX", "2")  # as scripts/run-pi.sh
    return env


def peak_rss_mb(before):
    try:
        import resource
    except ImportError:
        return None
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # ru_maxrss of children is the largest child so far (kB on Linux, bytes on macOS).
    if after <= before:
        return None
    return after / (1024 * 1024 if sys.platform == "darwin" else 1024)


def measure_start(options):
    times, peaks = [], []
    with tempfile.TemporaryDirectory() as folder:
        for i in range(options.runs):
            try:
                import resource
                before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            except ImportError:
                before = 0
            started = time.perf_counter()
            result = subprocess.run(luna_command(options, Path(folder) / ("%d.png" % i)),
                                    env=environment(), capture_output=True, text=True, cwd=ROOT)
            seconds = time.perf_counter() - started
            if result.returncode:
                sys.exit("Luna did not start:\n" + result.stderr[-2000:])
            times.append(seconds)
            peak = peak_rss_mb(before)
            if peak:
                peaks.append(peak)
    print("Start to first frame (%d runs): median %.2f s, min %.2f s, max %.2f s"
          % (len(times), statistics.median(times), min(times), max(times)))
    if peaks:
        print("Peak memory of one start: %.0f MB" % max(peaks))


def proc_numbers(pid):
    stat = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()
    ticks = int(stat[11]) + int(stat[12])  # utime + stime
    rss_kb = threads = None
    for line in Path("/proc/%d/status" % pid).read_text().splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
        elif line.startswith("Threads:"):
            threads = int(line.split()[1])
    return ticks / os.sysconf("SC_CLK_TCK"), rss_kb, threads


def measure_idle(options):
    if not Path("/proc/self/stat").exists():
        print("Idle CPU and memory: Linux only (on the Pi also watch `top -p $(pgrep -f main.py)`).")
        return
    process = subprocess.Popen(luna_command(options), env=environment(), cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(options.settle)  # models load; not counted
        cpu0, _, _ = proc_numbers(process.pid)
        started = time.monotonic()
        time.sleep(options.idle)
        cpu1, rss_kb, threads = proc_numbers(process.pid)
        wall = time.monotonic() - started
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
    print("Idle over %.0f s (after %.0f s to settle): CPU %.1f %% of one core, memory %.0f MB, %s threads"
          % (wall, options.settle, 100 * (cpu1 - cpu0) / wall, (rss_kb or 0) / 1024, threads))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--idle", type=float, default=30, help="idle seconds measured (0: skip)")
    parser.add_argument("--settle", type=float, default=15, help="seconds before idle is measured")
    parser.add_argument("--real-hardware", action="store_true", help="the real microphone and speaker")
    for flag in ("--no-voice", "--no-speech", "--no-ai", "--no-plugins"):
        parser.add_argument(flag, action="store_true")
    options = parser.parse_args(argv)
    print("Luna Core at %s, Python %s" % (ROOT, sys.version.split()[0]))
    if options.runs > 0:
        measure_start(options)
    if options.idle > 0:
        measure_idle(options)


if __name__ == "__main__":
    main()
