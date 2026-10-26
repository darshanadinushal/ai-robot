"""Measure Luna's listening with the real Vosk models (laptop or Raspberry Pi).

Piper speaks test questions (with pauses), the audio goes through VoiceListener and the
UtteranceAssembler on a simulated clock, and the table shows when Luna would have acted.
It also times Piper's first sentence and shows CPU and memory use.
Needs the English and Swedish Vosk models and the Piper voices (installed by robot-setup).

    .venv/bin/python scripts/measure_listening.py           # timing table + pause sweep
    .venv/bin/python scripts/measure_listening.py --cases   # baseline sentences (tests/conversation_cases.json)

These are simulated (synthetic voice, no microphone). Real microphone numbers come from Luna's
own log on the Pi: "Luna heard: ..." and "Luna: first spoken reply ...".
"""
import array
import io
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot_hud import voice, voices  # noqa: E402
from robot_hud.commands import route, utterance_end  # noqa: E402
from robot_hud.listening import UtteranceAssembler, continue_seconds  # noqa: E402
from robot_hud.speech import PiperSynth  # noqa: E402

CASES = [
    ("EN_FEMALE", "command", ["Luna, look left."]),
    ("EN_FEMALE", "question", ["Hey Luna, what is the biggest planet in our solar system?"]),
    ("EN_FEMALE", "2 pauses", ["Hey Luna, can you tell me about", 0.9, "the planet Mars and", 1.2,
                               "why it looks red?"]),
    ("EN_FEMALE", "wake, 1.5 s, question", ["Hey Luna.", 1.5, "What time is it in Tokyo?"]),
    ("EN_FEMALE", "18 s, 4 pauses", [
        "Hey Luna, I am planning a trip", 0.8, "with my family to Sweden next summer and", 1.0,
        "we want to visit Stockholm, Gothenburg and the mountains in the north,", 0.9,
        "and we also like museums, boats and good food, so", 1.0,
        "which places should we visit first and how many days do we need?"]),
    ("SV_FEMALE", "command", ["Hej Luna, titta åt vänster."]),
    ("SV_FEMALE", "question", ["Hej Luna, vad heter Sveriges största sjö?"]),
    ("SV_FEMALE", "2 pauses", ["Hej Luna, kan du berätta om", 0.9, "planeten Mars och", 1.2,
                               "varför den är röd?"]),
    ("SV_MALE", "1.0 s pause", ["Hej Luna, hur är vädret i Stockholm", 1.0,
                                "under vintermånaderna?"]),
]
CASE_VOICES = {"en": "EN_FEMALE", "sv": "SV_FEMALE"}
FIRST_SENTENCE = {"en": "Mars is the fourth planet from the Sun.",
                  "sv": "Mars är den fjärde planeten från solen."}
SWEEP = [("EN_MALE", "Hey Luna, what is the weather like in Stockholm", "during the winter months?"),
         ("SV_MALE", "Hej Luna, hur är vädret i Stockholm", "under vintermånaderna?")]


def resample(data, rate, target):
    """16-bit mono PCM to another rate (linear; audioop is gone in Python 3.13)."""
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder == "big":
        samples.byteswap()
    count = int(len(samples) * target / rate)
    step, last = rate / target, len(samples) - 1
    out = array.array("h", [0]) * count
    for i in range(count):
        position = i * step
        j = int(position)
        a = samples[j]
        b = samples[j + 1] if j < last else a
        out[i] = int(a + (b - a) * (position - j))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def peak_memory_mb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KiB
    except ImportError:  # Windows
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                     wintypes.DWORD]
        kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters),
                                         counters.cb)
        return counters.PeakWorkingSetSize / 2 ** 20


