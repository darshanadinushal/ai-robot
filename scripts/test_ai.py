"""Typed chat with Luna's AI brain, or a speed benchmark of reasoning efforts.

Needs: pip install openai, and OPENAI_API_KEY (environment or ~/.luna.env).
  python scripts/test_ai.py                          typed chat
  python scripts/test_ai.py --bench                  first sentence / whole answer times
  python scripts/test_ai.py --bench --efforts default,low,minimal --rounds 3
"default" sends no reasoning setting (the model's own default, as Luna does without
LUNA_AI_REASONING). Each benchmark question is asked without history.
"""
import argparse
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robot_hud.ai import REASONING_EFFORTS, LunaBrain, ai_configured  # noqa: E402
from robot_hud.assistant_core.context import instructions  # noqa: E402
from robot_hud.speech import SentenceStream  # noqa: E402

QUESTIONS = (
    ("en", "Why is the sky blue?"),
    ("en", "Can you explain how a rainbow forms, in a bit more detail?"),
    ("sv", "Vad heter Sveriges största ö?"),
    ("sv", "Kan du ge mig ett tips för att sova bättre?"),
)


def chat():
    brain = LunaBrain()
    print("Luna AI is ready (model: %s). Type 'exit' to stop." % brain.model)
    while True:
        question = input("You: ").strip()
        if question.lower() == "exit":
            break
        if question:
            try:
                print("Luna:", brain.ask(question))
            except Exception as error:
                print("AI error:", type(error).__name__)


def timed(brain, language, question):
    """Seconds to the first spoken sentence and to the whole answer."""
    started = time.monotonic()
    first = None
    sentences = SentenceStream()
    for piece in brain.stream(question, language, instructions(language, datetime.now()), {}):
        if first is None and sentences.feed(piece):
            first = time.monotonic() - started
    total = time.monotonic() - started
    return (first if first is not None else total), total


def bench(efforts, rounds):
    for effort in efforts:
        brain = LunaBrain()
        brain.reasoning = None if effort == "default" else effort
        firsts, totals, errors = [], [], []
        for _ in range(rounds):
            for language, question in QUESTIONS:
                brain.forget()
                try:
                    first, total = timed(brain, language, question)
                except Exception as error:
                    errors.append(type(error).__name__)
                    continue
                firsts.append(first)
                totals.append(total)
        if firsts:
            print("%-8s model %s: first sentence median %.2f s (max %.2f), whole answer median "
                  "%.2f s, %d answers" % (effort, brain.model, statistics.median(firsts),
                                          max(firsts), statistics.median(totals), len(firsts)))
        if errors:
            print("%-8s errors: %s" % (effort, ", ".join(sorted(set(errors)))))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--efforts", default="default,low",
                        help="comma list of: default, " + ", ".join(REASONING_EFFORTS))
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    if not ai_configured():
        sys.exit("Set OPENAI_API_KEY or put it in ~/.luna.env (never in the project).")
    if args.bench:
        efforts = [e.strip().lower() for e in args.efforts.split(",") if e.strip()]
        unknown = [e for e in efforts if e != "default" and e not in REASONING_EFFORTS]
        if unknown:
            sys.exit("Unknown effort: " + ", ".join(unknown))
        bench(efforts, max(1, args.rounds))
    else:
        chat()


if __name__ == "__main__":
    main()
