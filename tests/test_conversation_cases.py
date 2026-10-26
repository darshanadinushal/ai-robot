"""Phase 5.0 baseline: what Luna does with typical English and Swedish sentences today.

The sentences are in conversation_cases.json; scripts/measure_listening.py --cases speaks the
same sentences with Piper and runs them through the real Vosk models.
"""
import json
import unittest
from pathlib import Path

from robot_hud.commands import route, utterance_end

CASES = json.loads((Path(__file__).with_name("conversation_cases.json")).read_text(encoding="utf-8"))["cases"]


class ConversationCaseTests(unittest.TestCase):
    def test_baseline_covers_both_languages_and_all_routes(self):
        self.assertGreaterEqual(len(CASES), 30)
        self.assertEqual(len({case["id"] for case in CASES}), len(CASES))
        for language in ("en", "sv"):
            routes = {case["route"] for case in CASES if case["language"] == language}
            self.assertEqual(routes, {"command", "wake", "ai", "ignore"}, language)

    def test_routes_and_endings(self):
        for case in CASES:
            with self.subTest(case["id"]):
                text = case["say"].lower()  # Vosk gives lower case words without punctuation
                conversation = case.get("conversation", False)
                self.assertEqual(route(text, conversation, case["language"])[0], case["route"])
                if "ending" in case:
                    self.assertEqual(utterance_end(text, case["language"], conversation),
                                     case["ending"])


if __name__ == "__main__":
    unittest.main()
