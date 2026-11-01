# Phase 5.0 baseline (Step 0)

Measured before the assistant-core work, so later phases can be compared.
Commit `ee48acb` (phase 4.2). Laptop = Windows dev laptop, simulated microphone.

## How to measure
```bash
.venv/bin/python scripts/measure_listening.py           # timing table + pause sweep
.venv/bin/python scripts/measure_listening.py --cases   # 34 baseline sentences through Vosk
```
These are **simulated**: Piper speaks into the real Vosk model and the real assembler, without a
microphone. On the Pi also run Luna normally and read the log
(`journalctl --user -f` or the terminal): `Luna heard: ...` (end of speech → routed) and
`Luna: first spoken reply X s after the question` (routed → first audible sentence).

## Laptop results (simulated)
| Case | Utterance done after speech end |
|---|---|
| EN command | 0.89 s |
| EN question | 1.50 s |
| EN question, 2 pauses | 2.09 s |
| EN wake + question | 1.88 s |
| EN 18 s, 4 pauses | 2.45 s |
| SV command | 0.62 s |
| SV question | 1.79 s |
| SV question, 2 pauses | 2.15 s |

- Pauses joined into one question: EN 0.6–1.2 s, SV 0.6–0.8 s (SV male voice, 1.0 s: 2 pieces).
- Vosk CPU: 0.08–0.12× real time (EN), 0.02× (SV).
- Piper first sentence synthesis: EN 0.05 s, SV 0.06 s.
- Process: CPU 842 % (all threads, mostly Piper/onnx) over 31 s; peak memory 1659 MB with both models.

## Baseline sentences (`--cases`): 29/34
All 5 misses are Swedish, and all because the small Swedish model mishears **"Luna"**
("lugnar", "lindblad", "gynnat"), so the wake word is lost:
`sv-command-mood`, `sv-question`, `sv-unfinished`, `sv-command-word-in-question`, `sv-ambiguous-dark`.
Fix candidates: add the common mishearings as Swedish wake words, or online transcription (phase 5.1).

## Raspberry Pi 5 (real microphone)
To be filled in after testing on the Pi: the same tables, plus `Luna heard` → `first spoken reply`
times, CPU (`top`), RAM and any `input overflow` (dropped microphone blocks) lines.

Start-up and idle cost (also to fill in on the Pi):
```bash
.venv/bin/python scripts/measure_startup.py                       # mock hardware, 5 starts + 30 s idle
.venv/bin/python scripts/measure_startup.py --real-hardware --idle 60
```
| Measurement | v1.0.0 | v1.0.1 (display/font-only pygame, 2 malloc arenas) |
|---|---|---|
| Start to first frame, laptop (`--no-voice --no-speech`) | 0.54 s | 0.42 s |
| Start to first frame, Pi 5 | | |
| Idle CPU / memory / threads, Pi 5 | | |

## Background task helper (task service step 6)
```bash
.venv/bin/python scripts/measure_tasks.py --runs 3    # needs the [agents] extra; no API key used
```
A scripted helper (one web search + answer, no real API) through the real `TaskClient` and
helper process, while a 30 fps loop checks for late frames. Memory needs `psutil` on Windows.

| Measurement | Laptop (Windows, Python 3.12) | Pi 5 |
|---|---|---|
| Helper cold start to `ready` | 1.76 s | |
| Helper memory after `ready` / after a task | about 99 MB (step 4) | |
| Scripted task, cold / warm helper | 1.83 s / 0.05 s | |
| Late UI frames idle / during tasks | 0 / 89, 0 / 55 | |
| UI poll cost during tasks, median (max) | 0.66 ms (13.3 ms) | |

Talk latency with tasks on must match the start-up and listening tables above.

Not changed on purpose: opening `DejaVuSans.ttf` directly would skip pygame's `fc-list` font
search at start, but `SysFont('dejavusans')` picks another DejaVu file (for example Condensed)
when `fonts-dejavu-extra` is installed, so text would look different on such systems.
