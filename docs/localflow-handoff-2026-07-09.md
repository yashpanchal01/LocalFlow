# LocalFlow — session handoff (2026-07-09)

**Repo:** `C:\Users\YASH PANCHAL\localflow` · **branch `main`** (the previous
handoff said `Backend_views`; that's stale — we are on `main`).
**App:** single-file local dictation tool (`localflow.py`). Win32 hotkey →
faster-whisper → Ollama cleanup → clipboard paste, with a hand-painted
PySide6/Qt overlay.

> **Read these first — do NOT re-derive:**
> - `docs/localflow-handoff.md` — the *previous* session's handoff (now
>   committed). Background on items 1–5, the pipeline, hard constraints.
>   Item numbering below matches it and the research memo.
> - `docs/research/improving-localflow.md` — research memo (5 cheap wins +
>   medium items #6–8, §4 paste analysis, §5 architecture gaps).

---

## What happened this session (the delta)

1. **Items 2–5 smoke-tested, verified, and COMMITTED by the user** as
   `0e6d71f "Update LocalFlow files and documentation"`. That commit also
   brought `dictionary.txt`, `sounds/*.wav`, and `docs/localflow-handoff.md`
   under version control. Commit policy held: **the user commits; the agent
   stages nothing.** Keep honoring this.
2. **Overlay redesigned → "Aurora · Mono".** DONE and render-verified, but
   **UNCOMMITTED** (working tree only). This is the next thing for the user
   to review + commit.
3. **Accuracy problem surfaced (this is "Path B") — deferred, not started.**
   The user explicitly parked it for "the end." It is now the headline open
   task.

---

## Exact git state right now

- **HEAD = `0e6d71f`** contains items 2–5 (verified `beam_size=5` present) +
  the seeded dictionary + sounds.
- **Uncommitted, working tree only:** `localflow.py` — the **Aurora · Mono**
  overlay rewrite (~93 insertions / 53 deletions vs HEAD; the `Overlay` class
  only). Also `localflow.log` (runtime noise) and the `.pyc`.
- **Untracked:** `prototype_overlay.py` (+ its `.pyc`) — the throwaway UI
  prototype. See "Watch-outs" for cleanup.
- Aurora Mono is confirmed **NOT** in HEAD (`git show HEAD:localflow.py |
  grep Aurora` → 0).

---

## Item 2–5 verification (done this session — reference, don't repeat)

**Verdict: PASS on the mechanics of all of items 2–5.** How it was reached:
- **Live run** (real mic, GPU, Ollama): every dictation ran record →
  transcribe → stream-cleanup → paste with no crashes and **no `Ollama
  cleanup failed`** even with Whisper + LLM sharing 6 GB VRAM.
- **`…/scratchpad/repro_ollama_stream.py`** — hit real Ollama `/api/chat`
  streaming; 13/13 lines parsed clean. Cleared item 4's streaming code. (The
  historical `21:05:17` single-quote JSON error was a non-reproducible
  one-off, most likely a truncated chunk under VRAM pressure.)
- **`…/scratchpad/bg_verify_A.py`** — exercised the app's *real* functions:
  - Item 5 dictionary live-reload: append a word → `load_dictionary()` picks
    it up with no restart; `dictionary.txt` restored byte-for-byte. PASS.
  - Item 3 elevation detection: own integrity = MEDIUM (0x2000); of 322
    processes, **18 read as HIGH (0x3000)** → the cross-integrity read works,
    so `foreground_is_elevated()` *can* return True and the tray warning *can*
    fire. 116 at our integrity → no false positives. ⚠️ 160 read as `None`
    (SYSTEM/protected, unreadable) → if one of those is ever focused the
    warning won't fire and paste is silently attempted (pre-existing
    conservative fallback, not a regression).

### Residual Path A checks that still need the user (low-risk formalities)
Only these need a human at the mic / an admin window; never blocking:
1. **Item 4 visual** — confirm the pill *animates* the polished text
   word-by-word (log proves cleanup ran; only the eye confirms the animation).
2. **Item 5 e2e** — add a word to `dictionary.txt`, dictate it, see it in the
   transcript.
3. **Item 3 e2e** — focus an elevated window, dictate, confirm the tray
   balloon appears and text is left on the clipboard.

---

## Aurora · Mono overlay — what changed and how it was verified

- **Where:** `localflow.py`, the `Overlay` class only (constants, `_animate`,
  new `_pill_h`, `_place`, `paintEvent`). Comment at the class top records the
  decision + date + source prototype.
- **Look:** near-black glass pill (`344×46`), painted 3-layer soft shadow +
  top rim-light (window now carries a 26 px transparent `PAD` margin so the
  shadow has room), **greyscale voice bars (no glow)**, and a small blinking
  **state dot** — red ● listening / amber ● polishing — as the mode signal.
  Two-line transcript tail with the older line dimmed.
