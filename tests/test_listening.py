"""Phase 4.2: smart listening, one complete utterance from Vosk's pieces."""
import os
import queue
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from luna_core import transcription, voice
from luna_core.commands import utterance_end
from luna_core.listening import UtteranceAssembler
from luna_core.preferences import read_preferences
from test_streaming import FakeAI, TalkingSpeech


def assembler(language="en", **limits):
    return UtteranceAssembler(lambda text, conversation: utterance_end(text, language, conversation),
                              continue_seconds=0.6, long_pause_seconds=1.8, **limits)


def run(asm, script, until):
    """Feed a timed script of ("partial"|"final", text) at 0.1 s steps; return what came out."""
    done = []
    steps = {round(t, 1): (kind, text) for t, kind, text in script}
    t = 0.0
    while t <= until:
        key = round(t, 1)
        if key in steps:
            kind, text = steps[key]
            (asm.add_final if kind == "final" else asm.add_partial)(text, key)
            if kind == "final":
                asm.add_partial("", key)
        result = asm.poll(key)
        if result:
            done.append(result)
        t += 0.1
    return done


class EndpointTests(unittest.TestCase):
    def test_english_endings(self):
        self.assertEqual(utterance_end("luna look left"), "command")
        self.assertEqual(utterance_end("happy"), "command")
        self.assertEqual(utterance_end("hey luna"), "wake")
        self.assertEqual(utterance_end("luna what is the"), "unfinished")
        self.assertEqual(utterance_end("luna tell me about mars and"), "unfinished")
        self.assertEqual(utterance_end("luna what is the biggest planet"), "complete")
        self.assertEqual(utterance_end("luna look left and tell me why"), "complete",
                         "not a command: words inside a longer sentence")
        self.assertEqual(utterance_end("luna why are you sad today"), "complete")
        self.assertEqual(utterance_end("luna what is that"), "complete")
        self.assertEqual(utterance_end("luna where is it from"), "complete")

    def test_swedish_endings(self):
        self.assertEqual(utterance_end("luna titta åt vänster", "sv"), "command")
        self.assertEqual(utterance_end("hej luna", "sv"), "wake")
        self.assertEqual(utterance_end("hej luna jag vill ha en", "sv"), "unfinished")
        self.assertEqual(utterance_end("hej luna stäng av", "sv"), "complete")
        self.assertEqual(utterance_end("hej luna jag vill veta att", "sv"), "unfinished")
        self.assertEqual(utterance_end("hej luna vad heter du", "sv"), "complete")


