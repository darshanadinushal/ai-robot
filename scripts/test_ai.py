"""Typed chat with Luna's AI brain. Needs: pip install openai, and OPENAI_API_KEY set."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("Set OPENAI_API_KEY first (never put the key in a file or commit it).")

from robot_hud.ai import LunaBrain  # noqa: E402

brain = LunaBrain()
print("Luna AI is ready (model: %s). Type 'exit' to stop." % brain.model)
while True:
    question = input("You: ").strip()
    if question.lower() == "exit":
        break
    if not question:
        continue
    try:
        print("Luna:", brain.ask(question))
    except Exception as error:
        print("AI error:", error)
