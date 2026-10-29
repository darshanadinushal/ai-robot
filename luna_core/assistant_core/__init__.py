"""Luna's assistant core: conversation logic without pygame, audio devices or motors.

    conversation.py  Conversation: routes complete utterances, runs the AI answer lifecycle
    session.py       Session (id, follow-up window) and Turn (one question being answered)
    context.py       what Luna is told with every question (instructions)
    events.py        effects the core asks the app to carry out (speak, act, stop...)

The app (luna_core/app.py) is the adapter: it feeds the core heard text, the AI worker's
messages and whether Luna is speaking, and carries out the returned effects on the UI thread.
Submodules are imported directly (no imports here) to keep this package free of cycles.
"""
