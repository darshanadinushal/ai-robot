"""A minimal independent Luna Education plugin (no pygame, mic or device access)."""

from robot_hud.assistant_core.extensions import LunaExtension
from robot_hud.assistant_core.tools import Tool, ToolResult


def arithmetic_practice(arguments):
    if arguments["topic"] == "addition":
        question = "What is seven plus five?"
    else:
        question = "What is six times four?"
    # The AI receives a question to ask. This simple example keeps no student data.
    return [], ToolResult(True, "Ask the user this question.", {"question": question})


def extension():
    return LunaExtension(
        name="education",
        tools=(Tool(
            name="education_practice",
            description="Prepare one short arithmetic practice question for the user.",
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": ["addition", "multiplication"],
                        "description": "The type of practice question",
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            risk="local",
            run=arithmetic_practice,
        ),),
        abilities=("offer addition or multiplication practice questions",),
    )