def main():
    from vosk import KaldiRecognizer, Model, SetLogLevel
    started_cpu, started = time.process_time(), time.monotonic()
    SetLogLevel(-1)
    models = {code: Model(str(voice.model_path(code))) for code in ("en", "sv")}
    synths = {}

    def pcm(key, text):
        if key not in synths:
            synths[key] = PiperSynth(voices.VOICES[key])
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            synths[key]._piper.synthesize_wav(text, out)
        buf.seek(0)
        with wave.open(buf) as src:
            data, rate = src.readframes(src.getnframes()), src.getframerate()
        return resample(data, rate, voice.SAMPLE_RATE)

    def silence(seconds):
        return b"\0\0" * int(voice.SAMPLE_RATE * seconds)

    def run(key, parts):
        language = key[:2].lower()
        audio = b"".join(silence(p) if isinstance(p, float) else pcm(key, p) for p in parts)
        end = len(audio) / 32000
        audio += silence(4.0)
        listener = voice.VoiceListener(free_form=True, language=language)
        recognizer = KaldiRecognizer(models[language], voice.SAMPLE_RATE)
        assembler = UtteranceAssembler(lambda text, c: utterance_end(text, language, c),
                                       continue_seconds=continue_seconds(language))
        finals, sent, step = [], [], 4000  # 0.125 s steps
        for i in range(0, len(audio), step):
            t = (i + step) / 32000
            listener._step(recognizer, audio[i:i + step])
            while not listener.texts.empty():
                finals.append(t)
                assembler.add_final(listener.texts.get_nowait(), t)
            assembler.add_partial(listener.partial, t)
            done = assembler.poll(t)
            if done:
                sent.append((done, t))
        return end, finals, sent, listener.decode_seconds / (len(audio) / 32000)

    if "--cases" in sys.argv:
        cases = json.loads((ROOT / "tests" / "conversation_cases.json").read_text(
            encoding="utf-8"))["cases"]
        right = 0
        print("%-30s %-8s %-8s %s" % ("case", "expected", "heard as", "Vosk text"))
        for case in cases:
            language = case["language"]
            _, _, sent, _ = run(CASE_VOICES[language], [case["say"]])
            text = " ".join(d.text for d, _ in sent)
            got = route(text, case.get("conversation", False), language)[0] if sent else "nothing"
            right += got == case["route"]
            print("%-30s %-8s %-8s %s%s" % (case["id"], case["route"], got, text,
                                            "" if got == case["route"] else "   <-- differs"))
        print("\n%d of %d routed as expected" % (right, len(cases)))
        usage(started_cpu, started)
        return

    print("Piper, first sentence of an answer (synthesis only, before audio output):")
    for key in ("EN_FEMALE", "SV_FEMALE"):
        pcm(key, "Hello.")  # load the voice first
        begin = time.monotonic()
        pcm(key, FIRST_SENTENCE[key[:2].lower()])
        print("  %s: %.2f s" % (key, time.monotonic() - begin))
    print()
    print("%-9s %-22s %6s %6s %4s %9s %9s %6s" % ("voice", "case", "speech", "pieces", "sent",
                                                  "old: 1st", "new: sent", "CPU"))
    for key, name, parts in CASES:
        end, finals, sent, cpu = run(key, parts)
        print("%-9s %-22s %5.1fs %6d %4d %8.2fs %8.2fs %5.2fx  %s" % (
            key, name, end, len(finals), len(sent), finals[0] - end if finals else -1,
            sent[-1][1] - end if sent else -1, cpu,
            " | ".join("%s (%s)" % (d.text, d.ending) for d, _ in sent)))
    print("\nOne pause after a complete-sounding phrase: pauses (s) still joined")
    for key, first, second in SWEEP:
        joined = [p for p in (0.6, 0.8, 1.0, 1.2, 1.4, 1.6)
                  if len(run(key, [first, p, second])[2]) == 1]
        print("  %s (continue %.1f s): %s" % (key, continue_seconds(key[:2].lower()), joined))
    usage(started_cpu, started)


def usage(started_cpu, started):
    wall = time.monotonic() - started
    print("\nCPU %.0f%% of one core (all threads) over %.0f s; peak memory %.0f MB (both models loaded)"
          % (100 * (time.process_time() - started_cpu) / max(wall, 1e-9), wall, peak_memory_mb()))


if __name__ == "__main__":
    main()
