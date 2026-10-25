# Design: Natural voices for Luna (Piper) + Voice setting

Status: **design approved in principle — not implemented.** Implementation starts only when the
project owner says so.

## 1. Goal
- Give Luna a natural offline voice using **Piper**, with a **language (English / Svenska)** and **Female / Male** choice in Settings.
- Never break Luna: if Piper or a voice file is missing, keep speaking with the basic voice.
- Make voices **replaceable data**, so the provisional voices can later be swapped for
  commercially licensed ones without changing the engine.

## 2. Voices (English and Svenska from the start)
| Key | Language | Voice | Size | Licensing status |
| --- | --- | --- | --- | --- |
| `EN_FEMALE` | English | `en_US-lessac-medium` | ~63 MB | **Prototype only.** Training data has non-commercial restrictions. |
| `EN_MALE` | English | `en_US-ryan-high` | ~121 MB | **Prototype only.** Training data is CC BY-NC-SA 4.0. |
| `SV_FEMALE` | Svenska | `sv_SE-alma-medium` | ~63 MB | Model CC BY 4.0: attribution required. |
| `SV_MALE` | Svenska | `sv_SE-nst-medium` | ~63 MB | Training data CC0; model licence to confirm. |

Piper itself is GPL-3.0-or-later; eSpeak NG is GPL as well. Installing them separately does not
remove every obligation. **A commercial licensing review is a required gate before Luna is
distributed or sold** (see section 9).

Language affects more than the voice: in phase 2 Luna speaks with the voice of the selected
language and gender. Swedish speech recognition and Swedish AI answers are separate work
(a Swedish Vosk model and a language hint to the AI) and are not part of these phases.

## 3. Phases
| Phase | Scope | Project | Status |
| --- | --- | --- | --- |
| **1. Robot installer** | robot-setup "Natural voice" feature (`--with piper`): pinned `piper-tts` install, four voices (English + Swedish, female + male) downloaded to `~/.ai-robot-models/piper/` (outside Git) with size + SHA-256 checks, verify generates speech, listening check plays it, repair re-downloads | robot-setup | done (0.2.0), Pi test pending |
| **2. Voice engine** | Voice catalogue, Piper kept in memory, sentence pipeline (prepare next while playing), immediate stop, safe voice switching, eSpeak/Windows fallback, tests (section 4) | ai-robot | done, Pi test pending (headset audio) |
| **3. Settings** | Language (English / Svenska) and Female / Male buttons, preview phrase, saved preference, readiness/loading status | ai-robot | not started |
| **4. Response speed** | Stream the AI answer and speak each sentence as it arrives (today Luna waits for the full answer: 5-10 s) | ai-robot | not started |

The Pi is tested at the end of every phase. Luna's engine (phase 2) reads the voices from the
same folder and file names the installer uses; Luna does not download voices itself.

## 4. Phase 2 design — voice engine

### 4.1 Voice catalogue (`robot_hud/voices.py`, new, pure Python)
Each voice is a data record: `key`, `language` (en/sv), `label`, `gender` (female/male), Piper `model` name,
download location, `license_note`. Helper functions give the model file paths
(`~/.ai-robot-models/piper/<model>.onnx` + `.onnx.json`), download URLs, and "is it installed?".
Replacing a voice = editing this table (or adding a row), nothing else.

### 4.2 Engines
- **Piper engine:** loads the model **once** through Piper's Python API and keeps it in memory.
  No new process per sentence. Turns one sentence into a WAV file.
- **Fallback engine:** same job with the basic voice of the same gender —
  Pi/Linux: `espeak-ng` female/male variant of the selected language (`en-us`/`sv`) at 150 WPM; Windows: built-in voice chosen by gender.
Both engines produce WAV files, so playback is identical for both.

### 4.3 Speech pipeline (all off the touchscreen thread)
```text
speak_async(text)
   │
   v
[message queue] ─> PREPARER thread ─────────────> [ready queue, max 2] ─> PLAYER thread
                   clean text, split sentences      WAV files waiting       plays WAV, deletes it
                   synthesise sentence 1, 2, ...
```
- The preparer synthesises the **next sentence while the current one plays**, staying at most
  two sentences ahead (limits CPU and memory on the Pi).
