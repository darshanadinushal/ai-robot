"""Online AI brain for Luna (OpenAI Responses API). The API key comes from OPENAI_API_KEY."""
import os

DEFAULT_MODEL = "gpt-6-luna"
INSTRUCTIONS = (
    "You are Luna, a friendly AI robot. "
    "Give natural, concise, conversational answers suitable for speaking aloud."
)


class LunaBrain:
    def __init__(self, model=None):
        from openai import OpenAI  # imported lazily: the package is optional

        self.model = model or os.environ.get("LUNA_AI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI()
        self.previous_id = None

    def ask(self, question):
        args = {"model": self.model, "instructions": INSTRUCTIONS, "input": question}
        if self.previous_id:
            args["previous_response_id"] = self.previous_id
        response = self.client.responses.create(**args)
        self.previous_id = response.id
        return response.output_text
