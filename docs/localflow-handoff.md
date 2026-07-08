# LocalFlow — session handoff

**Date:** 2026-07-08
**Repo:** `C:\Users\YASH PANCHAL\localflow` (git root confirmed here; branch `Backend_views`)
**App:** single-file local dictation tool (`localflow.py`, ~760 lines now). Win32 hotkey → faster-whisper → Ollama cleanup → clipboard paste, with a PySide6/Qt overlay. Background in the user's auto-memory `localflow-project.md`.

## Next session focus (from the user)
> "install the new model later; for now use whatever current model there is. Focus on the other 4 changes and the UI."

So: **item 1 (model swap) is parked**, items 2–5 stand and need a real smoke test, and **UI polish is the new headline task**.

---

## State of the code — all UNCOMMITTED, nothing staged

Commit policy agreed with the user: **the user runs the smoke test and commits; the agent stages nothing.** Honor this.

### Landed in `localflow.py` (headless-verified, see below)
- **Item 2 — `beam_size=5` on the final transcribe pass**; live-preview pass stays `beam_size=1`. (In `_process` vs `_live_preview`.)
- **Item 3 — paste reliability** (new module-level helpers `wait_modifiers_released`, `_process_integrity`, `foreground_is_elevated`, new `Notifier(QObject)` class, rewritten `App._paste` + new `App._restore_clip`):
  - waits for Ctrl/Alt to be physically released before synthesizing Ctrl+V,
  - detects an elevated/admin foreground window (integrity-level comparison) and shows a **tray balloon warning** instead of failing silently, leaving text on the clipboard for manual paste,
  - verify-before-restore: only restores the previous clipboard if our dictated text is still there (won't clobber a fresh copy). Restore delay kept at 1.0s.
- **Item 4 — cleanup LLM streams** (`App.cleanup_text` now `stream:True`, iterates `iter_lines()`, accumulates tokens, animates them into `overlay.preview`); the rogue-output length-guard still runs on the full text at the end (Mode A: paste once at end, not progressively). Added `import json`.
- **Item 5 — persistent dictionary**: new `dictionary.txt` (seeded, sits next to `localflow.py`), new `load_dictionary()`, new `App._reload_hotwords()` called at the start of each recording so edits apply without restart; dictionary fed to Whisper as **both** `hotwords` and `initial_prompt` on both passes. `CORRECTIONS` regex stays hardcoded (deliberately).

### Item 1 — reverted / parked
`WHISPER_MODEL` is back to `"distil-large-v3"` (the current working model). A `TODO` comment marks the intended upgrade to `distil-whisper/distil-large-v3.5-ct2`. **Why parked:** pre-downloading the new model failed with `WinError 1314` — HF Hub tries to create a symlink in its cache and Windows blocks it without Developer Mode / admin. **This same failure would hit the real app on first launch** and silently drop it to CPU `small.en`. Fix options for later: enable Windows Developer Mode, run once as admin, or set `HF_HUB_DISABLE_SYMLINKS` (exact env-var name was being verified when the session ended — confirm against the installed `huggingface_hub` version before relying on it).

---

## Verification done vs. still needed
- **Headless checks: PASS.** Script at `…\scratchpad\verify_headless.py` (this session's scratchpad). Exercised the *real* functions: dictionary seed + live reload, `foreground_is_elevated()` (reads own integrity `0x2000` = medium, so an admin `0x3000` window will correctly trigger), `wait_modifiers_released`, and `cleanup_text` streaming with a **faked** `OLLAMA_SESSION.post` (happy path, rogue-output guard, bad-JSON fallback, ≤3-word short-circuit). `py_compile` clean.
- **Still needed — the user's smoke test** (only they can, needs mic + GPU + their apps):
  1. Launch (`py -3.13 localflow.py` or `LocalFlow.bat`); watch `localflow.log` for `Whisper ready (GPU, int8_float16)`.
  2. `Ctrl+Alt+/`, dictate a sentence with a filler + a dictionary word, stop; confirm clean paste and the pill animating while polishing (item 4).
  3. Add a word to `dictionary.txt`, dictate it next — confirm recognized with no restart (item 5).
  4. Optional: focus an admin window and dictate — expect the tray warning, not silence (item 3).

---

## UI work (the new focus) — context gathered this session
- The overlay is the `Overlay(QtWidgets.QWidget)` class, **hand-painted in `paintEvent` with `QPainter`** (frameless translucent pill, bottom-center, mic-reactive bars, live transcript). That hand-drawing ceilings the polish achievable.
- Direction discussed: for a real jump, move the overlay to **QML** (same PySide6 — declarative, GPU shaders, backdrop blur, spring animations). Not a stack change.
- **Screenshot → look → adjust loop** (the way to design it without flying blind — the user's memory note warns designing blind produced ugly results): write a throwaway script that builds `Overlay`, forces a state (e.g. `state="recording"`, fake `preview` + `level`), and uses Qt's `widget.grab()` to render straight to a PNG (no recording needed) — then **Read the PNG** to actually look, tweak, re-grab. Scale coordinates by devicePixelRatio **1.25** on this machine.
- **Sounds** (user wants these changed): `sounds/` folder now exists next to the script with the 5 synthesized chimes exported as `.wav` (`ready/start/stop/done/error`). The app auto-loads `sounds/<name>.wav` as overrides; **replace a file to change that sound, delete it to fall back** to the synthesized chime. Free sources suggested: Pixabay (pixabay.com/sound-effects), Mixkit, Freesound. Export script: `…\scratchpad\export_sounds.py`.

---

## Reference artifacts (do NOT re-derive — read these)
- **Research memo (fully cited):** `docs/research/improving-localflow.md` — the source of the 5 cheap wins + medium-effort items (per-app tone #6, VAD streaming #7, command mode #8) + the §4 paste analysis and §5 architecture gaps. Item numbering throughout this handoff matches it.
- **Architecture review (HTML):** `…\scratchpad\architecture-review-20260708-194758.html` — 3 deepening candidates (Transcriber/Cleaner/Paster seams, recording state machine, cleanup guard). Not acted on; relevant if the next agent wants testability before more features.
- **Grilling decisions:** captured inline in the conversation; the resolved plan is the item list above. No ADRs/CONTEXT.md exist yet.

## Hard constraints (from user memory — verify against current code before asserting)
RTX 4050 **6 GB VRAM** (Whisper + LLM must coexist), **8 GB RAM**, Windows 11, **use `py -3.13`** (packages are on 3.13, not the 3.11 `python` resolves to), fully on-device. No `jq` on this machine. A stale Windows proxy adds ~2s/localhost request — the app already uses a `requests.Session` with `trust_env=False`.

## Suggested skills for the next session
- **`run`** — to launch LocalFlow for the smoke test and for the UI screenshot loop (it knows this project's launch patterns).
- **`verify`** — to drive items 2–5 end-to-end and observe behavior before the user commits.
- **`prototype`** — if exploring what the redesigned overlay (QML vs. improved QPainter) should look like before committing to it.
- **`domain-modeling`** — only if introducing a `CONTEXT.md`/ADR while reshaping the UI or pipeline.

## Watch-outs
- Don't stage or commit — that's the user's step.
- `dictionary.txt`, `sounds/*.wav` are new untracked files the user may or may not want committed; ask.
- The elevation check is deliberately conservative (returns `True` only on a positive higher-integrity reading) so it can't break normal pasting — keep that property if you touch it.
- `cleanup_text` streaming was tested against a *fake* session; a real Ollama stream is only confirmed by the smoke test.