class AssemblerTests(unittest.TestCase):
    def test_long_question_with_pauses_is_one_utterance(self):
        script = [(0.0, "partial", "luna what"), (0.4, "partial", "luna what is the"),
                  (0.9, "final", "luna what is the"),          # pause on "the": long wait
                  (2.0, "partial", "biggest"), (2.5, "final", "biggest planet"),
                  (2.9, "partial", "and"), (3.3, "final", "and how far away is it"),
                  (3.8, "partial", "from"), (4.4, "final", "from the sun")]
        done = run(assembler(), script, 10)
        self.assertEqual(len(done), 1, "sent exactly once")
        self.assertEqual(done[0].text,
                         "luna what is the biggest planet and how far away is it from the sun")
        self.assertEqual(done[0].parts, 4)
        self.assertEqual(done[0].ending, "complete")
        self.assertAlmostEqual(done[0].finished, 4.4 + 0.6 + 0.3 + 0.32, delta=0.11)

    def test_swedish_long_question(self):
        script = [(0.0, "final", "hej luna vad är skillnaden mellan en"), (1.5, "partial", "planet"),
                  (2.0, "final", "planet och"),
                  (3.5, "final", "en stjärna")]
        done = run(assembler("sv"), script, 8)
        self.assertEqual([d.text for d in done],
                         ["hej luna vad är skillnaden mellan en planet och en stjärna"])

    def test_long_questions_get_a_little_more_time(self):
        asm = assembler()
        long_piece = "luna i want to visit stockholm gothenburg and the mountains in the north"
        asm.add_final(long_piece, 0.0)
        self.assertIsNone(asm.poll(0.7), "14 words: 0.6 + 0.24 s")
        self.assertIsNotNone(asm.poll(0.85))
        asm.add_final("luna what is mars", 1.0)
        asm.add_final("made from", 1.5)
        self.assertIsNone(asm.poll(2.1), "already paused once: 0.6 + 0.3 s")
        self.assertEqual(asm.poll(2.4).text, "luna what is mars made from")
        asm.add_final(" ".join(["word"] * 100), 3.0)
        asm.add_final("end", 3.5)
        self.assertIsNone(asm.poll(3.5 + 1.4))
        self.assertIsNotNone(asm.poll(3.5 + 1.5), "at most 0.6 + 0.6 + 0.3 s")

    def test_partial_speech_holds_the_question(self):
        asm = assembler()
        asm.add_final("luna what is mars", 0.0)
        asm.add_partial("made", 0.3)
        for t in (0.9, 2.0, 5.0):
            self.assertIsNone(asm.poll(t), "the user is still talking")
        self.assertEqual(asm.display(), "luna what is mars made")
        asm.add_final("made from", 5.1)
        asm.add_partial("", 5.1)
        self.assertIsNone(asm.poll(5.9))
        self.assertEqual(asm.poll(6.0).text, "luna what is mars made from")

    def test_short_command_has_no_added_delay(self):
        asm = assembler()
        asm.add_final("luna look left", 1.0)
        done = asm.poll(1.0)
        self.assertEqual((done.text, done.ending), ("luna look left", "command"))
        self.assertIsNone(asm.poll(1.1))
        asm.add_final("titta åt vänster", 2.0)
        self.assertIsNone(asm.poll(2.0), "Swedish words are no English command")
        self.assertEqual(asm.poll(2.6).ending, "complete")
        sv = assembler("sv")
        sv.add_final("titta åt vänster", 3.0)
        self.assertEqual(sv.poll(3.0).ending, "command")

    def test_command_followed_by_more_speech_waits(self):
        asm = assembler()
        asm.add_final("luna look left", 1.0)
        asm.add_partial("and tell", 1.0)
        self.assertIsNone(asm.poll(1.0))
        asm.add_final("and tell me why", 1.6)
        asm.add_partial("", 1.6)
        self.assertIsNone(asm.poll(2.4))
        done = asm.poll(2.5)
        self.assertEqual(done.text, "luna look left and tell me why")
        self.assertEqual(utterance_end(done.text), "complete")

    def test_wake_word_alone_waits_for_the_question(self):
        asm = assembler()
        asm.add_final("hey luna", 0.0)
        self.assertIsNone(asm.poll(1.0), "not a complete question yet")
        asm.add_partial("what time", 1.2)
        asm.add_final("what time is it", 1.8)
        asm.add_partial("", 1.8)
        self.assertIsNone(asm.poll(2.6))
        self.assertEqual(asm.poll(2.7).text, "hey luna what time is it")
        asm.add_final("luna", 5.0)
        self.assertIsNone(asm.poll(6.7))
        self.assertEqual(asm.poll(6.8).ending, "wake")

    def test_no_duplicated_text_from_a_stale_partial(self):
        asm = assembler()
        asm.add_partial("luna what is", 0.0)
        asm.add_final("luna what is the moon", 0.5)
        asm.add_partial("luna what is the moon", 0.5)  # read before Vosk cleared it
        self.assertEqual(asm.display(), "luna what is the moon")
        self.assertEqual(asm.poll(1.1).text, "luna what is the moon")
        self.assertIsNone(asm.poll(3.0))

    def test_conversation_window_counts_when_speaking_started(self):
        asm = assembler()
        asm.add_partial("and why", 7.5, conversation=True)
        asm.add_final("and why is it red", 8.5, conversation=False)
        asm.add_partial("", 8.5)
        done = asm.poll(9.1)
        self.assertTrue(done.conversation)

    def test_time_and_length_limits(self):
        asm = assembler(max_seconds=45, max_chars=1000)
        for i in range(0, 50):
            asm.add_final("word%d is a very long piece of a never ending story" % i, i)
            asm.add_partial("and", i)
            done = asm.poll(i)
            if done:
                break
        self.assertEqual(done.ending, "limit")
        self.assertLessEqual(len(done.text), 1000)
        self.assertFalse(done.text.endswith(" "))
        self.assertIsNone(asm.poll(i + 0.1), "the rest of that speech is skipped")
        asm.add_final("tail of the long story", i + 0.5)
        asm.add_partial("", i + 0.5)
        self.assertIsNone(asm.poll(i + 2.0))
        asm.add_final("luna smile", i + 3.0)
        self.assertEqual(asm.poll(i + 3.0).text, "luna smile", "listening again")
        short = assembler(max_seconds=45, max_chars=30)
        short.add_partial("luna tell me everything about the solar system", 0.0)
        cut = short.poll(0.1)
        self.assertEqual((cut.ending, cut.text), ("limit", "luna tell me everything about"))

    def test_invalidated_speech_is_never_sent(self):
        asm = assembler()
        asm.add_final("luna what is the", 0.0)
        asm.invalidate(0.5)
        self.assertEqual(asm.display(), "")
        asm.add_partial("biggest planet", 0.7)
        asm.add_final("biggest planet", 1.0)
        asm.add_partial("", 1.0)
        for t in (1.0, 2.0, 4.0):
            self.assertIsNone(asm.poll(t))
        asm.add_final("luna hello there", 5.0)
        self.assertEqual(asm.poll(5.6).text, "luna hello there")

    def test_silence_and_noise_send_nothing(self):
        asm = assembler()
        for t in range(10):
            asm.add_partial("", t)
            self.assertIsNone(asm.poll(t))
        asm.add_partial("uh", 10.0)
        asm.add_partial("", 10.2)  # Vosk dropped it ([unk])
        self.assertIsNone(asm.poll(12.1))
        self.assertFalse(asm.pending)