- Very short fragments are merged with the next sentence to avoid choppy speech.
- Playback: Pi `pw-play` / `paplay` / `aplay`; Windows: built-in sound player.
- `is_talking()` stays true from the first queued message until the last sentence finishes;
  `is_speaking()` adds the 0.8 s echo tail used for microphone muting (unchanged behaviour).

### 4.4 Immediate interruption
`stop()` (tap on the face, `S` key) does all of this at once:
1. Increases a "generation" number so every queued message and prepared sentence becomes stale.
2. **Kills the audio that is playing right now** (no waiting for the sentence to end).
3. Empties the message queue and the ready queue and deletes their WAV files.
A sentence that is in the middle of synthesis cannot be cut, but its result is discarded
unheard. Nothing stale is ever played.

### 4.5 Safe voice switching
- The selected voice is a **preference** (Luna's existing settings file, Phase 3), kept
  separate from the engine.
- `set_voice(key)` loads the new voice **in the background**. The current voice keeps
  speaking meanwhile. Status: "Loading Ryan - US male...".
- Only after a successful load does Luna switch. If loading fails (Piper missing, file
  missing or damaged), Luna stays on the basic voice of the chosen gender and the status says
  why, e.g. "Basic voice - natural voice not downloaded".
- Only one Piper voice is kept in memory; the old one is released after the switch.
- The preference is saved as `voice` in Luna's preferences file (default `EN_FEMALE`). Until
  Phase 3 adds the setting, it can be chosen for testing with `LUNA_VOICE=SV_MALE` (not saved).

### 4.6 Voice files
Installed by robot-setup (phase 1). For Windows development, download the same pinned files
into `%USERPROFILE%\.ai-robot-models\piper\`.

### 4.7 Dependencies
`piper-tts` becomes an **optional** package (like vosk and openai). Luna starts without it.

### 4.8 What does not change
Microphone muting and echo tail, tap-to-stop behaviour, voice commands, AI answers, `--no-speech`.

### 4.9 Phase 2 tests (no audio, no downloads, no network)
- Catalogue: paths, URLs, unknown key falls back to the default voice.
- Sentence splitting and merging of short fragments.
- Pipeline with a fake engine and fake player: order is kept, next sentence is prepared
  ahead, `stop()` cancels playing and pending items, `is_talking()` returns to false.
- Voice switching with fake loaders: success switches; failure keeps the basic voice with a
  clear status; a slow load does not block speaking.

### 4.10 Phase 2 done when
Tests pass on Windows; both voices sound right on Windows; on the Pi (manual install) the first
sentence starts quickly, there are no long gaps between sentences, tap stops immediately, and
Luna does not hear herself.

## 5. Phase 3 design — Settings (summary)
Bottom rows: **ENGLISH | SVENSKA** and **FEMALE | MALE**, plus a status line. Tapping a choice
saves `voice` in `~/.ai-robot-hud.json`, calls `set_voice`, and plays "Hi, I'm Luna." once the
voice is ready. Old settings files default to `EN_FEMALE`. The preview phrase is spoken in the chosen language.

## 6. Phase 1 design — robot-setup (summary)
Extend the **Offline voice** feature: pinned `piper-tts` install in Luna's venv, voice
downloads with checksum/size check, `verify` reports "natural voice ready", `repair`
re-downloads, `remove` deletes Piper and the voices. Luna service keeps running on the
basic voice if anything fails.

## 7. Phase 4 design — response speed (summary)
Stream the AI answer; whenever a complete sentence arrives, pass it to the speech pipeline.
Luna starts talking after the first sentence instead of after the whole answer.

## 8. Risks
| Risk | Mitigation |
| --- | --- |
| Ryan "high" is slow on the Pi | Prepare-ahead pipeline; fall back to `en_US-ryan-medium` (catalogue change). |
| CPU contention with speech recognition | At most two sentences ahead; mic is muted while talking anyway. |
| Windows-only testing hides Pi problems | Pi check at the end of every phase. |

## 9. Licensing review (gate before distribution)
Before Luna is sold or given to others: review licences of Piper, eSpeak NG, Vosk and its model,
pygame, the OpenAI SDK, and every voice model; replace the provisional voices with commercially
licensed ones; decide how GPL obligations are met (or replace GPL components). Get proper legal
advice for this step.
