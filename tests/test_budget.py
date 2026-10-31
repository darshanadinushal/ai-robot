"""AI usage meter and daily budget (tasks/budget.py) and where Luna uses them."""
import json
import queue
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from luna_core.ai import AIWorker, web_searches
from luna_core.assistant_core.conversation import BUDGET_LOW, BUDGET_SPENT, Conversation
from luna_core.assistant_core.events import Accepted, Speak
from luna_core.preferences import read_preferences
from luna_core.tasks.budget import CostGuard, Prices, UsageMeter, default_path
from luna_core.transcription import CloudTranscriber

PRICES = Prices(models={"cheap": {"input": 1.0, "cached": 0.1, "output": 10.0},
                        "dear": {"input": 5.0, "cached": 0.5, "output": 50.0}},
                tools={"web_search": 0.01}, audio_minute={"gpt-transcribe": 0.006})


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 31, 23, 59)

    def __call__(self):
        return self.now


def meter(folder=None, clock=None):
    folder = folder or tempfile.mkdtemp()
    return UsageMeter(Path(folder) / "tasks.db", PRICES, clock or Clock())


class PriceTests(unittest.TestCase):
    def test_cost_of_tokens_calls_and_audio(self):
        # 1M input of which 400k cached, 100k output
        self.assertAlmostEqual(PRICES.tokens("cheap", 1_000_000, 400_000, 100_000),
                               0.6 + 0.04 + 1.0)
        self.assertAlmostEqual(PRICES.tokens("cheap-2026-10-01", 1_000_000, 0, 0), 1.0,
                               msg="a dated snapshot uses its model's price")
        self.assertAlmostEqual(PRICES.calls("web_search", 3), 0.03)
        self.assertAlmostEqual(PRICES.audio("gpt-transcribe", 30), 0.003)

    def test_unknown_names_cost_the_most_known(self):
        self.assertAlmostEqual(PRICES.tokens("mystery", 0, 0, 1_000_000), 50.0)
        self.assertAlmostEqual(PRICES.audio("mystery", 60), 0.006)

    def test_shipped_table_and_override_file(self):
        shipped = Prices.load(override="")
        self.assertIn("gpt-6-luna", shipped.models)
        self.assertIn("web_search", shipped.tools)
        with tempfile.TemporaryDirectory() as folder:
            extra = Path(folder) / "prices.json"
            extra.write_text(json.dumps({"models": {"gpt-6-luna": {"input": 9, "output": 9},
                                                    "bad": {"input": "x"}}}), encoding="utf-8")
            prices = Prices.load(override=str(extra))
        self.assertEqual(prices.models["gpt-6-luna"], {"input": 9.0, "cached": 9.0, "output": 9.0})
        self.assertNotIn("bad", prices.models)
        self.assertIn("gpt-transcribe", prices.audio_minute, "the rest of the table is kept")

    def test_default_file(self):
        self.assertEqual(default_path({"LUNA_TASKS_FILE": "t.db"}), Path("t.db"))
        self.assertEqual(default_path({}), Path.home() / ".luna" / "tasks.db")


class MeterTests(unittest.TestCase):
    def test_totals_are_kept_per_day_and_survive_a_restart(self):
        folder, clock = tempfile.mkdtemp(), Clock()
        first = meter(folder, clock)
        self.assertEqual(first.spent(), 0.0)
        self.assertFalse((Path(folder) / "tasks.db").exists(), "no file until something is used")
        first.add_tokens("talk", "cheap", 100_000, 0, 10_000)
        first.add_calls("talk", "web_search", 2)
        first.add_audio("listen", "gpt-transcribe", 60)
        self.assertAlmostEqual(first.spent(), 0.1 + 0.1 + 0.02 + 0.006)
        again = meter(folder, clock)
        self.assertAlmostEqual(again.spent(), first.spent())
        clock.now += timedelta(minutes=2)  # past midnight
        self.assertEqual(again.spent(), 0.0)
        self.assertAlmostEqual(again.spent("2026-10-31"), first.spent("2026-10-31"))

    def test_an_unusable_file_still_counts_in_memory(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "tasks.db").mkdir()  # a folder where the file should be
        broken = meter(folder)
        with self.assertLogs("luna.budget", "WARNING"):
            broken.add_tokens("talk", "cheap", 0, 0, 100_000)
        self.assertAlmostEqual(broken.spent(), 1.0)