class FakeRecognizer:
    def __init__(self, finals=()):
        self.finals, self.resets, self.fed = list(finals), 0, 0

    def AcceptWaveform(self, data):
        self.fed += 1
        return bool(self.finals) and self.finals[0] is not None and self.fed % 2 == 0

    def Result(self):
        return '{"text": "%s"}' % self.finals.pop(0)

    def PartialResult(self):
        return '{"partial": "talking"}'

    def Reset(self):
        self.resets += 1


class ListenerAudioTests(unittest.TestCase):
    def listener(self, muted=False):
        self.muted = muted
        return voice.VoiceListener(muted=lambda: self.muted)

    def test_microphone_buffer_is_bounded(self):
        listener = self.listener()
        blocks = listener.audio.maxsize
        self.assertEqual(blocks, int(voice.BUFFER_SECONDS * 4), "about 10 s of 0.25 s blocks")
        for _ in range(blocks + 5):
            listener._on_audio(b"\0\0" * voice.BLOCK_SAMPLES)
        self.assertEqual(listener.audio.qsize(), blocks)
        self.assertEqual(listener.dropped_blocks, 5)

    def test_overrun_while_listening_resets_and_is_reported(self):
        listener = self.listener()
        recognizer = FakeRecognizer()
        for _ in range(listener.audio.maxsize + 1):
            listener._on_audio(b"\0\0" * 10)
        listener._step(recognizer, b"\0\0" * 10)
        self.assertEqual((listener.overruns, recognizer.resets, listener.audio.qsize()), (1, 1, 0))
        self.assertEqual(recognizer.fed, 0, "audio with a gap is not recognised")
        listener._step(recognizer, b"\0\0" * 10)
        self.assertEqual(recognizer.fed, 1, "listening again")

    def test_stale_audio_is_dropped_while_muted(self):
        listener = self.listener(muted=True)
        recognizer = FakeRecognizer()
        for _ in range(6):
            listener._on_audio(b"\1\0" * 10)
        listener._step(recognizer, b"\1\0" * 10)
        self.assertEqual((listener.audio.qsize(), recognizer.fed, listener.overruns), (0, 0, 0))
        status = types.SimpleNamespace(input_overflow=True)
        listener._on_audio(b"\0\0", status)
        listener._step(recognizer, b"\0\0")
        self.assertEqual(listener.overruns, 0, "lost audio while muted does not matter")

    def test_results_go_to_texts(self):
        listener = self.listener()
        recognizer = FakeRecognizer(["luna what is", "the moon"])
        for _ in range(4):
            listener._step(recognizer, b"\0\0" * 10)
        self.assertEqual([listener.texts.get_nowait(), listener.texts.get_nowait()],
                         ["luna what is", "the moon"])
        self.assertGreater(listener.decode_seconds, 0.0)


class FakeListener:
    def __init__(self):
        self.language, self.active_language, self.status = "en", "en", "listening"
        self.partial, self.level = "", 0.0
        self.texts = queue.Queue()
        self.overruns, self.dropped_blocks, self.decode_seconds = 0, 0, 0.0

    def set_language(self, language):
        self.language = self.active_language = language

    def stop(self):
        pass


class ListeningAppTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        self.speech = TalkingSpeech()
        folder = tempfile.mkdtemp()
        for patch in (mock.patch.object(app, "speech", self.speech),
                      mock.patch.object(app, "read_preferences",
                                        lambda: read_preferences(Path(folder) / "p.json")),
                      mock.patch.object(app, "write_preferences", lambda prefs: None),
                      mock.patch.dict(os.environ, {"LUNA_VOICE": ""}),
                      mock.patch("builtins.print")):
            patch.start()
            self.addCleanup(patch.stop)
        self.hud = app.RobotHUD(types.SimpleNamespace(
            fullscreen=False, windowed=True, no_voice=True, no_speech=False, no_ai=True))
        self.addCleanup(app.pygame.quit)
        self.ai = self.hud.ai = FakeAI()
        self.mic = self.hud.voice = FakeListener()
        self.hud.utterance.long_pause = 1.8

    def say(self, t, final=None, partial=""):
        if final:
            self.mic.texts.put(final)
        self.mic.partial = partial
        self.hud.poll_voice(t)

    def test_long_question_reaches_the_ai_once(self):
        self.say(0.0, partial="luna what is")
        self.say(0.5, final="luna what is the")
        self.say(1.5)
        self.hud.frame(1.5)
        self.assertEqual(self.ai.asked, [], "a pause after 'the' is not the end")
        self.say(1.7, partial="biggest")
        self.assertEqual(self.hud.utterance.display(), "luna what is the biggest")
        self.say(2.2, final="biggest planet")
        self.say(3.0)
        self.assertEqual(self.ai.asked, [])
        self.say(3.1)
        self.assertEqual(self.ai.asked, [("what is the biggest planet", "en")])
        self.assertEqual(self.hud.conversation.heard, "luna what is the biggest planet")
        self.say(4.0)
        self.assertEqual(len(self.ai.asked), 1, "no repeated processing")

    def test_swedish_question(self):
        self.hud.settings_choice("voice_language", "sv")
        self.say(0.0, final="hej luna vad är")
        self.say(1.0, partial="en komet")
        self.say(1.4, final="en komet")
        self.say(2.5)
        self.assertEqual(self.ai.asked, [], "Swedish waits a little longer")
        self.say(2.6)
        self.assertEqual(self.ai.asked, [("vad är en komet", "sv")])

    def test_continue_seconds_per_language(self):
        from luna_core.listening import continue_seconds
        self.assertEqual((continue_seconds("en"), continue_seconds("sv")), (0.6, 0.9))
        with mock.patch.dict(os.environ, {"LUNA_CONTINUE_SECONDS": "1.2"}):
            self.assertEqual(continue_seconds("sv"), 1.2)

    def test_command_is_immediate(self):
        self.say(1.0, final="luna look left")
        self.assertEqual((self.hud.target_x, self.hud.target_y), (-1.0, 0.0))
        self.assertEqual(self.speech.spoken, ["Looking left."])

    def test_wake_word_alone_then_yes(self):
        self.say(0.0, final="hey luna")
        self.say(1.7)
        self.assertEqual(self.speech.spoken, [])
        self.say(1.8)
        self.assertEqual(self.speech.spoken, ["Yes?"])
        self.assertGreater(self.hud.conversation.session.followup_until, 1.8, "the question may follow")

    def test_luna_talking_clears_the_unfinished_question(self):
        self.say(0.0, final="luna tell me about")
        self.speech.talking = True
        self.mic.texts.put("stale words")
        self.say(0.5)
        self.assertFalse(self.hud.utterance.pending)
        self.assertTrue(self.mic.texts.empty(), "never hears herself")
        self.speech.talking = False
        self.say(5.0)
        self.assertEqual(self.ai.asked, [])

    def test_language_change_and_interruption_clear_it(self):
        self.say(0.0, final="luna what is the")
        self.hud.settings_choice("voice_language", "sv")
        self.assertFalse(self.hud.utterance.pending)
        self.say(0.1, final="luna what is the")
        self.hud.interrupt_answer()
        self.assertFalse(self.hud.utterance.pending)
        self.say(5.0)
        self.assertEqual(self.ai.asked, [])

    def test_lost_audio_drops_the_question(self):
        self.say(0.0, final="luna what is the")
        self.mic.overruns += 1
        self.say(0.3, partial="moon")
        self.say(0.8, final="moon")
        self.say(4.0)
        self.assertEqual(self.ai.asked, [], "never answer a question with missing words")
        self.say(6.0, final="luna what is the moon")
        self.say(6.6)
        self.assertEqual(self.ai.asked, [("what is the moon", "en")])

    def test_follow_up_started_inside_the_window(self):
        self.hud.conversation.session.followup_until = 8.0
        self.say(7.5, partial="and why")
        self.say(9.0, final="and why is it red")
        self.say(9.6)
        self.assertEqual(self.ai.asked, [("and why is it red", "en")])

    def test_streaming_answer_still_works_after_listening(self):
        self.say(0.0, final="luna tell me about mars")
        self.say(0.6)
        self.assertTrue(self.hud.mic_muted())
        self.ai.send("text", "Mars is red. ")
        self.ai.send("sentence", "Mars is red.")
        self.ai.send("done")
        self.mic.texts.put("echo of luna")
        self.say(0.7)
        self.assertEqual(self.speech.spoken, ["Mars is red."])
        self.assertTrue(self.mic.texts.empty())
        self.say(0.8)
        self.assertEqual(self.ai.confirmed, [1])
        self.assertEqual(len(self.ai.asked), 1)

    def test_live_bubble_shows_the_newest_words_of_a_long_question(self):
        self.say(0.0, final="luna " + " ".join("word%d" % i for i in range(80)))
        self.say(0.1, partial="last words")
        self.hud.frame(0.1)
        self.assertTrue(self.hud.utterance.display().endswith("word79 last words"))


