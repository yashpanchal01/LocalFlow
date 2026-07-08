# LocalFlow

A local Wispr Flow clone: system-wide voice dictation, fully on-device.

- **Hotkey:** `Ctrl+Alt+/` — press once to start recording, press again to stop.
- **Transcription:** faster-whisper (`distil-whisper/distil-large-v3.5-ct2`) on the GPU (falls back to CPU `small.en`).
- **Cleanup:** Ollama (`qwen2.5:3b`) removes filler words, fixes punctuation, applies self-corrections. Falls back to a regex cleanup if Ollama is down.
- **Output:** pasted into whatever app has focus (clipboard is restored after).
- **Live preview:** a polished dark glass pill at the bottom of the screen shows the transcript growing with smooth easing animations (hard-S expand and fade-in), along with a blinking state dot.

## Run

- `LocalFlow.bat` — with console window (see logs, good for debugging).
- `LocalFlow-silent.vbs` — no window (everyday use). Quit from the tray icon.
- Autostart: put a shortcut to `LocalFlow-silent.vbs` in `shell:startup` (Win+R → `shell:startup`).

Ollama must be running (`ollama serve`, or the desktop app). If Ollama is down, the raw transcript is pasted uncleaned instead of failing.

## Tweak

All settings are at the top of `localflow.py`:

- `WHISPER_MODEL` — `tiny.en` (fastest) → `small.en` (default) → `distil-whisper/distil-large-v3.5-ct2` (most accurate, requires Developer Mode for symlinks).
- `OLLAMA_MODEL` — any model from `ollama list` (e.g. `gemma3:4b`, `hermes3:8b` for better cleanup at some latency cost).
- `HOTKEY` — configured via Win32 RegisterHotKey constants (`HOTKEY_MODS`, `HOTKEY_VK`, and `HOTKEY_NAME`).
- `CLEANUP_PROMPT` — how the LLM rewrites your speech.