class GuardTests(unittest.TestCase):
    def test_budget_warning_and_new_day(self):
        clock = Clock()
        usage = meter(clock=clock)
        guard = CostGuard(usage, 1.0)
        self.assertTrue(guard.allows())
        self.assertFalse(guard.warning_due())
        usage.add_tokens("talk", "cheap", 0, 0, 85_000)  # $0.85
        self.assertTrue(guard.allows())
        self.assertTrue(guard.warning_due())
        self.assertFalse(guard.warning_due(), "once a day")
        usage.add_tokens("talk", "cheap", 0, 0, 15_000)  # $1.00
        self.assertFalse(guard.allows())
        guard.daily_usd = 2.0
        self.assertTrue(guard.allows(), "a higher budget in Settings applies at once")
        guard.daily_usd = 1.0
        clock.now += timedelta(minutes=2)
        self.assertTrue(guard.allows(), "a new day")


class FakeBrain:
    model = "cheap"

    def __init__(self, searches=0):
        self.searches = searches

    def stream(self, question, language, instructions_text, result, **options):
        yield "Hello there."
        result["id"] = "r1"
        result["usage"] = (2000, 1000, 100)
        result["searches"] = self.searches

    def remember(self, response_id):
        pass


def wait_done(worker):
    while True:
        kind, _, _ = worker.answers.get(timeout=5)
        if kind in ("done", "error", "lost"):
            return kind


class TalkPathTests(unittest.TestCase):
    def test_each_completed_answer_is_counted(self):
        usage = meter()
        worker = AIWorker(lambda: FakeBrain(searches=1), meter=usage)
        worker.ask("hi")
        self.assertEqual(wait_done(worker), "done")
        expected = (1000 * 1.0 + 1000 * 0.1 + 100 * 10.0) / 1e6 + 0.01
        self.assertAlmostEqual(usage.spent(), expected)

    def test_web_searches_in_a_response(self):
        output = [types.SimpleNamespace(type="web_search_call"), types.SimpleNamespace(type="message"),
                  types.SimpleNamespace(type="web_search_call")]
        self.assertEqual(web_searches(types.SimpleNamespace(output=output)), 2)
        self.assertEqual(web_searches(types.SimpleNamespace(id="r1")), 0)

    def test_without_a_meter_nothing_changes(self):
        worker = AIWorker(FakeBrain)
        worker.ask("hi")
        self.assertEqual(wait_done(worker), "done")


class FakeAI:
    def __init__(self):
        self.asked, self.answers = [], queue.Queue()

    def ask(self, question, language, instructions_text=None, tools=None):
        self.asked.append(question)
        return len(self.asked)

    def cancel(self, request):
        pass


class ConversationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.usage = meter()
        self.ai = FakeAI()
        self.talk = Conversation(ai=self.ai)
        self.talk.budget = CostGuard(self.usage, 1.0)

    def test_over_budget_questions_never_reach_the_ai(self):
        self.usage.add_tokens("talk", "cheap", 0, 0, 100_000)  # $1.00
        effects = self.talk.ask("what is the weather", 0.0, "en")
        self.assertEqual(self.ai.asked, [])
        self.assertIn(Speak(BUDGET_SPENT), effects)
        self.assertEqual(self.talk.hear("luna turn left", 1.0, "en", "en")[0].__class__.__name__,
                         "Act", "offline commands still work")

    def test_luna_mentions_once_when_most_is_used(self):
        self.usage.add_tokens("talk", "cheap", 0, 0, 90_000)  # $0.90
        first = self.talk.ask("one", 0.0, "en")
        self.assertEqual(first[-2:], [Speak(BUDGET_LOW), Accepted("one")])
        self.talk.interrupt()
        self.assertNotIn(Speak(BUDGET_LOW), self.talk.ask("two", 1.0, "en"))
        self.assertEqual(self.ai.asked, ["one", "two"])


class OnlineListeningCostTests(unittest.TestCase):
    def test_transcribed_audio_is_counted(self):
        create = lambda **kwargs: types.SimpleNamespace(text="hello luna")
        client = types.SimpleNamespace(audio=types.SimpleNamespace(
            transcriptions=types.SimpleNamespace(create=create)))
        usage = meter()
        transcriber = CloudTranscriber(client=client, model="gpt-transcribe", meter=usage)
        self.assertEqual(transcriber.transcribe(b"\0\0" * 16000 * 30, "en"), "hello luna")
        self.assertAlmostEqual(usage.spent(), 0.003)


class PreferenceTests(unittest.TestCase):
    def test_only_offered_budgets_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            for saved, expected in ((1.0, 1.0), (0.5, 0.5), (7, 0.3), ("1.0", 0.3), (True, 0.3)):
                path.write_text(json.dumps({"ai_daily_budget": saved}), encoding="utf-8")
                self.assertEqual(read_preferences(path)["ai_daily_budget"], expected, saved)


if __name__ == "__main__":
    unittest.main()
