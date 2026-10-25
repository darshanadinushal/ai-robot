"""Measure Luna's listening with the real Vosk models (laptop or Raspberry Pi).

Piper speaks test questions (with pauses), the audio goes through VoiceListener and the
UtteranceAssembler on a simulated clock, and the table shows when Luna would have acted.
Needs the English and Swedish Vosk models and the Piper voices (installed by robot-setup).

    .venv/bin/python scripts/measure_listening.py
"""
import audioop
import io
import sys
import wave
import warnings
from pathlib import Path

warnings.simplefilter("ignore", DeprecationWarning)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robot_hud import voice, voices  # noqa: E402
from robot_hud.commands import utterance_end  # noqa: E402
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
SWEEP = [("EN_MALE", "Hey Luna, what is the weather like in Stockholm", "during the winter months?"),
         ("SV_MALE", "Hej Luna, hur är vädret i Stockholm", "under vintermånaderna?")]


def main():
    from vosk import KaldiRecognizer, Model, SetLogLevel
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
        return audioop.ratecv(data, 2, 1, rate, voice.SAMPLE_RATE, None)[0]

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


if __name__ == "__main__":
    main()