class FakeTranscriptions:
    def __init__(self, text="What is the biggest planet?", error=None):
        self.text, self.error, self.calls = text, error, []

    def create(self, **args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return types.SimpleNamespace(text=self.text)


class FakeTranscriber:
    timeout = 8.0

    def __init__(self, text="", error=None, gate=None):
        self.text, self.error, self.gate, self.calls = text, error, gate, []

    def transcribe(self, pcm, language):
        self.calls.append((len(pcm), language))
        if self.gate is not None:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return self.text


def wait_for(online, t):
    for _ in range(500):
        heard = online.poll(t)
        if heard:
            return heard
        threading.Event().wait(0.005)
    raise AssertionError("no result")


SECOND = b"\1\0" * 16000


class OnlineListeningTests(unittest.TestCase):
    def test_ring_is_memory_only_bounded_and_trimmed(self):
        ring = transcription.AudioRing(limit_seconds=3)
        for second in range(5):
            ring.add(SECOND, 100.0 + second)
        self.assertEqual(ring.seconds, 3.0, "oldest audio dropped")
        self.assertEqual(len(ring.since(103.0)), 2 * len(SECOND))
        ring.trim(104.0)
        self.assertEqual(ring.seconds, 1.0)
        ring.clear()
        self.assertEqual(ring.since(0), b"")

    def test_request_options(self):
        options = transcription.request_options("gpt-transcribe", "sv")
        self.assertNotIn("language", options, "gpt-transcribe uses `languages` only")
        self.assertEqual(options["extra_body"]["languages"], ["sv"])
        self.assertIn("Luna", options["extra_body"]["keywords"])
        self.assertIn("Luna", options["prompt"])
        older = transcription.request_options("gpt-4o-mini-transcribe", "en")
        self.assertEqual(older["language"], "en")
        self.assertNotIn("extra_body", older)
        self.assertEqual(transcription._hint(" a <b>\r\n c "), "a b c")

    def test_cloud_transcriber_sends_a_wav_in_memory(self):
        api = FakeTranscriptions(text="  Hej   Luna  ")
        client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=api))
        with mock.patch.dict(os.environ, {"LUNA_TRANSCRIBE_MODEL": ""}):
            cloud = transcription.CloudTranscriber(client)
        self.assertEqual(cloud.transcribe(SECOND, "sv"), "Hej Luna")
        call = api.calls[0]
        self.assertEqual((call["model"], call["file"].name), ("gpt-transcribe", "speech.wav"))
        self.assertTrue(call["file"].closed, "audio dropped after the upload")

    def utterance(self, text="luna what is up"):
        return types.SimpleNamespace(text=text, conversation=False)

    def test_online_text_and_fallbacks(self):
        online = transcription.OnlineListening(FakeTranscriber("What is up?"))
        self.assertIsNone(online.start(self.utterance(), SECOND, "en", 1.0))
        self.assertTrue(online.busy)
        heard = wait_for(online, 2.0)
        self.assertEqual((heard.text, heard.online, online.busy), ("What is up?", True, False))
        self.assertNotIn("What", heard.note, "the log never contains the text")

        for transcriber, note in ((FakeTranscriber(error=TimeoutError("secret")), "TimeoutError"),
                                  (FakeTranscriber(""), "no words")):
            online = transcription.OnlineListening(transcriber)
            online.start(self.utterance(), SECOND, "en", 1.0)
            heard = wait_for(online, 2.0)
            self.assertEqual((heard.text, heard.online), ("luna what is up", False))
            self.assertIn(note, heard.note)
            self.assertNotIn("secret", heard.note)

        transcriber = FakeTranscriber("late")
        heard = transcription.OnlineListening(transcriber).start(self.utterance(), b"\0\0", "en", 1)
        self.assertEqual((heard.text, heard.online, transcriber.calls), ("luna what is up", False, []))

    def test_timeout_and_cancel(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        online = transcription.OnlineListening(FakeTranscriber("late", gate=gate), timeout=2.0)
        online.start(self.utterance(), SECOND, "en", 1.0)
        self.assertIsNone(online.poll(3.9))
        heard = online.poll(4.0)
        self.assertEqual((heard.text, heard.online), ("luna what is up", False))
        online.start(self.utterance(), SECOND, "en", 5.0)
        online.cancel()
        gate.set()
        self.assertIsNone(online.poll(6.0), "a cancelled question is forgotten")

    def test_listener_fills_the_ring_only_when_asked_and_never_while_muted(self):
        muted = []
        listener = voice.VoiceListener(muted=lambda: bool(muted))
        recognizer = FakeRecognizer()
        listener._step(recognizer, SECOND)
        listener.capture = ring = transcription.AudioRing()
        listener._step(recognizer, SECOND)
        self.assertEqual(ring.seconds, 1.0)
        muted.append(True)
        listener._step(recognizer, SECOND)
        self.assertEqual(ring.seconds, 0.0, "cleared while Luna speaks")


class OnlineAppTests(unittest.TestCase):
    setUp = ListeningAppTests.setUp
    say = ListeningAppTests.say

    def turn_on(self, transcriber):
        self.transcriber = transcriber
        self.hud.online = transcription.OnlineListening(transcriber)
        self.hud.prefs["online_listening"] = True

    def speak(self, final, start=0.0):
        self.hud.ring.add(SECOND, self.hud.start_time + start)
        self.say(start, final=final)
        self.say(start + 2.0)

    def settle(self, t=3.0):
        for _ in range(500):
            self.say(t)
            if not self.hud.online.busy:
                return
            threading.Event().wait(0.005)

    def test_question_uses_the_online_text(self):
        gate = threading.Event()
        self.turn_on(FakeTranscriber("Luna, what is the biggest planet in the solar system?",
                                     gate=gate))
        self.speak("luna what is the biggest planet in the soul or system")
        self.assertEqual(self.transcriber.calls, [(len(SECOND), "en")])
        self.assertTrue(self.hud.online.busy and self.hud.thinking and self.hud.mic_muted())
        self.assertEqual(self.ai.asked, [], "waits for the transcript")
        self.assertEqual(self.hud.ring.seconds, 0.0, "the audio left the ring")
        self.hud.frame(2.5)
        gate.set()
        self.settle()
        self.assertEqual(len(self.ai.asked), 1)
        self.assertIn("solar system", self.ai.asked[0][0])

    def test_commands_and_background_never_leave(self):
        self.turn_on(FakeTranscriber("x"))
        self.speak("luna look left")
        self.speak("we should buy milk later today", start=10.0)
        self.assertEqual(self.transcriber.calls, [])
        self.assertLess(self.hud.target_x, 0, "the command still works offline")

    def test_off_by_default(self):
        transcriber = FakeTranscriber("x")
        self.hud.online = transcription.OnlineListening(transcriber)
        self.speak("luna what is the moon")
        self.assertEqual(self.ai.asked, [("what is the moon", "en")], "offline text, at once")
        self.assertEqual((transcriber.calls, self.mic.capture), ([], None))

    def test_failure_falls_back_to_the_offline_text(self):
        self.turn_on(FakeTranscriber(error=ConnectionError()))
        self.speak("luna what is the moon")
        self.settle()
        self.assertEqual(self.ai.asked, [("what is the moon", "en")])

    def test_tap_cancels_the_transcription(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.turn_on(FakeTranscriber("tell me a joke", gate=gate))
        self.speak("luna tell me a joke")
        self.assertTrue(self.hud.online.busy)
        self.hud.interrupt_answer()
        gate.set()
        self.settle()
        self.assertEqual(self.ai.asked, [], "a cancelled question is not asked")


if __name__ == "__main__":
    unittest.main()