- **Verified:** `py_compile` clean; rendered the *real* production `Overlay`
  (not the prototype) to PNGs at the machine's true dpr 1.25 via
  **`…/scratchpad/render_real_overlay.py`** → `…/scratchpad/real_overlay_shots/`.
  All three states matched the chosen prototype. Not yet seen in the live app
  with the fade-in over real windows (see "Running services").

### UI prototype (reference)
`prototype_overlay.py` holds 7 switchable variants. The **Aurora family**
(Ember/Arctic/Mono/Prism) plus **Ribbon HUD**, **Orb Island**, **Terminal
Ticker**. Run live: `py -3.13 prototype_overlay.py` (←/→ variant, Space phase,
Esc quit). Re-shoot stills: `py -3.13 prototype_overlay.py --shoot` →
`…/scratchpad/proto_shots/`. **Winner: Aurora · Mono.** The screenshot→look→
adjust loop (grab() → PNG → Read the PNG → tweak) is the way to iterate the
overlay without flying blind; the render harness above is that loop applied to
the real widget.

---

## Next session focus — Path B: transcription accuracy

The problem the user actually cares about. Observed in the live run: Whisper
mis-hears proper nouns even with the dictionary fed as `hotwords` +
`initial_prompt` — "Claude Code" → `broadcourt`/`claw code`/`Claudecote`,
"Ollama" → `Lama`, "test" → `taste`. **Not a code bug** — item 5 is wired
correctly (verified). Two root causes and two levers:
1. **Hotword biasing is too weak** for these acoustics.
2. **`CORRECTIONS` regex is too narrow** — its alternations don't match
   `broadcourt`, `claw code`, or `Lama`. **Cheapest deterministic lever:**
   broaden `CORRECTIONS` (no VRAM cost). Note the previous handoff said the
   regex is "deliberately hardcoded" — confirm intent before widening.
3. **Heavier lever:** the **parked item-1 model upgrade** to
   `distil-whisper/distil-large-v3.5-ct2` ("lower WER, same VRAM"), which
   directly targets this. Still parked over the Windows HF-cache symlink issue
   (`WinError 1314`; needs Developer Mode / run-once-as-admin /
   `HF_HUB_DISABLE_SYMLINKS` — verify the exact env-var name). See the `TODO`
   at `localflow.py:35` and research memo §2a.

Recommended order: broaden `CORRECTIONS` first (fast win), then decide on the
model upgrade.

---

## Running services (as of handoff)

- **Ollama: UP** (PID 28860 on `127.0.0.1:11434`) — started via
  `ollama serve` in a background task this session.
- **LocalFlow: NOT running** (port 52739 free) — the instance launched this
  session has been closed. Relaunch with `py -3.13 localflow.py`; watch
  `localflow.log` for `Whisper ready (GPU, int8_float16)`. **Caveat:** the log
  is append-only and spans several runs — match on the *newest* timestamps
  (the clock has rolled past midnight into `01:xx`), or you'll read a stale
  `LocalFlow ready` from an earlier run (this bit me once this session).

---

## Hard constraints (from user memory / prior handoff — verify before asserting)

RTX 4050 **6 GB VRAM** (Whisper + LLM must coexist), **8 GB RAM**, Windows 11.
**Use `py -3.13`** (packages are on 3.13, not the 3.11 that `python`
resolves to). Fully on-device. No `jq`. A stale Windows proxy adds ~2 s per
localhost request — the app uses a `requests.Session` with `trust_env=False`;
mirror that in any repro script.

---

## Watch-outs

- **Don't stage or commit** — the user does that. Aurora Mono is theirs to
  review + commit; note it stacks cleanly on `0e6d71f`.
- **`prototype_overlay.py` is throwaway + untracked.** The user hasn't decided
  whether to delete it or keep it as a UI-iteration tool — **ask before
  deleting**, don't let it get committed by accident.
- **Elevation check is deliberately conservative** (returns True only on a
  positive higher-integrity reading) so it can't break normal pasting — keep
  that property if you touch item 3.
- **`CORRECTIONS` was described as intentionally hardcoded** — confirm intent
  before broadening it for Path B.
- The scratchpad harnesses (`repro_ollama_stream.py`, `bg_verify_A.py`,
  `render_real_overlay.py`) import `localflow` safely (model load + app start
  are behind `if __name__ == "__main__"`). Reuse them; they add project dir to
  `sys.path` explicitly.

---

## Suggested skills for the next session

- **`verify`** — to sign off the residual Path A voice checks and to verify any
  Path B accuracy change end-to-end (drive real dictations, read the log).
- **`run`** — to relaunch LocalFlow for the live overlay look and the accuracy
  testing loop.
- **`prototype`** — only if exploring further overlay tweaks; `prototype_overlay.py`
  and the render harness are already set up for the screenshot→look loop.
- **`research`** — if pursuing the parked model upgrade, to nail down the
  `HF_HUB_DISABLE_SYMLINKS` env var / symlink workaround against primary docs
  before touching `WHISPER_MODEL`.
